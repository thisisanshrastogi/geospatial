"""Geometry normalization: force_2d + make_valid + empty handling."""

from __future__ import annotations

from shapely import force_2d, is_valid, make_valid
from shapely.geometry.base import BaseGeometry


def normalize(geom: BaseGeometry | None) -> tuple[BaseGeometry | None, str | None]:
    """Drop Z, repair invalid geometries; return (geometry, note).

    - If geom is None: return (None, "Empty geometry")
    - If geom is empty: return (geom, "Empty geometry")
    - Drop Z coordinate with force_2d
    - If not valid: repair with make_valid, note "Geometry was invalid and repaired"
    """
    if geom is None:
        return None, "Empty geometry"

    if geom.is_empty:
        return geom, "Empty geometry"

    # Drop Z values
    geom_2d = force_2d(geom)

    # Repair invalid geometries
    if not is_valid(geom_2d):
        geom_fixed = make_valid(geom_2d)
        return geom_fixed, "Geometry was invalid and repaired"

    return geom_2d, None
