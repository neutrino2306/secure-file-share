"""Application entry point for the Secure File Sharing Service.

Run with the app factory:

    uvicorn app.main:create_app --factory --port 8000
"""

import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import db
from app.config import Settings
from app.console import load_console_page
from app.errors import error_body, register_error_handlers
from app.log_redaction import install_access_log_redaction
from app.routes import download, files, links
from app.storage import BlobStorage
from app.timeutil import Clock

logger = logging.getLogger(__name__)

# Allowance for multipart boundaries and part headers on top of the file size.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
UPLOAD_PATH = "/v1/files"


def _configure_logging() -> None:
    # uvicorn only configures its own loggers; give application loggers a handler too.
    # basicConfig is a no-op if the root logger is already configured (e.g. by pytest).
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(settings: Settings | None = None, clock: Clock = time.time) -> FastAPI:
    """Build the application.

    settings: when omitted, loaded from environment variables / .env and validated;
              invalid configuration raises and the process refuses to start.
    clock:    returns the current unix time in seconds; injectable for tests.
    """
    settings = settings or Settings()
    _configure_logging()

    # Initialize persistent state eagerly so startup fails fast on a bad DATA_DIR.
    storage = BlobStorage(settings.data_dir)
    db.init_schema(settings.db_path)
    install_access_log_redaction()

    app = FastAPI(
        title="Secure File Sharing Service",
        version="1.0.0",
        description=(
            "Upload private files and share them through temporary, "
            "HMAC-signed download links. Authenticate with the X-API-Key header."
        ),
    )
    app.state.settings = settings
    app.state.storage = storage
    app.state.clock = clock

    register_error_handlers(app)

    @app.middleware("http")
    async def reject_oversized_uploads(request: Request, call_next):
        """Reject obviously oversized uploads before the body is read.

        This is a fast path based on Content-Length. The authoritative limit is
        enforced while streaming the file to disk (the header can be absent).
        """
        if request.method == "POST" and request.url.path == UPLOAD_PATH:
            declared = request.headers.get("content-length")
            limit = settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES
            if declared and declared.isdigit() and int(declared) > limit:
                return JSONResponse(
                    status_code=413,
                    content=error_body(
                        "file_too_large",
                        f"File exceeds the maximum size of {settings.max_upload_bytes} bytes",
                    ),
                )
        return await call_next(request)

    app.include_router(files.router)
    app.include_router(links.router)
    app.include_router(download.router)

    # Loaded once at startup; a missing or malformed page fails fast.
    console = load_console_page()

    @app.get("/", include_in_schema=False)
    def web_console() -> HTMLResponse:
        """Browser console for the API (vanilla JS, same-origin only, strict CSP)."""
        return HTMLResponse(console.html, headers=console.headers)

    @app.get("/healthz", tags=["health"], summary="Liveness probe")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    logger.info("Service started (data_dir=%s)", settings.data_dir)
    return app
