"""Unit tests for domain layer: models, status, errors, ports."""

from __future__ import annotations

import pytest

from app.domain.errors import (
    DomainError,
    FileNotFound,
    FileTooLarge,
    InvalidGeoData,
    InvalidStatusTransition,
    ProcessingNotComplete,
    UnsupportedFileType,
)
from app.domain.models import (
    FeatureRecord,
    GeoFile,
    Measurement,
    Page,
    ParsedFeature,
)
from app.domain.status import FileStatus, allowed_transitions, can_transition


class TestFileStatus:
    """Test the FileStatus state machine."""

    def test_pending_can_go_to_processing(self) -> None:
        assert can_transition(FileStatus.PENDING, FileStatus.PROCESSING)

    def test_pending_can_go_to_failed(self) -> None:
        assert can_transition(FileStatus.PENDING, FileStatus.FAILED)

    def test_processing_can_go_to_completed(self) -> None:
        assert can_transition(FileStatus.PROCESSING, FileStatus.COMPLETED)

    def test_processing_can_go_to_failed(self) -> None:
        assert can_transition(FileStatus.PROCESSING, FileStatus.FAILED)

    def test_completed_is_terminal(self) -> None:
        assert not can_transition(FileStatus.COMPLETED, FileStatus.PENDING)
        assert not can_transition(FileStatus.COMPLETED, FileStatus.PROCESSING)
        assert not can_transition(FileStatus.COMPLETED, FileStatus.FAILED)
        assert allowed_transitions(FileStatus.COMPLETED) == frozenset()

    def test_failed_is_terminal(self) -> None:
        assert not can_transition(FileStatus.FAILED, FileStatus.PENDING)
        assert not can_transition(FileStatus.FAILED, FileStatus.PROCESSING)
        assert not can_transition(FileStatus.FAILED, FileStatus.COMPLETED)
        assert allowed_transitions(FileStatus.FAILED) == frozenset()

    def test_illegal_transitions_raise(self) -> None:
        # These should not raise from can_transition, just return False
        assert not can_transition(FileStatus.COMPLETED, FileStatus.PROCESSING)
        assert not can_transition(FileStatus.FAILED, FileStatus.PROCESSING)


class TestGeoFile:
    """Test GeoFile status transitions."""

    def make_file(self, status: FileStatus = FileStatus.PENDING) -> GeoFile:
        return GeoFile(
            id="test-id",
            filename="test.zip",
            file_type="shapefile_zip",
            stored_path="/tmp/test.zip",
            status=status,
        )

    def test_start_processing_from_pending(self) -> None:
        f = self.make_file(FileStatus.PENDING)
        f.start_processing()
        assert f.status == FileStatus.PROCESSING

    def test_start_processing_from_processing_raises(self) -> None:
        f = self.make_file(FileStatus.PROCESSING)
        with pytest.raises(InvalidStatusTransition):
            f.start_processing()

    def test_mark_completed_from_processing(self) -> None:
        f = self.make_file(FileStatus.PROCESSING)
        f.mark_completed("EPSG:4326", 10)
        assert f.status == FileStatus.COMPLETED
        assert f.crs == "EPSG:4326"
        assert f.feature_count == 10
        assert f.completed_at is not None

    def test_mark_completed_from_pending_raises(self) -> None:
        f = self.make_file(FileStatus.PENDING)
        with pytest.raises(InvalidStatusTransition):
            f.mark_completed("EPSG:4326", 10)

    def test_mark_failed_from_pending(self) -> None:
        f = self.make_file(FileStatus.PENDING)
        f.mark_failed("something went wrong")
        assert f.status == FileStatus.FAILED
        assert f.error_message == "something went wrong"
        assert f.completed_at is not None

    def test_mark_failed_from_processing(self) -> None:
        f = self.make_file(FileStatus.PROCESSING)
        f.mark_failed("processing error")
        assert f.status == FileStatus.FAILED

    def test_mark_failed_from_completed_raises(self) -> None:
        f = self.make_file(FileStatus.COMPLETED)
        with pytest.raises(InvalidStatusTransition):
            f.mark_failed("oops")

    def test_mark_failed_from_failed_raises(self) -> None:
        f = self.make_file(FileStatus.FAILED)
        with pytest.raises(InvalidStatusTransition):
            f.mark_failed("oops")


