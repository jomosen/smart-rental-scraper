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
from src.saas.presentation.api.routes.classify import _normalize, get_classifier


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
