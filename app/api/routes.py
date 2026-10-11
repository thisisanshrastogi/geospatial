"""Thin route handlers: no business logic here.

NOTE: handlers are plain `def`, not `async def`, to avoid blocking the
event loop on geospatial / DB work (Starlette runs them in a threadpool).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.api.deps import (
    get_query_service,
    get_upload_service,
)
from app.api.schemas import (
    ErrorResponse,
    FeatureMeasurementResponse,
    FileInfoResponse,
    FileUploadResponse,
    MeasurementResponse,
    MeasurementsResponse,
)
from app.services.query_service import QueryService
from app.services.upload_service import UploadService

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.post(
    "/",
    response_model=FileUploadResponse,
    status_code=202,
    responses={
        400: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
    },
)
def upload_file(
    file: UploadFile = File(...),
    upload_service: UploadService = Depends(get_upload_service),
) -> FileUploadResponse:
    """Upload a .zip or .kml and start background processing. Returns 202 with file ID."""
    # Read file content to get size
    content = file.file.read()
    size_bytes = len(content)

    # Reset stream position
    from io import BytesIO

    stream = BytesIO(content)

    file_id = upload_service.upload(file.filename or "unknown", stream, size_bytes)

    return FileUploadResponse(id=file_id, filename=file.filename or "unknown", status="PENDING")


@router.get(
    "/{file_id}/",
    response_model=FileInfoResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_file(
    file_id: str,
    query_service: QueryService = Depends(get_query_service),
) -> FileInfoResponse:
    """Get file info and status. Returns 200 for any status, 404 if not found."""
    geo_file = query_service.get_file(file_id)

    return FileInfoResponse(
        id=geo_file.id,
        filename=geo_file.filename,
        file_type=geo_file.file_type,
        status=geo_file.status.value,
        crs=geo_file.crs,
        feature_count=geo_file.feature_count,
        error_message=geo_file.error_message,
        created_at=geo_file.created_at,
        completed_at=geo_file.completed_at,
    )


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def get_measurements(
    file_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    geometry_type: str | None = Query(None),
    query_service: QueryService = Depends(get_query_service),
) -> MeasurementsResponse:
    """Get per-feature measurements. Returns 409 if not COMPLETED."""
    page = query_service.list_measurements(
        file_id,
        limit=limit,
        offset=offset,
        geometry_type=geometry_type,
    )

    results = []
    for record in page.items:
        results.append(
            FeatureMeasurementResponse(
                feature_index=record.feature_index,
                layer=record.layer,
                geometry_type=record.geometry_type,
                crs=record.measurement.measurement_crs,
                properties=record.properties,
                geometry=record.geometry,
                measurement=MeasurementResponse(
                    area_m2=record.measurement.area_m2,
                    length_m=record.measurement.length_m,
                    geodesic_area_m2=record.measurement.geodesic_area_m2,
                    geodesic_length_m=record.measurement.geodesic_length_m,
                    measurement_crs=record.measurement.measurement_crs,
                    note=record.measurement.note,
                ),
            )
        )

    return MeasurementsResponse(
        file_id=file_id,
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        results=results,
    )
