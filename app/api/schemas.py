"""Pydantic request/response schemas matching spec §12."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Uniform error body: {"error": {"code": ..., "message": ...}}."""

    error: dict[str, str]


class FileUploadResponse(BaseModel):
    """202 response for POST /api/files/."""

    id: str
    filename: str
    status: str


class FileInfoResponse(BaseModel):
    """200 response for GET /api/files/{id}/."""

    id: str
    filename: str
    file_type: str
    status: str
    crs: str | None = None
    feature_count: int | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class MeasurementResponse(BaseModel):
    """Per-feature measurement block."""

    area_m2: float | None = None
    length_m: float | None = None
    geodesic_area_m2: float | None = None
    geodesic_length_m: float | None = None
    measurement_crs: str | None = None
    note: str | None = None


class FeatureMeasurementResponse(BaseModel):
    """Single feature measurement result."""

    feature_index: int
    layer: str
    geometry_type: str | None
    crs: str | None
    properties: dict[str, Any]
    geometry: dict[str, Any] | None
    measurement: MeasurementResponse


class MeasurementsResponse(BaseModel):
    """Paginated measurements response for GET /api/files/{id}/measurements/."""

    file_id: str
    total: int
    limit: int
    offset: int
    results: list[FeatureMeasurementResponse]
