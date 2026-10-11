"""Reader registry: tries each GeoFileReader until one accepts the file.

ROLE: Registry pattern — ProcessingService asks the registry instead of
hard-coding zip-vs-KML branching, so new formats plug in by registration.
"""

from __future__ import annotations

from pathlib import Path

from app.domain.errors import UnsupportedFileType
from app.domain.models import ParsedDataset
from app.domain.ports import GeoFileReader


class ReaderRegistry:
    """Ordered collection of readers with first-match dispatch."""

    def __init__(self, readers: list[GeoFileReader] | None = None) -> None:
        self._readers: list[GeoFileReader] = list(readers) if readers else []

    def register(self, reader: GeoFileReader) -> None:
        """Add a reader to the end of the lookup chain."""
        self._readers.append(reader)

    def for_path(self, path: Path) -> GeoFileReader:
        """Return the first reader whose `can_read` returns True for `path`."""
        for reader in self._readers:
            if reader.can_read(path):
                return reader
        raise UnsupportedFileType(f"No reader for file: {path.name}")

    def read(self, path: Path) -> ParsedDataset:
        """Read via the first matching reader."""
        reader = self.for_path(path)
        return reader.read(path)
