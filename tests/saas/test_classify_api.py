"""Tests for the ad-hoc classification API (/api/v1/classify) + read-through cache.

The LLM (GeminiClassificationService) is replaced via FastAPI dependency override
with a fake that counts calls, so we can assert the cache prevents re-classification
without hitting the real API. Auth uses a real committed API key; cache + tenant
rows are cleaned up in finally.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text

from src.saas.application.classification.dtos import ClassificationResult
from src.saas.infrastructure.auth.security import generate_api_key
from src.saas.infrastructure.persistence.models.tenant import ApiKey, Tenant
from src.saas.presentation.api.app import create_app
from src.saas.presentation.api.routes.classify import (
    _group_code,
    _normalize,
    get_classifier,
)


class _FakeClassifier:
    def __init__(self, result: ClassificationResult) -> None:
        self._result = result
        self.calls = 0

    def classify_provider_batch(self, provider_code, vehicles):
        self.calls += 1
        return [self._result]


_EDMR = ClassificationResult(
    acriss_category="E", acriss_body_type="D", acriss_transmission="M",
    acriss_fuel="R", confidence=0.95, pending_review=False,
)
_NULL = ClassificationResult(
    acriss_category=None, acriss_body_type=None, acriss_transmission=None,
    acriss_fuel=None, confidence=0.4, pending_review=True,
)


def _app_with(fake: _FakeClassifier):
    app = create_app()
    app.dependency_overrides[get_classifier] = lambda: fake
    return app


def test_missing_api_key_returns_401():
    client = TestClient(create_app())
    assert client.get("/api/v1/classify?model=x").status_code == 401


def test_group_code_wildcards_fuel():
    """Commercial group: fuel carries little weight → wildcarded."""
    assert _group_code("IGAV") == "IGA*"
    assert _group_code("IGAR") == "IGA*"
    assert _group_code("RGAH") == "RGA*"
    assert _group_code("CDAD") == "CDA*"


def test_group_code_bev_exception():
    """Electric keeps its fourth letter — its own commercial category."""
    assert _group_code("IGAE") == "IGAE"
    assert _group_code("MDAC") == "MDAC"


def test_group_code_absent_input():
    assert _group_code(None) is None
    assert _group_code("ED") is None


def test_empty_model_returns_400(super_db_session):
    ids, raw = _seed_tenant_key(super_db_session)
    try:
        client = TestClient(_app_with(_FakeClassifier(_EDMR)))
        r = client.get("/api/v1/classify", headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 400
    finally:
        _cleanup(super_db_session, ids, models=[])


def test_classify_miss_then_hit(super_db_session):
    ids, raw = _seed_tenant_key(super_db_session)
    fake = _FakeClassifier(_EDMR)
    model = "Peugeot 208 Manual Test"
    try:
        client = TestClient(_app_with(fake))
        h = {"Authorization": f"Bearer {raw}"}

        r1 = client.get("/api/v1/classify", params={"model": model}, headers=h)
        assert r1.status_code == 200
        body = r1.json()
        assert body["acriss_code"] == "EDMR"
        assert body["description"]                 # display_name from catalog
        assert isinstance(body["example_models"], list) and body["example_models"]
        assert body["cached"] is False
        assert fake.calls == 1

        r2 = client.get("/api/v1/classify", params={"model": model}, headers=h)
        assert r2.json()["cached"] is True
        assert r2.json()["acriss_code"] == "EDMR"
        assert fake.calls == 1                      # served from cache, no new LLM call
    finally:
        _cleanup(super_db_session, ids, models=[_normalize(model)])


def test_unclassifiable_returns_null(super_db_session):
    ids, raw = _seed_tenant_key(super_db_session)
    fake = _FakeClassifier(_NULL)
    model = "Totally Unknown Vehicle XYZ Test"
    try:
        client = TestClient(_app_with(fake))
        r = client.get(
            "/api/v1/classify", params={"model": model},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["acriss_code"] is None
        assert body["pending_review"] is True
        assert body["example_models"] == []
    finally:
        _cleanup(super_db_session, ids, models=[_normalize(model)])


_CDMD = ClassificationResult(
    acriss_category="C", acriss_body_type="D", acriss_transmission="M",
    acriss_fuel="D", confidence=0.95, pending_review=False,
)
_CDAH = ClassificationResult(
    acriss_category="C", acriss_body_type="D", acriss_transmission="A",
    acriss_fuel="H", confidence=0.9, pending_review=False,
)


def test_specific_fuel_without_market_recommends_trunk(super_db_session):
    """Diesel is materialized but no active market group carries it → the
    recommendation falls back to the R trunk; acriss_full keeps the truth.
    (Assumes the dev market has no diesel-specific groups — true today.)"""
    ids, raw = _seed_tenant_key(super_db_session)
    fake = _FakeClassifier(_CDMD)
    model = "Opel Astra Manual Diesel Trunk Test"
    try:
        client = TestClient(_app_with(fake))
        h = {"Authorization": f"Bearer {raw}"}

        r1 = client.get("/api/v1/classify", params={"model": model}, headers=h)
        body = r1.json()
        assert body["acriss_code"] == "CDMR"
        assert body["acriss_full"] == "CDMD"
        assert body["acriss_group"] == "CDM*"

        # The adjustment is live — a cache hit gets it too.
        r2 = client.get("/api/v1/classify", params={"model": model}, headers=h)
        assert r2.json()["cached"] is True
        assert r2.json()["acriss_code"] == "CDMR"
    finally:
        _cleanup(super_db_session, ids, models=[_normalize(model)])


def test_specific_fuel_with_market_is_kept(super_db_session):
    """Where the market DOES separate the fuel (a hybrid group exists), the
    specific code stays the recommendation."""
    ids, raw = _seed_tenant_key(super_db_session)
    code = f"clsfy{__import__('uuid').uuid4().hex[:8]}"
    pid = super_db_session.execute(
        text("INSERT INTO providers (code, display_name, status, scraper_key, default_currency) "
             "VALUES (:c, 'Classify Test Prov', 'active', :c, 'EUR') RETURNING id"),
        {"c": code},
    ).scalar()
    lid = super_db_session.execute(
        text("INSERT INTO provider_locations (provider_id, location_code, location_name, active) "
             "VALUES (:p, 'CLSLOC', 'Loc', TRUE) RETURNING id"), {"p": pid},
    ).scalar()
    rid = super_db_session.execute(
        text("INSERT INTO provider_rates (provider_id, rate_code, rate_name, active) "
             "VALUES (:p, 'CLSRATE', 'Rate', TRUE) RETURNING id"), {"p": pid},
    ).scalar()
    super_db_session.execute(
        text("INSERT INTO provider_vehicle_categories "
             "(provider_id, provider_location_id, provider_rate_id, external_code, "
             " example_models, acriss_category, acriss_body_type, acriss_transmission, "
             " acriss_fuel, active) "
             "VALUES (:p, :l, :r, 'CLS-H', 'Toyota Corolla Hybrid', 'C','D','A','H', TRUE)"),
        {"p": pid, "l": lid, "r": rid},
    )
    super_db_session.commit()

    fake = _FakeClassifier(_CDAH)
    model = "Corolla Hybrid Market Kept Test"
    try:
        client = TestClient(_app_with(fake))
        r = client.get(
            "/api/v1/classify", params={"model": model},
            headers={"Authorization": f"Bearer {raw}"},
        )
        body = r.json()
        assert body["acriss_code"] == "CDAH"
        assert body["acriss_group"] == "CDA*"
    finally:
        super_db_session.execute(
            text("DELETE FROM provider_vehicle_categories WHERE provider_id = :p"), {"p": pid})
        super_db_session.execute(
            text("DELETE FROM provider_rates WHERE provider_id = :p"), {"p": pid})
        super_db_session.execute(
            text("DELETE FROM provider_locations WHERE provider_id = :p"), {"p": pid})
        super_db_session.execute(
            text("DELETE FROM providers WHERE id = :p"), {"p": pid})
        super_db_session.commit()
        _cleanup(super_db_session, ids, models=[_normalize(model)])


def test_llm_transport_error_returns_502_and_is_not_cached(super_db_session):
    """An errored classification surfaces the upstream message and never caches.

    The regression this pins: a Gemini outage (e.g. 'User location is not
    supported') used to be cached as a null classification, so the model kept
    returning null from cache after the outage ended.
    """
    ids, raw = _seed_tenant_key(super_db_session)
    error_result = ClassificationResult(
        acriss_category=None, acriss_body_type=None, acriss_transmission=None,
        acriss_fuel=None, confidence=0.0, pending_review=True,
        rationale="Flash call failed: 400 FAILED_PRECONDITION",
        error="400 FAILED_PRECONDITION. User location is not supported for the API use.",
    )
    fake = _FakeClassifier(error_result)
    model = "Transport Error Vehicle Test"
    try:
        client = TestClient(_app_with(fake))
        h = {"Authorization": f"Bearer {raw}"}

        r1 = client.get("/api/v1/classify", params={"model": model}, headers=h)
        assert r1.status_code == 502
        detail = r1.json()["detail"]
        assert detail["error"] == "classification_unavailable"
        assert "User location is not supported" in detail["message"]

        # Nothing persisted for this model — the outage left no residue.
        row = super_db_session.execute(
            text("SELECT 1 FROM model_classifications WHERE normalized_model = :m"),
            {"m": _normalize(model)},
        ).fetchone()
        assert row is None

        # A later request retries the classifier instead of serving a cached null.
        r2 = client.get("/api/v1/classify", params={"model": model}, headers=h)
        assert r2.status_code == 502
        assert fake.calls == 2
    finally:
        _cleanup(super_db_session, ids, models=[_normalize(model)])


# ── helpers ──────────────────────────────────────────────────────────────────

def _seed_tenant_key(s):
    t = Tenant(name="Classify Test", currency="EUR")
    s.add(t)
    s.flush()
    raw, prefix, h = generate_api_key()
    s.add(ApiKey(tenant_id=t.id, name="k", key_prefix=prefix, key_hash=h))
    s.commit()
    return [t.id], raw


def _cleanup(s, tenant_ids, models):
    if models:
        s.execute(
            text("DELETE FROM model_classifications WHERE normalized_model = ANY(:m)"),
            {"m": models},
        )
    s.execute(text("DELETE FROM api_keys WHERE tenant_id = ANY(:t)"), {"t": tenant_ids})
    s.execute(text("DELETE FROM tenants WHERE id = ANY(:t)"), {"t": tenant_ids})
    s.commit()
