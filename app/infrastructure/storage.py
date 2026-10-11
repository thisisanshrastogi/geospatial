"""Local filesystem storage adapter."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO


class LocalFileStorage:
    """Adapter: saves upload streams under `uploads/{file_id}/`."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, file_id: str, filename: str, stream: BinaryIO) -> Path:
        """Save `stream` and return the stored path."""
        # Sanitize filename - keep only safe characters
        safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ")
        safe_name = safe_name.strip() or "upload"
        if not safe_name.lower().endswith((".zip", ".kml")):
            safe_name += ".bin"

        file_dir = self._base_dir / file_id
        file_dir.mkdir(parents=True, exist_ok=True)

        file_path = file_dir / safe_name
        with file_path.open("wb") as f:
            # Stream in chunks to handle large files
            while chunk := stream.read(8192):
                f.write(chunk)

        return file_path
