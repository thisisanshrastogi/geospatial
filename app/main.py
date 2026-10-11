"""App factory, logging, exception handlers, and startup recovery.

- Creates tables on startup.
- Recovers files stuck in PROCESSING/PENDING as FAILED.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import Settings, get_settings
from app.domain import errors
from app.domain.ports import FileRepository
from app.domain.status import FileStatus
from app.infrastructure.db import get_session, init_engine
from app.infrastructure.repositories import SqlFileRepository


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(levelname)s %(name)s: %(message)s")


def _error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application (composition root for app-level concerns)."""
    settings = settings or get_settings()
    _configure_logging(settings.log_level)

    app = FastAPI(title="Geospatial File Measurement API", version="0.1.0")

    # Initialize database engine
    init_engine(settings)

    # Startup: recover stuck files
    @app.on_event("startup")
    def startup_recovery() -> None:
        with get_session() as session:
            file_repo: FileRepository = SqlFileRepository(session)
            for status in (FileStatus.PENDING, FileStatus.PROCESSING):
                stuck_files = file_repo.list_by_status(status)
                for geo_file in stuck_files:
                    geo_file.mark_failed("Interrupted by server restart")
                    file_repo.save(geo_file)
                if stuck_files:
                    logging.warning(
                        "Recovered %d files stuck in %s status",
                        len(stuck_files),
                        status.value,
                    )

    app.include_router(router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Domain -> HTTP mapping
    @app.exception_handler(errors.DomainError)
    def _domain_error(request: Request, exc: errors.DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.code, str(exc)),
        )

    return app


app = create_app()
