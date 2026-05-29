from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.catalog import Provider


class ProviderRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get_by_code(self, code: str) -> Optional[Provider]:
        return self._s.scalar(select(Provider).where(Provider.code == code))

    def get_or_create(
        self,
        code: str,
        display_name: str,
        scraper_key: str,
        currency: str = "EUR",
        base_url: str = "",
    ) -> Provider:
        """Get existing provider by code or create it. Updates display_name/base_url if changed."""
        provider = self.get_by_code(code)
        if provider is None:
            provider = Provider(
                code=code,
                display_name=display_name,
                scraper_key=scraper_key,
                default_currency=currency,
                base_url=base_url,
                status="active",
            )
            self._s.add(provider)
            self._s.flush()
        else:
            dirty = False
            if provider.display_name != display_name:
                provider.display_name = display_name
                dirty = True
            if base_url and provider.base_url != base_url:
                provider.base_url = base_url
                dirty = True
            if dirty:
                self._s.flush()
        return provider

    def list_active(self) -> list[Provider]:
        return list(
            self._s.scalars(
                select(Provider).where(Provider.status == "active").order_by(Provider.code)
            )
        )
