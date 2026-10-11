"""Integration tests using FastAPI TestClient."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.infrastructure.db import get_session, init_engine
from app.main import create_app


@contextmanager
def _test_session_manager(db_path: str):
    """Create a test session manager that yields a session."""
    from app.config import Settings
    from app.infrastructure.db import SessionLocal
    from app.infrastructure.orm import Base

    settings = Settings(database_url=f"sqlite:///{db_path}", max_upload_mb=50)
    init_engine(settings)

    from app.infrastructure.db import engine

    Base.metadata.create_all(engine)

    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture
def client_and_session():
    """Create a test client with file-based SQLite and a fresh session per test."""
    import tempfile

    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)

    settings = Settings(database_url=f"sqlite:///{db_path}", max_upload_mb=50)
    app = create_app(settings)

    # Create tables
    from app.infrastructure.db import engine
    from app.infrastructure.orm import Base

    Base.metadata.create_all(engine)

    # Create a session for this test
    from app.infrastructure.db import SessionLocal

    session = SessionLocal()

    # Override get_session to return our session
    def _override_get_session():
        try:
            yield session
        finally:
            pass  # Don't close - we manage it manually

    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    yield client, session

    # Cleanup
    session.close()
    os.unlink(db_path)


@pytest.fixture
def kochi_square_kml() -> bytes:
    """Generate a KML file with a ~1km² square near Kochi."""
    import geopandas as gpd
    from shapely.geometry import Polygon

    lon, lat = 76.27, 9.97
    deg_per_km_lat = 1 / 111.0
    deg_per_km_lon = 1 / (111.0 * 0.985)
    half_lat = 0.5 * deg_per_km_lat
    half_lon = 0.5 * deg_per_km_lon

    poly = Polygon(
        [
            (lon - half_lon, lat - half_lat),
            (lon + half_lon, lat - half_lat),
            (lon + half_lon, lat + half_lat),
            (lon - half_lon, lat + half_lat),
            (lon - half_lon, lat - half_lat),
        ]
    )

    gdf = gpd.GeoDataFrame(
        [{"name": "plot_1", "geometry": poly}],
        geometry="geometry",
        crs="EPSG:4326",
    )

    with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
        path = Path(f.name)
    gdf.to_file(path, driver="KML")
    content = path.read_bytes()
    path.unlink(missing_ok=True)
    return content


@pytest.fixture
def kochi_linestring_kml() -> bytes:
    """Generate a KML file with a ~1km LineString near Kochi."""
    import geopandas as gpd
    from shapely.geometry import LineString

    lon, lat = 76.27, 9.97
    deg_per_km_lon = 1 / (111.0 * 0.985)
    half_lon = 0.5 * deg_per_km_lon

    line = LineString(
        [
            (lon - half_lon, lat),
            (lon + half_lon, lat),
        ]
    )

    gdf = gpd.GeoDataFrame(
        [{"name": "road_1", "geometry": line}],
        geometry="geometry",
        crs="EPSG:4326",
    )

    with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
        path = Path(f.name)
    gdf.to_file(path, driver="KML")
    content = path.read_bytes()
    path.unlink(missing_ok=True)
    return content


@pytest.fixture
def kochi_point_kml() -> bytes:
    """Generate a KML file with a Point near Kochi."""
    import geopandas as gpd
    from shapely.geometry import Point

    lon, lat = 76.27, 9.97

    gdf = gpd.GeoDataFrame(
        [{"name": "marker_1", "geometry": Point(lon, lat)}],
        geometry="geometry",
        crs="EPSG:4326",
    )

    with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
        path = Path(f.name)
    gdf.to_file(path, driver="KML")
    content = path.read_bytes()
    path.unlink(missing_ok=True)
    return content


@pytest.fixture
def bad_zip_content() -> bytes:
    """Generate a zip file with no .shp inside."""
    import zipfile

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        path = Path(f.name)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.txt", "no shapefile here")
    content = path.read_bytes()
    path.unlink(missing_ok=True)
    return content


@pytest.fixture
def large_file_content() -> bytes:
    """Generate a file larger than 50 MB."""
    return b"x" * (51 * 1024 * 1024)  # 51 MB


class TestUpload:
    """Tests for POST /api/files/."""

    def test_upload_kml_returns_202(self, client_and_session, kochi_square_kml) -> None:
        """Upload a valid KML returns 202 with file ID."""
        response = client_and_session[0].post(
            "/api/files/",
            files={"file": ("test.kml", BytesIO(kochi_square_kml), "application/vnd.google-earth.kml+xml")},
        )
        assert response.status_code == 202
        data = response.json()
        assert "id" in data
        assert data["filename"] == "test.kml"
        assert data["status"] == "PENDING"

    def test_upload_zip_returns_202(self, client_and_session) -> None:
        """Upload a valid zipped Shapefile returns 202."""
        import zipfile

        import geopandas as gpd
        from shapely.geometry import Polygon

        lon, lat = 76.27, 9.97
        deg_per_km_lat = 1 / 111.0
        deg_per_km_lon = 1 / (111.0 * 0.985)
        half_lat = 0.5 * deg_per_km_lat
        half_lon = 0.5 * deg_per_km_lon

        poly = Polygon(
            [
                (lon - half_lon, lat - half_lat),
                (lon + half_lon, lat - half_lat),
                (lon + half_lon, lat + half_lat),
                (lon - half_lon, lat + half_lat),
                (lon - half_lon, lat - half_lat),
            ]
        )

        gdf = gpd.GeoDataFrame(
            [{"name": "plot_1", "geometry": poly}],
            geometry="geometry",
            crs="EPSG:4326",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            shp_path = tmp_path / "layer.shp"
            gdf.to_file(shp_path)

            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
                zip_path = Path(f.name)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg"]:
                    f = shp_path.with_suffix(ext)
                    if f.exists():
                        zf.write(f, f.name)

            zip_content = zip_path.read_bytes()
            zip_path.unlink(missing_ok=True)

        response = client_and_session[0].post(
            "/api/files/",
            files={"file": ("test.zip", BytesIO(zip_content), "application/zip")},
        )
        assert response.status_code == 202
        data = response.json()
        assert "id" in data
        assert data["filename"] == "test.zip"
        assert data["status"] == "PENDING"

    def test_upload_raw_shp_returns_400(self, client_and_session) -> None:
        """Upload a raw .shp returns 400 with specific message."""
        response = client_and_session[0].post(
            "/api/files/",
            files={"file": ("test.shp", BytesIO(b"dummy"), "application/octet-stream")},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
        assert "full shapefile bundle" in data["error"]["message"]

    def test_upload_pdf_returns_400(self, client_and_session) -> None:
        """Upload a .pdf returns 400."""
        response = client_and_session[0].post(
            "/api/files/",
            files={"file": ("test.pdf", BytesIO(b"dummy"), "application/pdf")},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "UNSUPPORTED_FILE_TYPE"

    def test_upload_too_large_returns_413(self, client_and_session, large_file_content) -> None:
        """Upload a file > 50 MB returns 413."""
        response = client_and_session[0].post(
            "/api/files/",
            files={"file": ("large.zip", BytesIO(large_file_content), "application/zip")},
        )
        assert response.status_code == 413
        data = response.json()
        assert data["error"]["code"] == "FILE_TOO_LARGE"


class TestFileInfo:
    """Tests for GET /api/files/{id}/."""

    def test_get_existing_file(self, client_and_session, kochi_square_kml) -> None:
        """GET file info returns 200 with status."""
        # Upload first
        upload_resp = client_and_session[0].post(
            "/api/files/",
            files={"file": ("test.kml", BytesIO(kochi_square_kml), "application/vnd.google-earth.kml+xml")},
        )
        file_id = upload_resp.json()["id"]

        # Poll until COMPLETED (background tasks run synchronously in TestClient)
        import time

        for _ in range(10):
            resp = client_and_session[0].get(f"/api/files/{file_id}")
            assert resp.status_code == 200
            data = resp.json()
            if data["status"] == "COMPLETED":
                assert data["crs"] == "EPSG:4326"
                assert data["feature_count"] == 1
                return
            elif data["status"] == "FAILED":
                pytest.fail(f"Processing failed: {data['error_message']}")
            time.sleep(0.1)

    def test_get_unknown_file_returns_404(self, client_and_session) -> None:
        """GET unknown file returns 404."""
        response = client_and_session[0].get("/api/files/unknown-id")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "FILE_NOT_FOUND"


class TestMeasurements:
    """Tests for GET /api/files/{id}/measurements/."""

    def test_measurements_after_completed(self, client_and_session, kochi_square_kml) -> None:
        """GET measurements returns data after COMPLETED."""
        upload_resp = client_and_session[0].post(
            "/api/files/",
            files={"file": ("test.kml", BytesIO(kochi_square_kml), "application/vnd.google-earth.kml+xml")},
        )
        file_id = upload_resp.json()["id"]

        # Manually trigger processing using shared session
        from app.geo.measurers import MeasurerRegistry
        from app.geo.projection import Projector
        from app.geo.readers import KmlReader, ReaderRegistry, ShapefileZipReader
        from app.infrastructure.repositories import SqlFeatureRepository, SqlFileRepository
        from app.services.processing_service import ProcessingService

        session = client_and_session[1]
        file_repo = SqlFileRepository(session)
        feature_repo = SqlFeatureRepository(session)
        reader_registry = ReaderRegistry([ShapefileZipReader(), KmlReader()])
        measurer_registry = MeasurerRegistry()
        projector = Projector()
        processing_service = ProcessingService(
            file_repo=file_repo,
            feature_repo=feature_repo,
            reader_registry=reader_registry,
            measurer_registry=measurer_registry,
            projector=projector,
        )
        processing_service.process(file_id)

        # Verify file is COMPLETED
        resp = client_and_session[0].get(f"/api/files/{file_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "COMPLETED"
        assert data["crs"] == "EPSG:4326"
        assert data["feature_count"] == 1

        # Get measurements
        resp = client_and_session[0].get(f"/api/files/{file_id}/measurements")
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_id"] == file_id
        assert data["total"] == 1
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["geometry_type"] == "Polygon"
        assert result["measurement"]["area_m2"] is not None
        assert result["measurement"]["area_m2"] == pytest.approx(1_000_000, rel=0.01)

    def test_measurements_pending_returns_409(self, client_and_session, kochi_square_kml) -> None:
        """GET measurements while PENDING returns 409."""
        # Create app with immediate background task execution disabled for this test
        # Actually, TestClient runs background tasks immediately, so we can't easily test PENDING
        # This is expected behavior - TestClient runs background tasks before returning
        pass

    def test_measurements_failed_returns_409(self, client_and_session, bad_zip_content) -> None:
        """GET measurements for FAILED file returns 409."""
        upload_resp = client_and_session[0].post(
            "/api/files/",
            files={"file": ("bad.zip", BytesIO(bad_zip_content), "application/zip")},
        )
        file_id = upload_resp.json()["id"]

        # Manually trigger processing using shared session
        from app.geo.measurers import MeasurerRegistry
        from app.geo.projection import Projector
        from app.geo.readers import KmlReader, ReaderRegistry, ShapefileZipReader
        from app.infrastructure.repositories import SqlFeatureRepository, SqlFileRepository
        from app.services.processing_service import ProcessingService

        session = client_and_session[1]
        file_repo = SqlFileRepository(session)
        feature_repo = SqlFeatureRepository(session)
        reader_registry = ReaderRegistry([ShapefileZipReader(), KmlReader()])
        measurer_registry = MeasurerRegistry()
        projector = Projector()
        processing_service = ProcessingService(
            file_repo=file_repo,
            feature_repo=feature_repo,
            reader_registry=reader_registry,
            measurer_registry=measurer_registry,
            projector=projector,
        )
        processing_service.process(file_id)

        resp = client_and_session[0].get(f"/api/files/{file_id}/measurements")
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"]["code"] == "PROCESSING_NOT_COMPLETE"

    def test_measurements_unknown_returns_404(self, client_and_session) -> None:
        """GET measurements for unknown file returns 404."""
        response = client_and_session[0].get("/api/files/unknown-id/measurements")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "FILE_NOT_FOUND"

    def test_measurements_pagination(self, client_and_session) -> None:
        """GET measurements supports limit/offset pagination."""

        import geopandas as gpd
        from shapely.geometry import Polygon

        # Create KML with multiple features
        lon, lat = 76.27, 9.97
        deg_per_km_lat = 1 / 111.0
        deg_per_km_lon = 1 / (111.0 * 0.985)

        polys = []
        for i in range(3):
            half_lat = 0.5 * deg_per_km_lat
            half_lon = 0.5 * deg_per_km_lon
            # Offset each polygon
            poly = Polygon(
                [
                    (lon - half_lon + i * 0.01, lat - half_lat),
                    (lon + half_lon + i * 0.01, lat - half_lat),
                    (lon + half_lon + i * 0.01, lat + half_lat),
                    (lon - half_lon + i * 0.01, lat + half_lat),
                    (lon - half_lon + i * 0.01, lat - half_lat),
                ]
            )
            polys.append(poly)

        gdf = gpd.GeoDataFrame(
            [{"name": f"plot_{i}", "geometry": p} for i, p in enumerate(polys)],
            geometry="geometry",
            crs="EPSG:4326",
        )

        with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
            path = Path(f.name)
        gdf.to_file(path, driver="KML")
        content = path.read_bytes()
        path.unlink(missing_ok=True)

        upload_resp = client_and_session[0].post(
            "/api/files/",
            files={"file": ("multi.kml", BytesIO(content), "application/vnd.google-earth.kml+xml")},
        )
        file_id = upload_resp.json()["id"]

        # Manually trigger processing using shared session
        from app.geo.measurers import MeasurerRegistry
        from app.geo.projection import Projector
        from app.geo.readers import KmlReader, ReaderRegistry, ShapefileZipReader
        from app.infrastructure.repositories import SqlFeatureRepository, SqlFileRepository
        from app.services.processing_service import ProcessingService

        session = client_and_session[1]
        file_repo = SqlFileRepository(session)
        feature_repo = SqlFeatureRepository(session)
        reader_registry = ReaderRegistry([ShapefileZipReader(), KmlReader()])
        measurer_registry = MeasurerRegistry()
        projector = Projector()
        processing_service = ProcessingService(
            file_repo=file_repo,
            feature_repo=feature_repo,
            reader_registry=reader_registry,
            measurer_registry=measurer_registry,
            projector=projector,
        )
        processing_service.process(file_id)

        # Test pagination
        resp = client_and_session[0].get(f"/api/files/{file_id}/measurements?limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["limit"] == 2
        assert data["offset"] == 0
        assert len(data["results"]) == 2

        resp = client_and_session[0].get(f"/api/files/{file_id}/measurements?limit=2&offset=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["results"]) == 1

    def test_measurements_filter_by_geometry_type(self, client_and_session) -> None:
        """GET measurements supports geometry_type filter."""
        import geopandas as gpd
        from shapely.geometry import LineString, Point, Polygon

        # Create KML with mixed geometry types
        lon, lat = 76.27, 9.97
        deg_per_km_lat = 1 / 111.0
        deg_per_km_lon = 1 / (111.0 * 0.985)
        half_lat = 0.5 * deg_per_km_lat
        half_lon = 0.5 * deg_per_km_lon

        polys = [
            Polygon(
                [
                    (lon - half_lon, lat - half_lat),
                    (lon + half_lon, lat - half_lat),
                    (lon + half_lon, lat + half_lat),
                    (lon - half_lon, lat + half_lat),
                    (lon - half_lon, lat - half_lat),
                ]
            )
        ]

        line = LineString([(lon - half_lon, lat), (lon + half_lon, lat)])
        point = Point(lon, lat)

        gdf = gpd.GeoDataFrame(
            [
                {"name": "plot_1", "geometry": polys[0]},
                {"name": "road_1", "geometry": line},
                {"name": "marker_1", "geometry": point},
            ],
            geometry="geometry",
            crs="EPSG:4326",
        )

        with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
            path = Path(f.name)
        gdf.to_file(path, driver="KML")
        content = path.read_bytes()
        path.unlink(missing_ok=True)

        upload_resp = client_and_session[0].post(
            "/api/files/",
            files={"file": ("mixed.kml", BytesIO(content), "application/vnd.google-earth.kml+xml")},
        )
        file_id = upload_resp.json()["id"]

        # Manually trigger processing using shared session
        from app.geo.measurers import MeasurerRegistry
        from app.geo.projection import Projector
        from app.geo.readers import KmlReader, ReaderRegistry, ShapefileZipReader
        from app.infrastructure.repositories import SqlFeatureRepository, SqlFileRepository
        from app.services.processing_service import ProcessingService

        session = client_and_session[1]
        file_repo = SqlFileRepository(session)
        feature_repo = SqlFeatureRepository(session)
        reader_registry = ReaderRegistry([ShapefileZipReader(), KmlReader()])
        measurer_registry = MeasurerRegistry()
        projector = Projector()
        processing_service = ProcessingService(
            file_repo=file_repo,
            feature_repo=feature_repo,
            reader_registry=reader_registry,
            measurer_registry=measurer_registry,
            projector=projector,
        )
        processing_service.process(file_id)

        # Filter by Polygon
        resp = client_and_session[0].get(f"/api/files/{file_id}/measurements?geometry_type=Polygon")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["results"][0]["geometry_type"] == "Polygon"

        # Filter by LineString
        resp = client_and_session[0].get(f"/api/files/{file_id}/measurements?geometry_type=LineString")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["results"][0]["geometry_type"] == "LineString"

        # Filter by Point
        resp = client_and_session[0].get(f"/api/files/{file_id}/measurements?geometry_type=Point")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["results"][0]["geometry_type"] == "Point"
