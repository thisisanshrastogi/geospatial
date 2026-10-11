"""Port interfaces (typing.Protocol) — the dependency-inversion boundary.

ROLE: `services` depend ONLY on these Protocols, never on SQLAlchemy,
file I/O, or FastAPI. `infrastructure` provides the concrete adapters
(wired together in `app.api.deps`, the composition root).

We use Protocol instead of ABC so any duck-typed object (including simple
test fakes) satisfies the interface without inheritance.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import BinaryIO, Protocol

from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.domain.models import FeatureRecord, GeoFile, Measurement, Page, ParsedDataset
from app.domain.status import FileStatus


class GeoFileReader(Protocol):
    """Reads one upload file into a ParsedDataset (Strategy participants)."""

    def can_read(self, path: Path) -> bool:
        """Return True if this reader handles the file at `path`."""
        ...

    def read(self, path: Path) -> ParsedDataset:
        """Parse the file; raises InvalidGeoData on unusable content."""
        ...


class ProjectionStrategy(Protocol):
    """Chooses a metric target CRS for one geometry (Strategy pattern)."""

    def choose_crs(self, geom: BaseGeometry, source: CRS) -> CRS:
        """Return the projected CRS (in meters) to measure `geom` in."""
        ...

    def to_metric(self, geom: BaseGeometry, source_crs: CRS) -> tuple[BaseGeometry, CRS]:
        """Return (projected_geom, target_crs) for metric measurement."""
        ...


class GeometryMeasurer(Protocol):
    """Measures one (already projected, if needed) geometry (Strategy pattern)."""

    @property
    def requires_projection(self) -> bool:
        """True if measurement needs a meter-based projected CRS first."""
        ...

    def measure(self, geom: BaseGeometry, source_geom: BaseGeometry | None = None) -> Measurement:
        """Return the measurement; never raises on odd geometry types.

        `source_geom` is the original geometry in the source CRS (typically EPSG:4326),
        used for geodesic cross-check. If None, `geom` is used for both.
        """
        ...


class FileStorage(Protocol):
    """Persists raw upload bytes (Adapter over the local filesystem)."""

    def save(self, file_id: str, filename: str, stream: BinaryIO) -> Path:
        """Save `stream` under `uploads/{file_id}/` and return the path."""
        ...


class TaskDispatcher(Protocol):
    """Enqueues async processing (Adapter over Starlette BackgroundTasks)."""

    def enqueue_processing(self, file_id: str, job_factory: Callable[[], Callable[[], None]]) -> None:
        """Schedule a background job for `file_id`.

        `job_factory` is a callable that returns a no-arg function to run in the background.
        """
        ...


class FileRepository(Protocol):
    """Persistence for GeoFile aggregates (Repository pattern)."""

    def add(self, f: GeoFile) -> None:
        """Insert a new GeoFile."""
        ...

    def flush(self) -> None:
        """Flush pending changes to the database."""
        ...

    def commit(self) -> None:
        """Commit the current transaction."""
        ...

    def get(self, file_id: str) -> GeoFile | None:
        """Return the GeoFile or None if unknown."""
        ...

    def save(self, f: GeoFile) -> None:
        """Persist state changes to an existing GeoFile."""
        ...

    def list_by_status(self, status: FileStatus) -> Sequence[GeoFile]:
        """Return all files with the given status (for startup recovery)."""
        ...


class FeatureRepository(Protocol):
    """Persistence for per-feature measurement rows (Repository pattern)."""

    def add_many(self, file_id: str, records: Sequence[FeatureRecord]) -> None:
        """Bulk-insert all feature records for one file."""
        ...

    def list(
        self,
        file_id: str,
        *,
        limit: int,
        offset: int,
        geometry_type: str | None,
    ) -> Page[FeatureRecord]:
        """Return one paginated, optionally geometry-type-filtered page."""
        ...
