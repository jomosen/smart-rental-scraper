from __future__ import annotations

import datetime
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.tenant import PricingRule


class PricingRuleRepository:
    """Append-only versioned store for cross-provider pricing configurations.

    Each call to save() creates a new PricingRule row with version N+1 and
    supersedes the previous active row for that tenant.  No UPDATE in place.
    """

    def __init__(self, session: Session) -> None:
        self._s = session

    def get_active(self, tenant_id: uuid.UUID) -> Optional[PricingRule]:
        """Return the current active rule for a tenant, or None."""
        return self._s.scalar(
            select(PricingRule)
            .where(
                PricingRule.tenant_id == tenant_id,
                PricingRule.active.is_(True),
            )
            .order_by(PricingRule.version.desc())
            .limit(1)
        )

    def get_history(self, tenant_id: uuid.UUID) -> list[PricingRule]:
        """Return all rules for a tenant ordered newest-first."""
        return list(
            self._s.scalars(
                select(PricingRule)
                .where(PricingRule.tenant_id == tenant_id)
                .order_by(PricingRule.version.desc())
            ).all()
        )

    def save(
        self,
        tenant_id: uuid.UUID,
        name: str,
        formula_jsonb: dict,
        acriss_code: Optional[str] = None,
    ) -> PricingRule:
        """Persist a new version of the pricing configuration.

        Supersedes the previous active rule (if any) and returns the new row.
        Caller is responsible for commit/rollback.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        previous = self.get_active(tenant_id)
        new_version = (previous.version + 1) if previous is not None else 1

        new_rule = PricingRule(
            tenant_id=tenant_id,
            acriss_code=acriss_code,
            name=name,
            version=new_version,
            formula_jsonb=formula_jsonb,
            condition_jsonb={},
            active=True,
        )
        self._s.add(new_rule)
        self._s.flush()  # assigns new_rule.id before we reference it

        if previous is not None:
            previous.active = False
            previous.superseded_at = now
            previous.superseded_by_id = new_rule.id
            self._s.flush()

        return new_rule
