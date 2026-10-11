"""Read-side query service for the GET endpoints."""

from __future__ import annotations

from app.domain.errors import FileNotFound, ProcessingNotComplete
from app.domain.models import FeatureRecord, GeoFile, Page
from app.domain.ports import FeatureRepository, FileRepository
from app.domain.status import FileStatus


class QueryService:
    """Serves file info and paginated measurements."""

    def __init__(
        self,
        file_repo: FileRepository,
        feature_repo: FeatureRepository,
    ) -> None:
        self._file_repo = file_repo
        self._feature_repo = feature_repo

    def get_file(self, file_id: str) -> GeoFile:
        """Return file info or raise FileNotFound."""
        geo_file = self._file_repo.get(file_id)
        if geo_file is None:
            raise FileNotFound(f"File {file_id} not found")
        return geo_file

    def list_measurements(
        self,
        file_id: str,
        *,
        limit: int,
        offset: int,
        geometry_type: str | None,
    ) -> Page[FeatureRecord]:
        """Return measurements page; 409 unless COMPLETED."""
        geo_file = self._file_repo.get(file_id)
        if geo_file is None:
            raise FileNotFound(f"File {file_id} not found")

        if geo_file.status != FileStatus.COMPLETED:
            raise ProcessingNotComplete(f"File is {geo_file.status.value}, measurements not available")

        # Clamp limit
        limit = min(max(limit, 1), 1000)
        offset = max(offset, 0)

        return self._feature_repo.list(
            file_id,
            limit=limit,
            offset=offset,
            geometry_type=geometry_type,
        )
