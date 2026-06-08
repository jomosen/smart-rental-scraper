"""Transactional link-email delivery.

RETAINED, currently unused by the login flow (login is email + password — see
docs/DATA_MODEL.md §"Authentication: email + password"). Kept as the substrate
for a future "password reset by email" flow.

Production: Resend transactional API (RESEND_API_KEY). Dev: when no API key is
configured, the full link is logged to the server console instead of being sent.
Delivery failures are logged and swallowed so the caller can keep a constant
response (no enumeration, no leak of whether the email exists or sending worked).
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"


def send_magic_link(email: str, link: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        logger.warning(
            "[DEV] No RESEND_API_KEY set — magic link for %s NOT emailed. Open it manually:\n  %s",
            email, link,
        )
        return

    sender = os.environ.get("RESEND_FROM", "Acceso <login@example.com>")
    try:
        resp = httpx.post(
            _RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": sender,
                "to": [email],
                "subject": "Tu enlace de acceso",
                "html": (
                    "<p>Hola,</p>"
                    "<p>Pulsa el botón para acceder a tu panel de precios. "
                    "El enlace caduca en 15 minutos y solo puede usarse una vez.</p>"
                    f'<p><a href="{link}">Acceder a tu panel</a></p>'
                    f"<p>O copia esta URL: {link}</p>"
                ),
            },
            timeout=10.0,
        )
        resp.raise_for_status()
    except Exception as exc:  # never surface delivery details to the caller
        logger.error("Failed to send magic link to %s: %s", email, exc)
