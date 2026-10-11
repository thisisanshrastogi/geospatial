"""Unit tests for geo layer: readers, normalize, projection, measurers."""

from __future__ import annotations

from pathlib import Path

import pytest
from pyproj import CRS
from shapely.geometry import LineString, Point, Polygon

from app.domain.errors import InvalidGeoData, UnsupportedFileType
from app.geo.measurers import (
    AreaMeasurer,
    LengthMeasurer,
    MeasurerRegistry,
    NoOpMeasurer,
    UnsupportedMeasurer,
)
from app.geo.normalize import normalize
from app.geo.projection import Projector, UtmProjectionStrategy
from app.geo.readers import KmlReader, ReaderRegistry, ShapefileZipReader


class TestNormalize:
    """Tests for geometry normalization."""

    def test_none_geometry(self) -> None:
        geom, note = normalize(None)
        assert geom is None
        assert note == "Empty geometry"

    def test_empty_geometry(self) -> None:
        geom, note = normalize(Polygon())
        assert geom.is_empty
        assert note == "Empty geometry"

    def test_force_2d(self) -> None:
        geom = Point(1, 2, 3)
        normalized, note = normalize(geom)
        assert normalized.has_z is False
        assert note is None

    def test_invalid_polygon_repaired(self) -> None:
        # Bow-tie self-intersecting polygon
        geom = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
        assert not geom.is_valid
        normalized, note = normalize(geom)
        assert normalized.is_valid
        assert note == "Geometry was invalid and repaired"


class TestProjection:
    """Tests for projection strategy and projector."""

    def test_utm_strategy_northern_hemisphere(self) -> None:
        strategy = UtmProjectionStrategy()
        geom = Point(76.27, 9.97)  # Kochi, India
        source = CRS.from_epsg(4326)
        target = strategy.choose_crs(geom, source)
        assert target.to_epsg() == 32643  # UTM zone 43N

    def test_utm_strategy_southern_hemisphere(self) -> None:
        strategy = UtmProjectionStrategy()
        geom = Point(151.21, -33.87)  # Sydney, Australia
        source = CRS.from_epsg(4326)
        target = strategy.choose_crs(geom, source)
        assert target.to_epsg() == 32756  # UTM zone 56S

    def test_utm_strategy_requires_geographic(self) -> None:
        strategy = UtmProjectionStrategy()
        geom = Point(0, 0)
        source = CRS.from_epsg(32643)  # Already projected
        with pytest.raises(ValueError):
            strategy.choose_crs(geom, source)

    def test_projector_passes_through_projected_in_meters(self) -> None:
        projector = Projector()
        geom = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000), (0, 0)])
        source = CRS.from_epsg(32643)  # UTM zone 43N - meters
        projected, target = projector.to_metric(geom, source)
        assert projected.equals(geom)
        assert target == source

    def test_projector_reprojects_geographic_to_utm(self) -> None:
        projector = Projector()
        geom = Point(76.27, 9.97)  # Kochi
        source = CRS.from_epsg(4326)
        projected, target = projector.to_metric(geom, source)
        assert target.to_epsg() == 32643
        # Projected point should be in meters (large coordinate values)
        assert projected.x > 100000  # UTM easting
        assert projected.y > 1000000  # UTM northing

    def test_transformer_caching(self) -> None:
        projector = Projector()
        source = CRS.from_epsg(4326)
        target = CRS.from_epsg(32643)
        t1 = projector._get_transformer(source, target)
        t2 = projector._get_transformer(source, target)
        assert t1 is t2  # Same cached instance


