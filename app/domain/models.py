"""Domain dataclasses: pure-Python types with no framework dependencies.

These are frozen (immutable) so pipeline stages cannot accidentally mutate
each other's outputs; state changes produce new values instead.
GeoFile is the only mutable one (status transitions); the rest are frozen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.domain.errors import InvalidStatusTransition
from app.domain.status import FileStatus, can_transition


@dataclass(frozen=True)
class Page[T]:
    """Paginated result container."""

    items: tuple[T, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class ParsedFeature:
    """One raw feature as read from a geospatial source."""

    index: int
    layer: str
    geometry: BaseGeometry | None
    geometry_type: str | None
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedDataset:
    """All features read from one upload, plus source CRS (never None)."""

    crs: CRS
    features: tuple[ParsedFeature, ...] = ()


@dataclass(frozen=True)
class Measurement:
    """Measurement result for one feature.

    All values are in meters/m². Geodesic values are cross-checks on WGS84 ellipsoid.
    """

    area_m2: float | None = None
    length_m: float | None = None
    geodesic_area_m2: float | None = None
    geodesic_length_m: float | None = None
    measurement_crs: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class FeatureRecord:
    """A persisted per-feature row combining geometry + measurement."""

    file_id: str
    feature_index: int
    layer: str
    geometry_type: str | None
    geometry: dict[str, Any]  # GeoJSON in source CRS
    properties: dict[str, Any]
    measurement: Measurement
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class GeoFile:
    """A single uploaded file and its processing state.

    This is the only mutable domain object — status transitions are controlled
    through explicit methods that validate the state machine.
    """

    __slots__ = (
        "id",
        "filename",
        "file_type",
        "stored_path",
        "status",
        "crs",
        "feature_count",
        "error_message",
        "created_at",
        "completed_at",
    )

    def __init__(
        self,
        *,
        id: str | None = None,
        filename: str,
        file_type: str,
        stored_path: str,
        status: FileStatus = FileStatus.PENDING,
        crs: str | None = None,
        feature_count: int | None = None,
        error_message: str | None = None,
        created_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        self.id = id or str(uuid4())
        self.filename = filename
        self.file_type = file_type  # "shapefile_zip" or "kml"
        self.stored_path = stored_path
        self.status = status
        self.crs = crs
        self.feature_count = feature_count
        self.error_message = error_message
        self.created_at = created_at or datetime.now(UTC)
        self.completed_at = completed_at

    def _ensure_transition(self, target: FileStatus) -> None:
        """Validate that the current -> target transition is legal."""
        if not can_transition(self.status, target):
            raise InvalidStatusTransition(f"Illegal status transition: {self.status} -> {target}")

    def start_processing(self) -> None:
        """Transition PENDING -> PROCESSING."""
        self._ensure_transition(FileStatus.PROCESSING)
        self.status = FileStatus.PROCESSING

    def mark_completed(self, crs: str, feature_count: int) -> None:
        """Transition PROCESSING -> COMPLETED."""
        self._ensure_transition(FileStatus.COMPLETED)
        self.status = FileStatus.COMPLETED
        self.crs = crs
        self.feature_count = feature_count
        self.completed_at = datetime.now(UTC)

    def mark_failed(self, message: str) -> None:
        """Transition PENDING|PROCESSING -> FAILED."""
        self._ensure_transition(FileStatus.FAILED)
        self.status = FileStatus.FAILED
        self.error_message = message
        self.completed_at = datetime.now(UTC)
