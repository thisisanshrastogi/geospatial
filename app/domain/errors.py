"""Domain error hierarchy, mapped to HTTP status codes in `app.main`.

Each error carries a stable `code` string so API clients can branch on it
without parsing human-readable messages.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all expected domain failures."""

    code: str = "DOMAIN_ERROR"
    http_status: int = 500


class UnsupportedFileType(DomainError):
    """Raised when the upload extension is not .zip or .kml."""

    code: str = "UNSUPPORTED_FILE_TYPE"
    http_status: int = 400


class FileTooLarge(DomainError):
    """Raised when the upload exceeds `Settings.max_upload_mb`."""

    code: str = "FILE_TOO_LARGE"
    http_status: int = 413


class FileNotFound(DomainError):
    """Raised when a file id does not exist."""

    code: str = "FILE_NOT_FOUND"
    http_status: int = 404


class ProcessingNotComplete(DomainError):
    """Raised when measurements are requested before status is COMPLETED."""

    code: str = "PROCESSING_NOT_COMPLETE"
    http_status: int = 409


class InvalidGeoData(DomainError):
    """Raised when the file parses but its geospatial content is unusable."""

    code: str = "INVALID_GEO_DATA"
    http_status: int = 422


class InvalidStatusTransition(DomainError):
    """Raised on an illegal FileStatus transition."""

    code: str = "INVALID_STATUS_TRANSITION"
    http_status: int = 500
