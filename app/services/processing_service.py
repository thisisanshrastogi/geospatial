"""Processing pipeline: read -> normalize -> project -> measure -> persist.

Background pipeline; never raises out of `process` (marks FAILED instead).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from pyproj import CRS
from shapely.geometry import mapping
from shapely.ops import transform

from app.domain.errors import DomainError
from app.domain.models import FeatureRecord, GeoFile, Measurement, ParsedFeature
from app.domain.ports import (
    FeatureRepository,
    FileRepository,
    ProjectionStrategy,
)
from app.geo.measurers import MeasurerRegistry
from app.geo.normalize import normalize
from app.geo.readers import ReaderRegistry

logger = logging.getLogger(__name__)


class ProcessingService:
    """Background pipeline; never raises out of `process` (marks FAILED instead)."""

    def __init__(
        self,
        file_repo: FileRepository,
        feature_repo: FeatureRepository,
        reader_registry: ReaderRegistry,
        measurer_registry: MeasurerRegistry,
        projector: ProjectionStrategy,
    ) -> None:
        self._file_repo = file_repo
        self._feature_repo = feature_repo
        self._reader_registry = reader_registry
        self._measurer_registry = measurer_registry
        self._projector = projector

    @staticmethod
    def create_background_job(file_id: str) -> Callable[[], None]:
        """Build a background job with an isolated database session."""
        from app.geo.measurers import MeasurerRegistry
        from app.geo.projection import Projector
        from app.geo.readers import KmlReader, ReaderRegistry, ShapefileZipReader
        from app.infrastructure.db import SessionLocal
        from app.infrastructure.repositories import SqlFeatureRepository, SqlFileRepository

        def _job() -> None:
            logger.info("Starting background processing for file_id=%s", file_id)
            session_factory = SessionLocal
            assert session_factory is not None
            session = session_factory()
            try:
                file_repo = SqlFileRepository(session)
                feature_repo = SqlFeatureRepository(session)
                processing_service = ProcessingService(
                    file_repo=file_repo,
                    feature_repo=feature_repo,
                    reader_registry=ReaderRegistry([ShapefileZipReader(), KmlReader()]),
                    measurer_registry=MeasurerRegistry(),
                    projector=Projector(),
                )
                processing_service.process(file_id)
                session.commit()
            except Exception:
                logger.exception("Error in background processing, rolling back")
                session.rollback()
                raise
            finally:
                session.close()
            logger.info("Completed background processing for file_id=%s", file_id)

        return _job

    def process(self, file_id: str) -> None:
        """Run the full measurement pipeline for `file_id`."""
        # 1. Load file and transition to PROCESSING
        geo_file = self._file_repo.get(file_id)
        if geo_file is None:
            logger.error("File %s not found during processing", file_id)
            return

        try:
            geo_file.start_processing()
            self._file_repo.save(geo_file)
        except DomainError:
            logger.exception("Invalid status transition for %s", file_id)
            return

        # 2. Resolve reader and read dataset
        try:
            reader = self._reader_registry.for_path(Path(geo_file.stored_path))
            dataset = reader.read(Path(geo_file.stored_path))
        except DomainError:
            geo_file.mark_failed("Invalid geo data")
            self._file_repo.save(geo_file)
            return
        except Exception:
            logger.exception("Unexpected error reading file %s", file_id)
            geo_file.mark_failed("Internal processing error")
            self._file_repo.save(geo_file)
            return

        # 3. Process each feature
        feature_records: list[FeatureRecord] = []
        for idx, feature in enumerate(dataset.features):
            try:
                record = self._process_feature(idx, feature, dataset.crs, geo_file)
                feature_records.append(record)
            except Exception:
                # Log but continue with other features
                logger.exception("Error processing feature %d of file %s", idx, file_id)

        # 4. Bulk insert features and mark COMPLETED
        try:
            self._feature_repo.add_many(file_id, feature_records)
            geo_file.mark_completed(str(dataset.crs), len(feature_records))
            self._file_repo.save(geo_file)
        except Exception:
            logger.exception("Error saving results for file %s", file_id)
            geo_file.mark_failed("Internal processing error")
            self._file_repo.save(geo_file)

    def _process_feature(
        self,
        feature_index: int,
        feature: ParsedFeature,
        source_crs: CRS,
        geo_file: GeoFile,
    ) -> FeatureRecord:
        """Process a single feature: normalize -> measure -> create record."""
        # Normalize geometry
        geom, norm_note = normalize(feature.geometry)

        # Get measurer for this geometry type
        measurer = self._measurer_registry.for_geometry_type(feature.geometry_type)

        # Project if needed
        if measurer.requires_projection and geom is not None:
            projected_geom, measurement_crs = self._projector.to_metric(geom, source_crs)
        else:
            projected_geom = geom
            measurement_crs = source_crs

        geodesic_geom = self._to_wgs84(geom, source_crs)

        # Measure
        measurement = measurer.measure(projected_geom, geodesic_geom)
        # Create new Measurement with all fields (frozen dataclass)
        measurement = Measurement(
            area_m2=measurement.area_m2,
            length_m=measurement.length_m,
            geodesic_area_m2=measurement.geodesic_area_m2,
            geodesic_length_m=measurement.geodesic_length_m,
            measurement_crs=str(measurement_crs) if geom is not None else None,
            note=norm_note or measurement.note,
        )

        # Convert geometry to GeoJSON (in source CRS)
        geom_geojson = (
            mapping(feature.geometry)
            if feature.geometry is not None
            else {"type": "GeometryCollection", "geometries": []}
        )

        return FeatureRecord(
            file_id=geo_file.id,
            feature_index=feature_index,
            layer=feature.layer,
            geometry_type=feature.geometry_type,
            geometry=geom_geojson,
            properties=feature.properties,
            measurement=measurement,
        )

    @staticmethod
    def _to_wgs84(geom: object, source_crs: CRS) -> object:
        """Convert a source geometry to WGS84 for geodesic cross-checks."""
        if geom is None or source_crs.is_geographic:
            return geom
        from pyproj import Transformer

        transformer = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
        return transform(transformer.transform, geom)
