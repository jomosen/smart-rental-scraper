"""FastAPI application factory for RentRadar API.

Mount order is critical for the SPA pattern:
  1. Explicit /api/* routes (registered first, highest router priority).
  2. /api/{path:path} catch-all  →  404 JSON for any undefined API path.
     This MUST sit before the SPA fallback: without it, /api/<undefined>
     would match /{full_path:path} and return index.html, causing confusing
     JSON-parse errors in the front-end.
  3. /{full_path:path} catch-all  →  serves dist/ or index.html (SPA).

Verification:
  GET /api/health          → 200 {"status": "ok", "service": "saas-api"}
  GET /api/doesnotexist   → 404 {"detail": {"error": "API endpoint not found"}}
  GET /                   → 200 index.html (if built) or JSON dev hint

Start with:
  uvicorn src.saas.presentation.api.app:app --reload --port 8000
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

# Load .env so DB URLs and DEV_TENANT_ID are available when uvicorn imports the app.
load_dotenv()

# Absolute path to the SPA build artefacts from `npm run build`.
# Both this file and vite.config.ts agree on this location.
_DIST = Path(__file__).parent.parent / "web" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="RentRadar API", docs_url=None, redoc_url=None)

    # ── API endpoints ─────────────────────────────────────────────────────────

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "saas-api"}

    # Data + auth routers — registered before the /api/* catch-all and SPA fallback.
    from .routes import cross_tariff as cross_tariff_routes
    from .routes import auth as auth_routes
    from .routes import public_prices as public_prices_routes
    from .routes import classify as classify_routes
    from .routes import catalog as catalog_routes
    app.include_router(cross_tariff_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(public_prices_routes.router)
    app.include_router(classify_routes.router)
    app.include_router(catalog_routes.router)

    # Explicit /api/* 404 handler.
    # Registered BEFORE the SPA catch-all so undefined /api/<anything>
    # always returns 404 JSON, never the SPA index.html.
    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    )
    async def api_not_found(path: str):
        raise HTTPException(
            status_code=404,
            detail={"error": "API endpoint not found"},
        )

    # ── SPA static serving ────────────────────────────────────────────────────

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if not _DIST.is_dir():
            return {
                "detail": (
                    "Front-end not built. "
                    "Run: cd src/saas/presentation/web && npm run build"
                )
            }
        # Serve the exact file (hashed JS/CSS bundles, favicon, etc.),
        # or fall back to index.html for any SPA client-side route.
        candidate = _DIST / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        index = _DIST / "index.html"
        if index.is_file():
            return FileResponse(str(index))
        raise HTTPException(status_code=503, detail="Front-end build incomplete")

    return app


app = create_app()
