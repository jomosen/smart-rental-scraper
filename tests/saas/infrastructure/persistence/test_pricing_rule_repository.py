"""Integration tests for PricingRuleRepository.

Requires: Postgres running, migrations applied, SUPER_DATABASE_URL set.
All changes are rolled back after each test (super_db_session fixture).

Covers:
  - Create first rule (version = 1, active = True)
  - Save second rule: supersedes first, gets version = 2
  - Tenant isolation: tenant B cannot see tenant A's rules
"""
from __future__ import annotations

import uuid

import pytest

from src.saas.infrastructure.persistence.models.tenant import PricingRule, Tenant
from src.saas.infrastructure.persistence.repositories import PricingRuleRepository


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_tenant(session, name: str = "Test Tenant") -> Tenant:
    t = Tenant(name=name, currency="EUR", plan="mvp")
    session.add(t)
    session.flush()
    return t


def _formula(label: str = "v1") -> dict:
    return {
        "providers": ["centauro", "solcar"],
        "base_aggregation": "min",
        "master_provider": "centauro",
        "rounding": "0",
        "global_rule": {"op": "sub", "val": 1.0, "mode": "pct", "floor": "auto", "ceiling": "max"},
        "category_overrides": {},
        "_label": label,  # to distinguish versions in assertions
    }


# ── Tests ────────────────────────────────────────────────────────────────────

class TestPricingRuleRepository:
    def test_get_active_returns_none_for_new_tenant(self, super_db_session):
        tenant = _make_tenant(super_db_session)
        repo = PricingRuleRepository(super_db_session)
        assert repo.get_active(tenant.id) is None

    def test_save_creates_version_1(self, super_db_session):
        tenant = _make_tenant(super_db_session)
        repo = PricingRuleRepository(super_db_session)

        rule = repo.save(tenant.id, "Tarifa cruzada", _formula("v1"))

        assert rule.version == 1
        assert rule.active is True
        assert rule.acriss_code is None
        assert rule.formula_jsonb["_label"] == "v1"
        assert rule.superseded_by_id is None
        assert rule.superseded_at is None

    def test_save_second_supersedes_first(self, super_db_session):
        tenant = _make_tenant(super_db_session)
        repo = PricingRuleRepository(super_db_session)

        rule1 = repo.save(tenant.id, "Tarifa cruzada", _formula("v1"))
        rule2 = repo.save(tenant.id, "Tarifa cruzada", _formula("v2"))

        # Refresh rule1 from DB (the session has the dirty state)
        super_db_session.expire(rule1)
        super_db_session.refresh(rule1)

        assert rule2.version == 2
        assert rule2.active is True
        assert rule1.active is False
        assert rule1.superseded_by_id == rule2.id
        assert rule1.superseded_at is not None

    def test_get_active_returns_latest(self, super_db_session):
        tenant = _make_tenant(super_db_session)
        repo = PricingRuleRepository(super_db_session)

        repo.save(tenant.id, "Rule", _formula("v1"))
        repo.save(tenant.id, "Rule", _formula("v2"))
        repo.save(tenant.id, "Rule", _formula("v3"))

        active = repo.get_active(tenant.id)
        assert active is not None
        assert active.version == 3
        assert active.formula_jsonb["_label"] == "v3"

    def test_get_history_ordered_newest_first(self, super_db_session):
        tenant = _make_tenant(super_db_session)
        repo = PricingRuleRepository(super_db_session)

        repo.save(tenant.id, "Rule", _formula("v1"))
        repo.save(tenant.id, "Rule", _formula("v2"))
        repo.save(tenant.id, "Rule", _formula("v3"))

        history = repo.get_history(tenant.id)
        assert len(history) == 3
        assert history[0].version == 3
        assert history[1].version == 2
        assert history[2].version == 1

    def test_acriss_code_nullable(self, super_db_session):
        """Global config rules have acriss_code = NULL."""
        tenant = _make_tenant(super_db_session)
        repo = PricingRuleRepository(super_db_session)

        rule = repo.save(tenant.id, "Global config", _formula())
        assert rule.acriss_code is None

    def test_tenant_isolation(self, super_db_session):
        """Tenant B cannot see tenant A's rules."""
        tenant_a = _make_tenant(super_db_session, "Tenant A")
        tenant_b = _make_tenant(super_db_session, "Tenant B")
        repo = PricingRuleRepository(super_db_session)

        repo.save(tenant_a.id, "Rule A", _formula("a"))

        assert repo.get_active(tenant_b.id) is None
        assert repo.get_history(tenant_b.id) == []
