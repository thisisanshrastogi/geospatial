"""Composition root: builds and wires all objects via FastAPI Depends.

Constructor injection everywhere; ONLY this module knows concrete classes.
Route handlers depend on services/ports defined here.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from fastapi import BackgroundTasks, Depends

from app.config import Settings, get_settings
from app.domain.ports import (
    FeatureRepository,
    FileRepository,
    FileStorage,
    TaskDispatcher,
)
from app.geo.measurers import MeasurerRegistry
from app.geo.projection import Projector
from app.geo.readers import ReaderRegistry
from app.infrastructure.db import init_engine
from app.infrastructure.repositories import SqlFeatureRepository, SqlFileRepository
from app.infrastructure.storage import LocalFileStorage
from app.infrastructure.tasks import BackgroundTaskDispatcher
from app.services.processing_service import ProcessingService
from app.services.query_service import QueryService
from app.services.upload_service import UploadService


@lru_cache
def get_settings_cached() -> Settings:
    """Cached Settings instance for the process lifetime."""
    return get_settings()


def get_db_engine(settings: Settings = Depends(get_settings_cached)) -> None:
    """Initialize database engine on first request (idempotent)."""
    init_engine(settings)


def get_file_repo() -> Iterator[FileRepository]:
    """FileRepository per request (creates own session)."""
    from app.infrastructure.db import get_session

    session = get_session()
    try:
        yield SqlFileRepository(session)
    finally:
        session.close()


def get_feature_repo() -> Iterator[FeatureRepository]:
    """FeatureRepository per request."""
    from app.infrastructure.db import get_session

    session = get_session()
    try:
        yield SqlFeatureRepository(session)
    finally:
        session.close()


def get_storage(settings: Settings = Depends(get_settings_cached)) -> FileStorage:
    """FileStorage singleton (stateless)."""
    return LocalFileStorage(Path(settings.upload_dir))


def get_task_dispatcher(
    background: BackgroundTasks,
) -> TaskDispatcher:
    """BackgroundTaskDispatcher per request (wraps request's BackgroundTasks)."""
    return BackgroundTaskDispatcher(background)


# Singleton registries (stateless, created once)
@lru_cache
def get_reader_registry() -> ReaderRegistry:
    from app.geo.readers import KmlReader, ShapefileZipReader

    return ReaderRegistry([ShapefileZipReader(), KmlReader()])


@lru_cache
def get_measurer_registry() -> MeasurerRegistry:
    return MeasurerRegistry()


@lru_cache
def get_projector() -> Projector:
    return Projector()


def get_upload_service(
    storage: FileStorage = Depends(get_storage),
    file_repo: FileRepository = Depends(get_file_repo),
    task_dispatcher: TaskDispatcher = Depends(get_task_dispatcher),
    settings: Settings = Depends(get_settings_cached),
) -> UploadService:
    """UploadService per request."""
    return UploadService(
        storage,
        file_repo,
        task_dispatcher,
        settings,
        ProcessingService.create_background_job,
    )


def get_query_service(
    file_repo: FileRepository = Depends(get_file_repo),
    feature_repo: FeatureRepository = Depends(get_feature_repo),
) -> QueryService:
    """QueryService per request."""
    return QueryService(file_repo, feature_repo)