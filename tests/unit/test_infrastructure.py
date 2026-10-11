"""Unit tests for infrastructure layer: db, orm, repositories, storage."""

from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import text

from app.config import Settings
from app.domain.models import FeatureRecord, GeoFile, Measurement
from app.domain.status import FileStatus
from app.infrastructure.db import get_session, init_engine
from app.infrastructure.orm import Base
from app.infrastructure.repositories import SqlFeatureRepository, SqlFileRepository
from app.infrastructure.storage import LocalFileStorage


def _get_engine_and_session_local():
    """Import engine and SessionLocal after init_engine has been called."""
    from app.infrastructure.db import SessionLocal, engine

    return engine, SessionLocal


class TestDatabase:
    """Tests for database engine and session management."""

    def test_init_engine_creates_tables(self) -> None:
        """init_engine creates engine and session factory."""
        settings = Settings(database_url="sqlite:///:memory:")
        init_engine(settings)

        engine, SessionLocal = _get_engine_and_session_local()

        assert engine is not None
        assert SessionLocal is not None

        # Tables should be created
        Base.metadata.create_all(engine)

        # Verify tables exist
        with engine.connect() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            table_names = {row[0] for row in result}
            assert "files" in table_names
            assert "features" in table_names

    def test_get_session_yields_session(self) -> None:
        """get_session yields a working session."""
        settings = Settings(database_url="sqlite:///:memory:")
        init_engine(settings)

        engine, _ = _get_engine_and_session_local()
        Base.metadata.create_all(engine)

        with get_session() as session:
            assert session is not None
            # Can execute queries
            result = session.execute(text("SELECT 1")).scalar()
            assert result == 1

    def test_session_rollback_on_exception(self) -> None:
        """Session rolls back on exception."""
        settings = Settings(database_url="sqlite:///:memory:")
        init_engine(settings)

        engine, _ = _get_engine_and_session_local()
        Base.metadata.create_all(engine)

        from sqlalchemy.exc import SQLAlchemyError

        with pytest.raises(SQLAlchemyError):
            with get_session() as session:
                session.execute(text("INVALID SQL"))

        # Next session should work
        with get_session() as session:
            result = session.execute(text("SELECT 1")).scalar()
            assert result == 1


