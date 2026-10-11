"""Pytest fixtures: programmatic geospatial test data.

Generates KML and zipped Shapefile fixtures for:
- ~1 km² square polygon near Kochi, India (~76.27E, 9.97N) in EPSG:4326
- ~1 km straight LineString
- Point
- GeometryCollection
- Bad zip (no .prj)
- Bad zip (no .shp)
- Invalid self-intersecting polygon
- Southern hemisphere fixture (~34°S)
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import (
    GeometryCollection,
    LineString,
    Point,
    Polygon,
)

# Coordinates for Kochi, India (~76.27E, 9.97N)
KOCHI_LON = 76.27
KOCHI_LAT = 9.97

# ~1 km in degrees at this latitude (approximate)
# 1 degree lat ≈ 111 km, 1 degree lon ≈ 111 km * cos(lat)
DEG_PER_KM_LAT = 1 / 111.0
DEG_PER_KM_LON = 1 / (111.0 * 0.985)  # cos(9.97°) ≈ 0.985

# Southern hemisphere test point (~34°S, 151°E near Sydney)
SYDNEY_LON = 151.21
SYDNEY_LAT = -33.87


def _make_kochi_square() -> Polygon:
    """Create a ~1km x 1km square near Kochi."""
    half_lat = 0.5 * DEG_PER_KM_LAT
    half_lon = 0.5 * DEG_PER_KM_LON
    return Polygon(
        [
            (KOCHI_LON - half_lon, KOCHI_LAT - half_lat),
            (KOCHI_LON + half_lon, KOCHI_LAT - half_lat),
            (KOCHI_LON + half_lon, KOCHI_LAT + half_lat),
            (KOCHI_LON - half_lon, KOCHI_LAT + half_lat),
            (KOCHI_LON - half_lon, KOCHI_LAT - half_lat),
        ]
    )


def _make_kochi_linestring() -> LineString:
    """Create a ~1km straight LineString near Kochi."""
    half_lon = 0.5 * DEG_PER_KM_LON
    return LineString(
        [
            (KOCHI_LON - half_lon, KOCHI_LAT),
            (KOCHI_LON + half_lon, KOCHI_LAT),
        ]
    )


def _make_kochi_point() -> Point:
    """Create a point near Kochi."""
    return Point(KOCHI_LON, KOCHI_LAT)


def _make_geometry_collection() -> GeometryCollection:
    """Create a GeometryCollection with mixed types."""
    return GeometryCollection(
        [
            _make_kochi_point(),
            _make_kochi_linestring(),
            _make_kochi_square(),
        ]
    )


def _make_sydney_square() -> Polygon:
    """Create a ~1km x 1km square near Sydney (southern hemisphere)."""
    # At -33.87°, cos(lat) ≈ 0.83
    deg_per_km_lon = 1 / (111.0 * 0.83)
    half_lat = 0.5 * DEG_PER_KM_LAT
    half_lon = 0.5 * deg_per_km_lon
    return Polygon(
        [
            (SYDNEY_LON - half_lon, SYDNEY_LAT - half_lat),
            (SYDNEY_LON + half_lon, SYDNEY_LAT - half_lat),
            (SYDNEY_LON + half_lon, SYDNEY_LAT + half_lat),
            (SYDNEY_LON - half_lon, SYDNEY_LAT + half_lat),
            (SYDNEY_LON - half_lon, SYDNEY_LAT - half_lat),
        ]
    )


def _make_invalid_polygon() -> Polygon:
    """Create a self-intersecting (bow-tie) polygon."""
    return Polygon(
        [
            (KOCHI_LON, KOCHI_LAT),
            (KOCHI_LON + 0.01, KOCHI_LAT + 0.01),
            (KOCHI_LON + 0.01, KOCHI_LAT - 0.01),
            (KOCHI_LON - 0.01, KOCHI_LAT + 0.01),
            (KOCHI_LON, KOCHI_LAT),
        ]
    )


def _write_kml_file(path: Path, geometries: dict[str, list]) -> None:
    """Write a KML file with given geometries per layer."""
    gdf = gpd.GeoDataFrame(
        [
            {"layer": layer, "name": f"feature_{i}", "geometry": geom}
            for layer, geoms in geometries.items()
            for i, geom in enumerate(geoms)
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )
    # KML requires a Name column for each feature
    gdf.to_file(path, driver="KML")


def _write_shapefile_zip(path: Path, gdf: gpd.GeoDataFrame) -> None:
    """Write a GeoDataFrame as a zipped Shapefile bundle."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        shp_path = tmp_path / "layer.shp"
        gdf.to_file(shp_path)
        # Zip all shapefile components
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg"]:
                f = shp_path.with_suffix(ext)
                if f.exists():
                    zf.write(f, f.name)


@pytest.fixture(scope="session")
def kochi_square_kml() -> Path:
    """KML file with a ~1km² square polygon near Kochi."""
    with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
        path = Path(f.name)
    _write_kml_file(path, {"plots": [_make_kochi_square()]})
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def kochi_square_zip() -> Path:
    """Zipped Shapefile with a ~1km² square polygon near Kochi."""
    gdf = gpd.GeoDataFrame(
        [{"name": "plot_1", "geometry": _make_kochi_square()}],
        geometry="geometry",
        crs="EPSG:4326",
    )
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        path = Path(f.name)
    _write_shapefile_zip(path, gdf)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def kochi_linestring_kml() -> Path:
    """KML file with a ~1km LineString near Kochi."""
    with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
        path = Path(f.name)
    _write_kml_file(path, {"roads": [_make_kochi_linestring()]})
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def kochi_point_kml() -> Path:
    """KML file with a Point near Kochi."""
    with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
        path = Path(f.name)
    _write_kml_file(path, {"points": [_make_kochi_point()]})
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def geometry_collection_kml() -> Path:
    """KML file with a GeometryCollection."""
    with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
        path = Path(f.name)
    gc = _make_geometry_collection()
    _write_kml_file(path, {"mixed": [gc]})
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def bad_zip_no_prj() -> Path:
    """Zipped Shapefile missing .prj file."""
    gdf = gpd.GeoDataFrame(
        [{"name": "plot_1", "geometry": _make_kochi_square()}],
        geometry="geometry",
        crs=None,  # No CRS
    )
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        path = Path(f.name)
    # Write without .prj
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        shp_path = tmp_path / "layer.shp"
        gdf.to_file(shp_path)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for ext in [".shp", ".shx", ".dbf"]:  # No .prj
                f = shp_path.with_suffix(ext)
                if f.exists():
                    zf.write(f, f.name)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def bad_zip_no_shp() -> Path:
    """Zip file with no .shp inside."""
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        path = Path(f.name)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.txt", "no shapefile here")
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def invalid_polygon_kml() -> Path:
    """KML file with a self-intersecting polygon."""
    with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
        path = Path(f.name)
    _write_kml_file(path, {"bad": [_make_invalid_polygon()]})
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def sydney_square_kml() -> Path:
    """KML file with a ~1km² square near Sydney (southern hemisphere)."""
    with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
        path = Path(f.name)
    _write_kml_file(path, {"plots": [_make_sydney_square()]})
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def multi_feature_kml() -> Path:
    """KML file with multiple features across layers."""
    with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
        path = Path(f.name)
    _write_kml_file(
        path,
        {
            "farms": [_make_kochi_square(), _make_kochi_square()],
            "roads": [_make_kochi_linestring()],
            "markers": [_make_kochi_point()],
        },
    )
    yield path
    path.unlink(missing_ok=True)
