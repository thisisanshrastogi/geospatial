"""Projection to meter-based CRS before measuring.

WHY: measuring area/length in lat/lon degrees is meaningless — degrees are
not uniform meters. We reproject each feature to its local UTM zone so
`shapely.area` / `shapely.length` return true m² / m.
"""

from __future__ import annotations

from functools import lru_cache

from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from app.domain.ports import ProjectionStrategy


class UtmProjectionStrategy:
    """Strategy: pick the UTM zone containing the geometry's centroid."""

    def choose_crs(self, geom: BaseGeometry, source: CRS) -> CRS:
        """Return the UTM CRS for `geom` based on its centroid."""
        if not source.is_geographic:
            raise ValueError("Source CRS must be geographic for UTM zone selection")

        centroid = geom.centroid
        lon = centroid.x
        lat = centroid.y

        # Calculate UTM zone
        zone = int((lon + 180) / 6) + 1
        hemisphere = "north" if lat >= 0 else "south"

        if hemisphere == "north":
            epsg_code = 32600 + zone  # EPSG:32601-32660
        else:
            epsg_code = 32700 + zone  # EPSG:32701-32760

        return CRS.from_epsg(epsg_code)


class Projector(ProjectionStrategy):
    """Projects geometries to a meter-based CRS (or passes through if already)."""

    def __init__(self, strategy: UtmProjectionStrategy | None = None) -> None:
        self._strategy = strategy or UtmProjectionStrategy()

    def choose_crs(self, geom: BaseGeometry, source: CRS) -> CRS:
        """Delegate to the strategy."""
        return self._strategy.choose_crs(geom, source)

    def to_metric(self, geom: BaseGeometry, source_crs: CRS) -> tuple[BaseGeometry, CRS]:
        """Return (projected_geom, target_crs).

        If source is already projected and in meters, return unchanged.
        Otherwise, choose UTM zone from centroid and transform.
        """
        # Check if already projected in meters
        if source_crs.is_projected:
            axis_info = source_crs.axis_info
            if axis_info and axis_info[0].unit_name == "metre":
                return geom, source_crs

        # Source is geographic - choose UTM zone
        target_crs = self._strategy.choose_crs(geom, source_crs)

        # Transform
        transformer = self._get_transformer(source_crs, target_crs)
        projected_geom = transform(transformer.transform, geom)

        return projected_geom, target_crs

    @staticmethod
    @lru_cache(maxsize=128)
    def _get_transformer(source: CRS, target: CRS) -> Transformer:
        """Get cached transformer with always_xy=True."""
        return Transformer.from_crs(source, target, always_xy=True)