class TestErrors:
    """Test error hierarchy and codes."""

    def test_domain_error_defaults(self) -> None:
        e = DomainError("msg")
        assert e.code == "DOMAIN_ERROR"
        assert e.http_status == 500

    def test_unsupported_file_type(self) -> None:
        e = UnsupportedFileType("bad type")
        assert e.code == "UNSUPPORTED_FILE_TYPE"
        assert e.http_status == 400

    def test_file_too_large(self) -> None:
        e = FileTooLarge("too big")
        assert e.code == "FILE_TOO_LARGE"
        assert e.http_status == 413

    def test_file_not_found(self) -> None:
        e = FileNotFound("missing")
        assert e.code == "FILE_NOT_FOUND"
        assert e.http_status == 404

    def test_processing_not_complete(self) -> None:
        e = ProcessingNotComplete("still working")
        assert e.code == "PROCESSING_NOT_COMPLETE"
        assert e.http_status == 409

    def test_invalid_geo_data(self) -> None:
        e = InvalidGeoData("bad geo")
        assert e.code == "INVALID_GEO_DATA"
        assert e.http_status == 422

    def test_invalid_status_transition(self) -> None:
        e = InvalidStatusTransition("illegal move")
        assert e.code == "INVALID_STATUS_TRANSITION"
        assert e.http_status == 500


class TestModels:
    """Test domain data classes."""

    def test_parsed_feature_immutable(self) -> None:
        from dataclasses import FrozenInstanceError

        pf = ParsedFeature(
            index=0,
            layer="test",
            geometry=None,  # type: ignore
            geometry_type="Point",
            properties={"name": "test"},
        )
        assert pf.index == 0
        assert pf.layer == "test"
        with pytest.raises(FrozenInstanceError):
            pf.index = 1  # type: ignore

    def test_measurement_fields(self) -> None:
        m = Measurement(
            area_m2=100.0,
            length_m=50.0,
            geodesic_area_m2=99.5,
            geodesic_length_m=49.8,
            measurement_crs="EPSG:32643",
            note="test note",
        )
        assert m.area_m2 == 100.0
        assert m.geodesic_area_m2 == 99.5

    def test_page_generic(self) -> None:
        page = Page[int](items=(1, 2, 3), total=100, limit=10, offset=0)
        assert page.total == 100
        assert page.items == (1, 2, 3)

    def test_feature_record(self) -> None:
        m = Measurement(area_m2=100.0)
        fr = FeatureRecord(
            file_id="fid",
            feature_index=0,
            layer="layer1",
            geometry_type="Polygon",
            geometry={"type": "Polygon", "coordinates": []},
            properties={"name": "plot"},
            measurement=m,
        )
        assert fr.file_id == "fid"
        assert fr.measurement.area_m2 == 100.0


class TestPortsStructure:
    """Verify ports.py protocols have expected structure (no implementation tests)."""

    def test_geofile_reader_protocol_exists(self) -> None:
        from app.domain.ports import GeoFileReader

        assert hasattr(GeoFileReader, "can_read")
        assert hasattr(GeoFileReader, "read")

    def test_projection_strategy_protocol_exists(self) -> None:
        from app.domain.ports import ProjectionStrategy

        assert hasattr(ProjectionStrategy, "choose_crs")

    def test_geometry_measurer_protocol_exists(self) -> None:
        from app.domain.ports import GeometryMeasurer

        assert hasattr(GeometryMeasurer, "requires_projection")
        assert hasattr(GeometryMeasurer, "measure")

    def test_file_storage_protocol_exists(self) -> None:
        from app.domain.ports import FileStorage

        assert hasattr(FileStorage, "save")

    def test_task_dispatcher_protocol_exists(self) -> None:
        from app.domain.ports import TaskDispatcher

        assert hasattr(TaskDispatcher, "enqueue_processing")

    def test_file_repository_protocol_exists(self) -> None:
        from app.domain.ports import FileRepository

        assert hasattr(FileRepository, "add")
        assert hasattr(FileRepository, "get")
        assert hasattr(FileRepository, "save")
        assert hasattr(FileRepository, "list_by_status")

    def test_feature_repository_protocol_exists(self) -> None:
        from app.domain.ports import FeatureRepository

        assert hasattr(FeatureRepository, "add_many")
        assert hasattr(FeatureRepository, "list")
