"""Geometry measurers (Strategy + Null Object + Registry patterns).

- AreaMeasurer: Polygon / MultiPolygon / LinearRing -> area_m2
- LengthMeasurer: LineString / MultiLineString -> length_m
- NoOpMeasurer: Point / MultiPoint -> no measurement (Null Object)
- UnsupportedMeasurer: fallback for anything else; returns a note, never raises
- MeasurerRegistry: dict geom_type -> measurer with graceful fallback
"""

from __future__ import annotations

from pyproj import Geod
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from app.domain.models import Measurement

GEOD_WGS84 = Geod(ellps="WGS84")


class AreaMeasurer:
    """Strategy: measures polygon area in a meter-based CRS."""

    requires_projection: bool = True

    def measure(self, geom: BaseGeometry, source_geom: BaseGeometry | None = None) -> Measurement:
        """Return area measurement with geodesic cross-check.

        `geom` is the projected geometry (in meters).
        `source_geom` is the original geometry in source CRS (typically EPSG:4326),
        used for geodesic cross-check.
        """
        area_m2 = geom.area

        # Geodesic cross-check on WGS84 using source geometry
        geodesic_area = self._geodesic_area(source_geom or geom)

        return Measurement(
            area_m2=area_m2,
            geodesic_area_m2=geodesic_area,
        )

    def _geodesic_area(self, geom: BaseGeometry) -> float | None:
        """Compute geodesic area on WGS84 ellipsoid."""
        try:
            if geom.geom_type in ("Polygon", "MultiPolygon"):
                total = 0.0
                if geom.geom_type == "Polygon":
                    total = self._polygon_area(geom)
                else:
                    for poly in geom.geoms:
                        total += self._polygon_area(poly)
                return abs(total)
        except Exception:
            pass
        return None

    def _polygon_area(self, poly: Polygon) -> float:
        """Calculate geodesic area of a single polygon."""
        oriented = orient(poly, sign=1.0)
        area, _ = GEOD_WGS84.geometry_area_perimeter(oriented)
        return abs(area)


class LengthMeasurer:
    """Strategy: measures linestring length in a meter-based CRS."""

    requires_projection: bool = True

    def measure(self, geom: BaseGeometry, source_geom: BaseGeometry | None = None) -> Measurement:
        """Return length measurement with geodesic cross-check.

        `geom` is the projected geometry (in meters).
        `source_geom` is the original geometry in source CRS (typically EPSG:4326),
        used for geodesic cross-check.
        """
        length_m = geom.length

        # Geodesic cross-check on WGS84 using source geometry
        geodesic_length = self._geodesic_length(source_geom or geom)

        return Measurement(
            length_m=length_m,
            geodesic_length_m=geodesic_length,
        )

    def _geodesic_length(self, geom: BaseGeometry) -> float | None:
        """Compute geodesic length on WGS84 ellipsoid."""
        try:
            if geom.geom_type in ("LineString", "MultiLineString", "LinearRing"):
                total = 0.0
                if geom.geom_type == "LineString":
                    total = self._linestring_length(geom)
                elif geom.geom_type == "MultiLineString":
                    for line in geom.geoms:
                        total += self._linestring_length(line)
                else:  # LinearRing
                    total = self._linestring_length(geom)
                return total
        except Exception:
            pass
        return None

    def _linestring_length(self, line: LineString) -> float:
        """Calculate geodesic length of a single linestring."""
        return GEOD_WGS84.geometry_length(line)


class NoOpMeasurer:
    """Null Object: points have no area/length; returns an empty Measurement."""

    requires_projection: bool = False

    def measure(self, geom: BaseGeometry, source_geom: BaseGeometry | None = None) -> Measurement:
        """Return an empty Measurement."""
        return Measurement(note="Point geometries have no area or length")


class UnsupportedMeasurer:
    """Null Object fallback: never raises; records a human-readable note."""

    requires_projection: bool = False

    def measure(self, geom: BaseGeometry, source_geom: BaseGeometry | None = None) -> Measurement:
        """Return an empty Measurement with a note."""
        geometry_type = geom.geom_type if geom is not None else "Empty geometry"
        return Measurement(note=f"Unsupported geometry type: {geometry_type}")


class MeasurerRegistry:
    """Registry mapping geometry type name -> measurer, with safe fallback."""

    def __init__(self) -> None:
        self._measurers: dict[str, AreaMeasurer | LengthMeasurer | NoOpMeasurer] = {
            "Polygon": AreaMeasurer(),
            "MultiPolygon": AreaMeasurer(),
            "LinearRing": AreaMeasurer(),
            "LineString": LengthMeasurer(),
            "MultiLineString": LengthMeasurer(),
            "Point": NoOpMeasurer(),
            "MultiPoint": NoOpMeasurer(),
        }
        self._fallback = UnsupportedMeasurer()

    def for_geometry_type(
        self, geometry_type: str | None
    ) -> AreaMeasurer | LengthMeasurer | NoOpMeasurer | UnsupportedMeasurer:
        """Return the measurer for `geometry_type` or the fallback."""
        if geometry_type is None:
            return self._fallback
        return self._measurers.get(geometry_type, self._fallback)