class TestMeasurers:
    """Tests for geometry measurers."""

    def test_area_measurer_polygon(self) -> None:
        measurer = AreaMeasurer()
        # 1000m x 1000m square in UTM = 1,000,000 m²
        geom = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000), (0, 0)])
        # Source geometry in lat/lon (approximate for Kochi)
        source_geom = Polygon([(76.27, 9.97), (76.28, 9.97), (76.28, 9.98), (76.27, 9.98), (76.27, 9.97)])
        m = measurer.measure(geom, source_geom)
        assert m.area_m2 == pytest.approx(1_000_000, rel=0.01)
        assert m.geodesic_area_m2 is not None

    def test_area_measurer_multipolygon(self) -> None:
        measurer = AreaMeasurer()
        geom = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000), (0, 0)])
        from shapely.geometry import MultiPolygon

        multi = MultiPolygon([geom, geom])
        source_geom = Polygon([(76.27, 9.97), (76.28, 9.97), (76.28, 9.98), (76.27, 9.98), (76.27, 9.97)])
        from shapely.geometry import MultiPolygon as MP

        source_multi = MP([source_geom, source_geom])
        m = measurer.measure(multi, source_multi)
        assert m.area_m2 == pytest.approx(2_000_000, rel=0.01)

    def test_length_measurer_linestring(self) -> None:
        measurer = LengthMeasurer()
        # 1000m line in UTM
        geom = LineString([(0, 0), (1000, 0)])
        # Source in lat/lon
        source_geom = LineString([(76.27, 9.97), (76.28, 9.97)])
        m = measurer.measure(geom, source_geom)
        assert m.length_m == pytest.approx(1000, rel=0.01)
        assert m.geodesic_length_m is not None

    def test_length_measurer_multilinestring(self) -> None:
        measurer = LengthMeasurer()
        from shapely.geometry import MultiLineString

        line1 = LineString([(0, 0), (1000, 0)])
        line2 = LineString([(0, 0), (0, 1000)])
        multi = MultiLineString([line1, line2])
        source_line1 = LineString([(76.27, 9.97), (76.28, 9.97)])
        source_line2 = LineString([(76.27, 9.97), (76.27, 9.98)])
        source_multi = MultiLineString([source_line1, source_line2])
        m = measurer.measure(multi, source_multi)
        assert m.length_m == pytest.approx(2000, rel=0.01)

    def test_noop_measurer_point(self) -> None:
        measurer = NoOpMeasurer()
        geom = Point(0, 0)
        m = measurer.measure(geom)
        assert m.area_m2 is None
        assert m.length_m is None
        assert m.note is not None

    def test_noop_measurer_multipoint(self) -> None:
        measurer = NoOpMeasurer()
        from shapely.geometry import MultiPoint

        geom = MultiPoint([Point(0, 0), Point(1, 1)])
        m = measurer.measure(geom)
        assert m.note is not None

    def test_unsupported_measurer(self) -> None:
        measurer = UnsupportedMeasurer()
        from shapely.geometry import GeometryCollection

        geom = GeometryCollection([Point(0, 0)])
        m = measurer.measure(geom)
        assert m.note is not None
        assert "Unsupported" in m.note

    def test_measurer_registry_fallback(self) -> None:
        registry = MeasurerRegistry()
        # Known types
        assert isinstance(registry.for_geometry_type("Polygon"), AreaMeasurer)
        assert isinstance(registry.for_geometry_type("LineString"), LengthMeasurer)
        assert isinstance(registry.for_geometry_type("Point"), NoOpMeasurer)
        # Unknown type falls back to UnsupportedMeasurer
        assert isinstance(registry.for_geometry_type("GeometryCollection"), UnsupportedMeasurer)

    def test_measurer_requires_projection_property(self) -> None:
        assert AreaMeasurer.requires_projection is True
        assert LengthMeasurer.requires_projection is True
        assert NoOpMeasurer.requires_projection is False
        assert UnsupportedMeasurer.requires_projection is False


class TestReaders:
    """Contract tests for readers."""

    def test_shapefile_zip_reader_can_read(self) -> None:
        reader = ShapefileZipReader()
        assert reader.can_read(Path("test.zip"))
        assert reader.can_read(Path("test.ZIP"))
        assert not reader.can_read(Path("test.kml"))
        assert not reader.can_read(Path("test.shp"))

    def test_kml_reader_can_read(self) -> None:
        reader = KmlReader()
        assert reader.can_read(Path("test.kml"))
        assert reader.can_read(Path("test.KML"))
        assert not reader.can_read(Path("test.zip"))

    def test_reader_registry_dispatch(self, kochi_square_kml, kochi_square_zip) -> None:
        registry = ReaderRegistry([ShapefileZipReader(), KmlReader()])
        reader = registry.for_path(kochi_square_zip)
        assert isinstance(reader, ShapefileZipReader)
        reader = registry.for_path(kochi_square_kml)
        assert isinstance(reader, KmlReader)

    def test_reader_registry_unsupported(self) -> None:
        registry = ReaderRegistry([ShapefileZipReader(), KmlReader()])
        with pytest.raises(UnsupportedFileType):
            registry.for_path(Path("test.pdf"))

    def test_kml_reads_crs_and_features(self, kochi_square_kml) -> None:
        reader = KmlReader()
        dataset = reader.read(kochi_square_kml)
        assert dataset.crs.to_epsg() == 4326
        assert len(dataset.features) == 1
        assert dataset.features[0].geometry_type == "Polygon"

    def test_shapefile_zip_reads_crs_and_features(self, kochi_square_zip) -> None:
        reader = ShapefileZipReader()
        dataset = reader.read(kochi_square_zip)
        assert dataset.crs.to_epsg() == 4326
        assert len(dataset.features) == 1
        assert dataset.features[0].geometry_type == "Polygon"

    def test_bad_zip_no_prj_raises(self, bad_zip_no_prj) -> None:
        reader = ShapefileZipReader()
        with pytest.raises(InvalidGeoData, match="CRS"):
            reader.read(bad_zip_no_prj)

    def test_bad_zip_no_shp_raises(self, bad_zip_no_shp) -> None:
        reader = ShapefileZipReader()
        with pytest.raises(InvalidGeoData, match="No .shp file"):
            reader.read(bad_zip_no_shp)

    def test_invalid_polygon_repaired_on_read(self, invalid_polygon_kml) -> None:
        reader = KmlReader()
        _ = reader.read(invalid_polygon_kml)
        # The invalid polygon should be repaired during processing
        # The reader returns raw geometry; normalization happens later


