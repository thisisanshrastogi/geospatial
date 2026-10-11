"""SQLAlchemy repositories + ORM <-> domain mapping."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import FeatureRecord, GeoFile, Measurement, Page
from app.domain.ports import FeatureRepository, FileRepository
from app.domain.status import FileStatus
from app.infrastructure.orm import FeatureORM, FileORM


def _orm_to_geo_file(orm: FileORM) -> GeoFile:
    """Convert FileORM to GeoFile domain object."""
    return GeoFile(
        id=orm.id,  # type: ignore[arg-type]
        filename=orm.filename,  # type: ignore[arg-type]
        file_type=orm.file_type,  # type: ignore[arg-type]
        stored_path=orm.stored_path,  # type: ignore[arg-type]
        status=FileStatus(orm.status),  # type: ignore[arg-type]
        crs=orm.crs,  # type: ignore[arg-type]
        feature_count=orm.feature_count,  # type: ignore[arg-type]
        error_message=orm.error_message,  # type: ignore[arg-type]
        created_at=orm.created_at,  # type: ignore[arg-type]
        completed_at=orm.completed_at,  # type: ignore[arg-type]
    )


def _geo_file_to_orm(geo: GeoFile) -> FileORM:
    """Convert GeoFile domain object to FileORM."""
    return FileORM(
        id=geo.id,
        filename=geo.filename,
        file_type=geo.file_type,
        stored_path=geo.stored_path,
        status=geo.status.value,
        crs=geo.crs,
        feature_count=geo.feature_count,
        error_message=geo.error_message,
        created_at=geo.created_at,
        completed_at=geo.completed_at,
    )


def _orm_to_feature_record(orm: FeatureORM) -> FeatureRecord:
    """Convert FeatureORM to FeatureRecord domain object."""
    return FeatureRecord(
        file_id=orm.file_id,  # type: ignore[arg-type]
        feature_index=orm.feature_index,  # type: ignore[arg-type]
        layer=orm.layer,  # type: ignore[arg-type]
        geometry_type=orm.geometry_type,  # type: ignore[arg-type]
        geometry=orm.geometry,  # type: ignore[arg-type]
        properties=orm.properties,  # type: ignore[arg-type]
        measurement=Measurement(
            area_m2=orm.area_m2,  # type: ignore[arg-type]
            length_m=orm.length_m,  # type: ignore[arg-type]
            geodesic_area_m2=orm.geodesic_area_m2,  # type: ignore[arg-type]
            geodesic_length_m=orm.geodesic_length_m,  # type: ignore[arg-type]
            measurement_crs=orm.measurement_crs,  # type: ignore[arg-type]
            note=orm.note,  # type: ignore[arg-type]
        ),
    )


def _feature_record_to_orm(record: FeatureRecord) -> FeatureORM:
    """Convert FeatureRecord domain object to FeatureORM."""
    return FeatureORM(
        file_id=record.file_id,
        feature_index=record.feature_index,
        layer=record.layer,
        geometry_type=record.geometry_type,
        geometry=record.geometry,
        properties=record.properties,
        area_m2=record.measurement.area_m2,
        length_m=record.measurement.length_m,
        geodesic_area_m2=record.measurement.geodesic_area_m2,
        geodesic_length_m=record.measurement.geodesic_length_m,
        measurement_crs=record.measurement.measurement_crs,
        note=record.measurement.note,
    )


class SqlFileRepository(FileRepository):
    """Repository: persists GeoFile aggregates via SQLAlchemy."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, f: GeoFile) -> None:
        """Insert a new GeoFile."""
        orm = _geo_file_to_orm(f)
        self._session.add(orm)

    def flush(self) -> None:
        """Flush pending changes to the database."""
        self._session.flush()

    def commit(self) -> None:
        """Commit the current transaction."""
        self._session.commit()

    def get(self, file_id: str) -> GeoFile | None:
        """Return the GeoFile or None if unknown."""
        orm = self._session.get(FileORM, file_id)
        return _orm_to_geo_file(orm) if orm else None

    def save(self, f: GeoFile) -> None:
        """Persist state changes to an existing GeoFile."""
        orm = self._session.get(FileORM, f.id)
        if orm is None:
            raise ValueError(f"GeoFile {f.id} not found")
        orm.status = f.status.value  # type: ignore[assignment]
        orm.crs = f.crs  # type: ignore[assignment]
        orm.feature_count = f.feature_count  # type: ignore[assignment]
        orm.error_message = f.error_message  # type: ignore[assignment]
        orm.completed_at = f.completed_at if f.completed_at is not None else None  # type: ignore[assignment]
        self._session.add(orm)

    def list_by_status(self, status: FileStatus) -> Sequence[GeoFile]:
        """Return all files with the given status (for startup recovery)."""
        stmt = select(FileORM).where(FileORM.status == status.value)
        orms = self._session.execute(stmt).scalars().all()
        return [_orm_to_geo_file(o) for o in orms]


class SqlFeatureRepository(FeatureRepository):
    """Repository: persists FeatureRecord rows via SQLAlchemy."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(self, file_id: str, records: Sequence[FeatureRecord]) -> None:
        """Bulk-insert all feature records for one file."""
        orms = [_feature_record_to_orm(r) for r in records]
        self._session.add_all(orms)

    def list(
        self,
        file_id: str,
        *,
        limit: int,
        offset: int,
        geometry_type: str | None,
    ) -> Page[FeatureRecord]:
        """Return one paginated, optionally geometry-type-filtered page."""
        from sqlalchemy import Select

        stmt = select(FeatureORM).where(FeatureORM.file_id == file_id)
        if geometry_type:
            stmt = stmt.where(FeatureORM.geometry_type == geometry_type)
        stmt = stmt.order_by(FeatureORM.feature_index)

        # Get total count
        count_stmt: Select[tuple[int]] = select(FeatureORM.id).where(FeatureORM.file_id == file_id)
        if geometry_type:
            count_stmt = count_stmt.where(FeatureORM.geometry_type == geometry_type)
        total = len(self._session.execute(count_stmt).all())

        # Apply pagination
        stmt = stmt.limit(limit).offset(offset)
        orms = self._session.execute(stmt).scalars().all()

        items = tuple(_orm_to_feature_record(o) for o in orms)
        return Page(items=items, total=total, limit=limit, offset=offset)
