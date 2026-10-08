"""
The application.

Multi-tenant by construction: nothing here holds a customer's credentials, and
every request that touches customer data resolves a tenant first. See
`app/api/deps.py` for how that resolution works and `app/repositories/tenants.py`
for where the credentials live.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.router import api_router
from .api.routes.health import router as health_router
from .config import settings
from .db.supabase import supabase
from .errors import install_error_handlers
from .security.secret_box import encryption_configured
from .services import drive
from .services.audio import ffmpeg_available

log = logging.getLogger("calling-agent")


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # httpx logs every request at INFO, which buries our own lines under a
    # transcript of the Graph API.
    logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()

    # Said once, at boot, rather than discovered on the first call that needed
    # it. Each of these degrades quietly, and quiet degradation is only
    # acceptable if somebody was told.
    if getattr(supabase, "is_local", False):
        log.warning(
            "SUPABASE_URL / SUPABASE_SERVICE_KEY are not set — using the local JSON "
            "store at data/local-store.json. Fine on a laptop; on a host with a "
            "replaceable filesystem every deploy erases it."
        )
    if not encryption_configured():
        log.warning(
            "CREDENTIALS_SECRET is not set — tenant credentials will be stored in "
            "plain text. Set it before onboarding real customers."
        )
    if not ffmpeg_available():
        log.warning(
            "ffmpeg was not found — recordings are stored exactly as they arrive, "
            "which means larger files and no seeking in the player."
        )
    if not settings.google_oauth_configured:
        log.info("Google OAuth is not configured; companies cannot connect Drive.")
    log.info("calling-agent api ready (%s)", settings.app_env)

    try:
        yield
    finally:
        # A call still running when the process goes away would otherwise
        # leave a row IN_PROGRESS forever and its recording unwritten.
        from .services.agent import live

        await live.end_all("the service is restarting")
        await supabase.close()
        await drive.close()


app = FastAPI(
    title="Calling Agent API",
    version="1.0.0",
    summary="Multi-tenant voice and WhatsApp agents: calls, recordings and messaging.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Credentials are sent as a bearer token, not a cookie, so the browser does
    # not need credentialed CORS — and leaving it off means the origin list is
    # allowed to be specific without breaking preflight.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

install_error_handlers(app)
app.include_router(api_router)
# Also at the root, for a load balancer that does not know about /api.
app.include_router(health_router)


def run() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=not settings.is_production,
    )


if __name__ == "__main__":
    run()
