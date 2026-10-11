"""Upload service: validate -> store -> create PENDING record -> enqueue."""

from __future__ import annotations

from collections.abc import Callable
from typing import BinaryIO

from app.config import Settings
from app.domain.errors import FileTooLarge, UnsupportedFileType
from app.domain.models import GeoFile
from app.domain.ports import FileRepository, FileStorage, TaskDispatcher
from app.domain.status import FileStatus


class UploadService:
    """Orchestrates the POST /api/files/ flow (thin over ports)."""

    def __init__(
        self,
        storage: FileStorage,
        file_repo: FileRepository,
        task_dispatcher: TaskDispatcher,
        settings: Settings,
        job_factory: Callable[[str], Callable[[], None]],
    ) -> None:
        self._storage = storage
        self._file_repo = file_repo
        self._task_dispatcher = task_dispatcher
        self._settings = settings
        self._job_factory = job_factory

    def upload(self, filename: str, stream: BinaryIO, size_bytes: int) -> str:
        """Validate, store, record, and enqueue; return the new file id."""
        # 1. Validate extension
        ext = filename.lower().split(".")[-1] if "." in filename else ""
        if ext not in ("zip", "kml"):
            if ext == "shp":
                raise UnsupportedFileType("Upload the full shapefile bundle as a .zip")
            raise UnsupportedFileType(f"Unsupported file type: .{ext}. Only .zip and .kml are allowed.")

        # 2. Validate size
        max_bytes = self._settings.max_upload_mb * 1024 * 1024
        if size_bytes > max_bytes:
            raise FileTooLarge(
                f"File size {size_bytes} bytes exceeds limit of {max_bytes} bytes "
                f"({self._settings.max_upload_mb} MB)"
            )

        # 3. Generate file ID and store
        from uuid import uuid4

        file_id = str(uuid4())
        stored_path = self._storage.save(file_id, filename, stream)

        # 4. Determine file type
        file_type = "shapefile_zip" if ext == "zip" else "kml"

        # 5. Create PENDING GeoFile
        geo_file = GeoFile(
            id=file_id,
            filename=filename,
            file_type=file_type,
            stored_path=str(stored_path),
            status=FileStatus.PENDING,
        )
        self._file_repo.add(geo_file)
        self._file_repo.flush()
        self._file_repo.commit()

        # 6. Enqueue processing
        self._task_dispatcher.enqueue_processing(
            file_id,
            lambda: self._job_factory(file_id),
        )

        return file_id
