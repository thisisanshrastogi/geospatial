"""KML reader.

Reads every KML layer (Folders become layers via pyogrio).
CRS is always EPSG:4326 per the KML spec.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pyogrio
from pyproj import CRS

from app.domain.errors import InvalidGeoData
from app.domain.models import ParsedDataset, ParsedFeature


class KmlReader:
    """Reads every KML layer (Folders become layers via pyogrio)."""

    def can_read(self, path: Path) -> bool:
        """Return True for `.kml` files (case-insensitive)."""
        return path.suffix.lower() == ".kml"

    def read(self, path: Path) -> ParsedDataset:
        """Read all layers with pyogrio."""
        try:
            layers = pyogrio.list_layers(path)
        except Exception as e:
            raise InvalidGeoData(f"Failed to read KML: {e}") from e

        if layers.size == 0:
            raise InvalidGeoData("KML file has no layers")

        crs = CRS.from_epsg(4326)
        all_features: list[ParsedFeature] = []

        # pyogrio.list_layers returns a 2D array with layer names in first column
        layer_names = layers[:, 0] if layers.ndim == 2 else layers
        for layer_name in layer_names:
            try:
                gdf = gpd.read_file(path, layer=layer_name)
            except Exception as e:
                raise InvalidGeoData(f"Failed to read KML layer '{layer_name}': {e}") from e

            for idx, row in gdf.iterrows():
                geom = row.geometry
                if geom is None:
                    continue
                props = {k: v for k, v in row.items() if k != "geometry"}
                props = self._make_json_safe(props)
                all_features.append(
                    ParsedFeature(
                        index=idx,
                        layer=layer_name,
                        geometry=geom,
                        geometry_type=geom.geom_type,
                        properties=props,
                    )
                )

        return ParsedDataset(crs=crs, features=tuple(all_features))

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
