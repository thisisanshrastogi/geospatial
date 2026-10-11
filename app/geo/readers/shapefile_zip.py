"""Shapefile (.zip bundle) reader.

Reads a zipped Shapefile bundle; validates .shx/.dbf/.prj siblings.
Extracts to temp directory with zip-slip protection.
"""

from __future__ import annotations

import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.domain.errors import InvalidGeoData
from app.domain.models import ParsedDataset, ParsedFeature


class ShapefileZipReader:
    """Reads a zipped Shapefile bundle; validates .shx/.dbf/.prj siblings."""

    def can_read(self, path: Path) -> bool:
        """Return True for `.zip` files (case-insensitive)."""
        return path.suffix.lower() == ".zip"

    def read(self, path: Path) -> ParsedDataset:
        """Extract, validate, and read all .shp layers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            self._extract_zip_safely(path, tmp_path)
            shp_files = list(tmp_path.rglob("*.shp"))
            if not shp_files:
                raise InvalidGeoData("No .shp file found in zip")

            all_features: list[ParsedFeature] = []
            dataset_crs: CRS | None = None

            for shp_file in shp_files:
                layer_name = shp_file.stem
                features, layer_crs = self._read_shapefile(shp_file, layer_name)
                if dataset_crs is None:
                    dataset_crs = layer_crs
                elif layer_crs != dataset_crs:
                    raise InvalidGeoData(
                        f"Multiple shapefiles with different CRS: {dataset_crs} vs {layer_crs}"
                    )
                all_features.extend(features)

            if dataset_crs is None:
                raise InvalidGeoData("Shapefile has no CRS (.prj missing); cannot measure safely")

            return ParsedDataset(crs=dataset_crs, features=tuple(all_features))

    def _extract_zip_safely(self, zip_path: Path, dest: Path) -> None:
        """Extract zip with zip-slip protection."""
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                member_path = dest / member.filename
                try:
                    member_path.resolve().relative_to(dest.resolve())
                except ValueError:
                    raise InvalidGeoData(f"Zip-slip attempt detected: {member.filename}") from None
            zf.extractall(dest)

    def _read_shapefile(self, shp_file: Path, layer_name: str) -> tuple[list[ParsedFeature], CRS]:
        """Read a single shapefile and return features + CRS."""
        base = shp_file.with_suffix("")
        if not base.with_suffix(".shx").exists():
            raise InvalidGeoData(f"Missing .shx file for {shp_file.name}")
        if not base.with_suffix(".dbf").exists():
            raise InvalidGeoData(f"Missing .dbf file for {shp_file.name}")
        if not base.with_suffix(".prj").exists():
            raise InvalidGeoData("Shapefile has no CRS (.prj missing); cannot measure safely")

        gdf = gpd.read_file(shp_file)
        if gdf.crs is None:
            raise InvalidGeoData("Shapefile has no CRS (.prj missing); cannot measure safely")

        crs = CRS.from_user_input(gdf.crs)
        features: list[ParsedFeature] = []
        for idx, row in gdf.iterrows():
            geom = row.geometry
            if not isinstance(geom, BaseGeometry):
                geometry: BaseGeometry | None = None
                geometry_type: str | None = None
            else:
                geometry = geom
                geometry_type = geom.geom_type
            props = {k: v for k, v in row.items() if k != "geometry"}
            props = self._make_json_safe(props)
            features.append(
                ParsedFeature(
                    index=idx,
                    layer=layer_name,
                    geometry=geometry,
                    geometry_type=geometry_type,
                    properties=props,
                )
            )
        return features, crs

    def _make_json_safe(self, props: dict[str, Any]) -> dict[str, Any]:
        """Convert props to JSON-safe types."""
        safe: dict[str, Any] = {}
        for k, v in props.items():
            if isinstance(v, (datetime, date)):
                safe[k] = v.isoformat()
            elif isinstance(v, np.floating):
                safe[k] = float(v) if not np.isnan(v) else None
            elif isinstance(v, np.integer):
                safe[k] = int(v)
            elif isinstance(v, np.bool_):
                safe[k] = bool(v)
            elif v is None or isinstance(v, (str, int, float, bool)):
                safe[k] = v
            else:
                safe[k] = str(v)
        return safe