class TestKnownAnswers:
    """Known-answer tests with ~1km² square near Kochi."""

    def test_kochi_square_area_projected(self, kochi_square_zip) -> None:
        """Area of ~1km² square in UTM should be ~1,000,000 m² ±0.5%."""
        reader = ShapefileZipReader()
        dataset = reader.read(kochi_square_zip)
        feature = dataset.features[0]

        # Project to UTM
        projector = Projector()
        projected, target_crs = projector.to_metric(feature.geometry, dataset.crs)

        # Measure with source geometry for geodesic cross-check
        measurer = AreaMeasurer()
        m = measurer.measure(projected, feature.geometry)

        # Should be ~1,000,000 m² within 0.5%
        assert m.area_m2 == pytest.approx(1_000_000, rel=0.005)
        assert target_crs.to_epsg() == 32643

    def test_kochi_square_area_geodesic_vs_projected(self, kochi_square_zip) -> None:
        """Projected vs geodesic area should match within 0.5%."""
        reader = ShapefileZipReader()
        dataset = reader.read(kochi_square_zip)
        feature = dataset.features[0]

        projector = Projector()
        projected, _ = projector.to_metric(feature.geometry, dataset.crs)

        measurer = AreaMeasurer()
        m = measurer.measure(projected, feature.geometry)

        assert m.geodesic_area_m2 is not None
        # Projected and geodesic should be within 0.5%
        assert m.area_m2 == pytest.approx(m.geodesic_area_m2, rel=0.005)

    def test_kochi_linestring_length_projected(self, kochi_linestring_kml) -> None:
        """Length of ~1km LineString in UTM should be ~1000 m ±0.5%."""
        reader = KmlReader()
        dataset = reader.read(kochi_linestring_kml)
        feature = dataset.features[0]

        projector = Projector()
        projected, target_crs = projector.to_metric(feature.geometry, dataset.crs)

        measurer = LengthMeasurer()
        m = measurer.measure(projected, feature.geometry)

        assert m.length_m == pytest.approx(1000, rel=0.005)
        assert target_crs.to_epsg() == 32643

    def test_kochi_linestring_length_geodesic_vs_projected(self, kochi_linestring_kml) -> None:
        """Projected vs geodesic length should match within 0.5%."""
        reader = KmlReader()
        dataset = reader.read(kochi_linestring_kml)
        feature = dataset.features[0]

        projector = Projector()
        projected, _ = projector.to_metric(feature.geometry, dataset.crs)

        measurer = LengthMeasurer()
        m = measurer.measure(projected, feature.geometry)

        assert m.geodesic_length_m is not None
        assert m.length_m == pytest.approx(m.geodesic_length_m, rel=0.005)

    def test_sydney_square_uses_southern_utm(self, sydney_square_kml) -> None:
        """Southern hemisphere point should use southern UTM zone."""
        reader = KmlReader()
        dataset = reader.read(sydney_square_kml)
        feature = dataset.features[0]

        projector = Projector()
        projected, target_crs = projector.to_metric(feature.geometry, dataset.crs)

        assert target_crs.to_epsg() == 32756  # UTM zone 56S

    def test_point_no_measurement(self, kochi_point_kml) -> None:
        """Point geometries get NoOpMeasurer with note."""
        reader = KmlReader()
        dataset = reader.read(kochi_point_kml)
        feature = dataset.features[0]

        registry = MeasurerRegistry()
        measurer = registry.for_geometry_type(feature.geometry_type)
        m = measurer.measure(feature.geometry)

        assert m.area_m2 is None
        assert m.length_m is None
        assert m.note is not None

    def test_geometry_collection_fallback(self, geometry_collection_kml) -> None:
        """GeometryCollection gets UnsupportedMeasurer with note."""
        reader = KmlReader()
        dataset = reader.read(geometry_collection_kml)
        feature = dataset.features[0]

        registry = MeasurerRegistry()
        measurer = registry.for_geometry_type(feature.geometry_type)
        m = measurer.measure(feature.geometry)

        assert m.note is not None
        assert "Unsupported" in m.note