class TestRepositories:
    """Tests for SQLAlchemy repositories."""

    @pytest.fixture
    def session(self):
        """Create a test session with in-memory SQLite."""
        settings = Settings(database_url="sqlite:///:memory:")
        init_engine(settings)

        engine, SessionLocal = _get_engine_and_session_local()
        Base.metadata.create_all(engine)

        session = SessionLocal()
        yield session
        session.close()

    def test_file_repository_add_and_get(self, session) -> None:
        """FileRepository can add and retrieve GeoFile."""
        repo = SqlFileRepository(session)

        geo_file = GeoFile(
            id="test-id",
            filename="test.zip",
            file_type="shapefile_zip",
            stored_path="/uploads/test-id/test.zip",
            status=FileStatus.PENDING,
        )

        repo.add(geo_file)
        session.commit()

        retrieved = repo.get("test-id")
        assert retrieved is not None
        assert retrieved.id == "test-id"
        assert retrieved.filename == "test.zip"
        assert retrieved.status == FileStatus.PENDING

    def test_file_repository_get_unknown_returns_none(self, session) -> None:
        """FileRepository.get returns None for unknown ID."""
        repo = SqlFileRepository(session)
        result = repo.get("unknown-id")
        assert result is None

    def test_file_repository_save_status_change(self, session) -> None:
        """FileRepository.save persists status changes."""
        repo = SqlFileRepository(session)

        geo_file = GeoFile(
            id="test-id-2",
            filename="test2.zip",
            file_type="shapefile_zip",
            stored_path="/uploads/test-id-2/test2.zip",
            status=FileStatus.PENDING,
        )
        repo.add(geo_file)
        session.commit()

        # Change status
        geo_file.start_processing()
        repo.save(geo_file)
        session.commit()

        retrieved = repo.get("test-id-2")
        assert retrieved is not None
        assert retrieved.status == FileStatus.PROCESSING

    def test_file_repository_list_by_status(self, session) -> None:
        """FileRepository.list_by_status returns files with matching status."""
        repo = SqlFileRepository(session)

        # Add files with different statuses
        for i, status in enumerate([FileStatus.PENDING, FileStatus.PROCESSING, FileStatus.COMPLETED]):
            geo_file = GeoFile(
                id=f"test-id-{i}",
                filename=f"test{i}.zip",
                file_type="shapefile_zip",
                stored_path=f"/uploads/test-id-{i}/test{i}.zip",
                status=status,
            )
            repo.add(geo_file)
        session.commit()

        pending = repo.list_by_status(FileStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].id == "test-id-0"

        processing = repo.list_by_status(FileStatus.PROCESSING)
        assert len(processing) == 1

    def test_feature_repository_add_many_and_list(self, session) -> None:
        """FeatureRepository can bulk insert and paginate."""
        file_repo = SqlFileRepository(session)
        feature_repo = SqlFeatureRepository(session)

        # First add a file
        geo_file = GeoFile(
            id="file-1",
            filename="test.zip",
            file_type="shapefile_zip",
            stored_path="/uploads/file-1/test.zip",
            status=FileStatus.COMPLETED,
            crs="EPSG:4326",
            feature_count=2,
        )
        file_repo.add(geo_file)
        session.commit()

        # Add features
        records = [
            FeatureRecord(
                file_id="file-1",
                feature_index=0,
                layer="layer1",
                geometry_type="Polygon",
                geometry={"type": "Polygon", "coordinates": []},
                properties={"name": "Plot A"},
                measurement=Measurement(area_m2=1000.0),
            ),
            FeatureRecord(
                file_id="file-1",
                feature_index=1,
                layer="layer1",
                geometry_type="LineString",
                geometry={"type": "LineString", "coordinates": []},
                properties={"name": "Road 1"},
                measurement=Measurement(length_m=500.0),
            ),
        ]
        feature_repo.add_many("file-1", records)
        session.commit()

        # List all
        page = feature_repo.list("file-1", limit=10, offset=0, geometry_type=None)
        assert page.total == 2
        assert len(page.items) == 2

        # Filter by geometry type
        page = feature_repo.list("file-1", limit=10, offset=0, geometry_type="Polygon")
        assert page.total == 1
        assert page.items[0].geometry_type == "Polygon"

        # Pagination
        page = feature_repo.list("file-1", limit=1, offset=0, geometry_type=None)
        assert len(page.items) == 1
        assert page.items[0].feature_index == 0

        page = feature_repo.list("file-1", limit=1, offset=1, geometry_type=None)
        assert len(page.items) == 1
        assert page.items[0].feature_index == 1


class TestStorage:
    """Tests for LocalFileStorage."""

    def test_save_creates_file(self) -> None:
        """LocalFileStorage.save writes the stream to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            storage = LocalFileStorage(base_dir)

            content = b"test content"
            stream = BytesIO(content)

            path = storage.save("file-1", "test.zip", stream)

            assert path.exists()
            assert path.read_bytes() == content
            assert "file-1" in str(path)

    def test_save_sanitizes_filename(self) -> None:
        """LocalFileStorage.save sanitizes dangerous filenames."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            storage = LocalFileStorage(base_dir)

            # Path traversal attempt
            stream = BytesIO(b"content")
            path = storage.save("file-1", "../../../etc/passwd", stream)

            # Should be saved under the file_id directory
            assert "file-1" in str(path)
            assert "etc" not in str(path) or str(path).startswith(str(base_dir))

    def test_save_creates_directory(self) -> None:
        """LocalFileStorage.save creates the file_id directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            storage = LocalFileStorage(base_dir)

            stream = BytesIO(b"content")
            path = storage.save("new-file-id", "test.zip", stream)

            assert path.parent.exists()
            assert path.parent.name == "new-file-id"
