# Geospatial File Measurement API

Upload a zipped Shapefile (`.zip`) or KML (`.kml`); get per-feature area/length
measurements back. Polygon → area, LineString → length, Point → no measurement.
All measurements are computed in a meter-based projected CRS (per-feature UTM),
never in lat/lon degrees.

## Quickstart

### Local with uv (Recommended)

```bash
# 1. Install uv: https://docs.astral.sh/uv/getting-started/installation/
# 2. From the project root:
cp .env.example .env
uv sync
uv run uvicorn app.main:app --reload
```

- API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

### Docker

```bash
docker compose up --build
```

- API docs: http://localhost:8000/docs

### Development

```bash
# Run tests
uv run pytest

# Format code
uv run ruff format app tests

# Lint
uv run ruff check app tests

# Type check
uv run mypy app
```

## Smoke Test (30 seconds)

```bash
# 1. Upload a KML file
curl -X POST "http://localhost:8000/api/files/" \
  -F "file=@test_docker.kml"

# Response: {"id": "<uuid>", "filename": "test.kml", "status": "PENDING"}

# 2. Poll for completion
FILE_ID=<uuid from above>
curl "http://localhost:8000/api/files/$FILE_ID"
# Wait until status is "COMPLETED"

# 3. Get measurements
curl "http://localhost:8000/api/files/$FILE_ID/measurements"
```

A sample KML file `test_docker.kml` is included in the project root for testing.

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `sqlite:///./geospatial.db` | SQLAlchemy database URL |
| `UPLOAD_DIR` | `./uploads` | Raw upload storage |
| `MAX_UPLOAD_MB` | `50` | Upload size limit (MB) |
| `LOG_LEVEL` | `INFO` | Logging level |

Copy `.env.example` to `.env` and adjust as needed.

## API Reference

### POST /api/files/

Upload a `.zip` (Shapefile bundle) or `.kml` file and start background processing.

**Request:** `multipart/form-data` with `file` field

**Success (202):**
```json
{"id": "9b1c...", "filename": "farms.zip", "status": "PENDING"}
```

**Errors:**
- 400: Unsupported file type (only `.zip` and `.kml` allowed; raw `.shp` rejected with message "Upload the full shapefile bundle as a .zip")
- 413: File exceeds `MAX_UPLOAD_MB`

### GET /api/files/{id}/

File info and current status. Poll this endpoint after upload.

**Success (200):**
```json
{
  "id": "9b1c...",
  "filename": "farms.zip",
  "file_type": "shapefile_zip",
  "status": "COMPLETED",
  "crs": "EPSG:4326",
  "feature_count": 120,
  "error_message": null,
  "created_at": "2026-10-10T12:00:00Z",
  "completed_at": "2026-10-10T12:00:05Z"
}
```

**Errors:**
- 404: Unknown file ID

### GET /api/files/{id}/measurements/

Per-feature measurements. Supports pagination and geometry type filter.

**Query params:**
- `limit` (default 100, max 1000)
- `offset` (default 0)
- `geometry_type` (optional filter: `Polygon`, `LineString`, `Point`, `MultiPolygon`, `MultiLineString`, `MultiPoint`, `LinearRing`, `GeometryCollection`)

**Success (200):**
```json
{
  "file_id": "9b1c...",
  "total": 120,
  "limit": 100,
  "offset": 0,
  "results": [{
    "feature_index": 0,
    "layer": "farms",
    "geometry_type": "Polygon",
    "crs": "EPSG:4326",
    "properties": {"name": "Plot A"},
    "geometry": {"type": "Polygon", "coordinates": [[[76.27, 9.97], ...]]},
    "measurement": {
      "area_m2": 10234.5,
      "length_m": null,
      "geodesic_area_m2": 10231.9,
      "geodesic_length_m": null,
      "measurement_crs": "EPSG:32643",
      "note": null
    }
  }]
}
```

**Errors:**
- 404: Unknown file ID
- 409: File not COMPLETED (includes current status in body)

### Error Format (all errors)
```json
{"error": {"code": "PROCESSING_NOT_COMPLETE", "message": "File is still PROCESSING"}}
```

### Status Codes
| Code | Meaning |
|------|---------|
| 200 | OK |
| 202 | Accepted (processing started) |
| 400 | Bad request (unsupported file type) |
| 404 | Not found |
| 409 | Conflict (measurements requested before COMPLETED) |
| 413 | Payload too large |
| 422 | Unprocessable entity (invalid geo data) |
| 500 | Internal server error |

## Supported File Formats

### Shapefile (`.zip`)
- Must contain `.shp`, `.shx`, `.dbf`, `.prj` files (`.cpg` optional)
- Multiple shapefiles in one zip supported (each becomes a layer)
- **`.prj` is required** - no CRS guessing for safety
- Zip-slip protection built in

### KML (`.kml`)
- All layers (folders) read automatically
- CRS is always EPSG:4326 per KML spec
- Multiple folders → multiple layers

### Future Formats (Planned)
- GeoJSON, KMZ, GeoPackage

## Supported Geometry Types & Measurements

| Geometry Type | Area (m²) | Length (m) | Notes |
|---------------|-----------|------------|-------|
| Polygon | ✓ | ✗ | |
| MultiPolygon | ✓ | ✗ | Sum of all polygons |
| LineString | ✗ | ✓ | |
| MultiLineString | ✗ | ✓ | Sum of all lines |
| LinearRing | ✓ | ✓ | Treated as polygon for area |
| Point | ✗ | ✗ | No measurement |
| MultiPoint | ✗ | ✗ | No measurement |
| GeometryCollection | ✗ | ✗ | Note: "Unsupported geometry type" |

## Measurement Details

### Projected Measurement
- Computed in the feature's UTM zone (meters)
- Uses `shapely.area` / `shapely.length` on projected geometry
- Accurate for local features

### Geodesic Cross-Check
- Computed on WGS84 ellipsoid using `pyproj.Geod`
- Provides `geodesic_area_m2` / `geodesic_length_m`
- Compare with projected for validation (±0.5% typical)

### Measurement CRS
- Recorded per feature as `measurement_crs` (e.g., `EPSG:32643`)
- If source CRS is already projected in meters, uses that CRS
- Geographic CRS → projected to UTM zone from centroid

## Example Requests

### Upload a KML
```bash
curl -X POST "http://localhost:8000/api/files/" \
  -F "file=@farms.kml"
```

### Upload a Zipped Shapefile
```bash
curl -X POST "http://localhost:8000/api/files/" \
  -F "file=@farms.zip"
```

### Check Status
```bash
curl "http://localhost:8000/api/files/9b1c.../"
```

### Get Measurements (with pagination)
```bash
# First 50 features
curl "http://localhost:8000/api/files/9b1c.../measurements?limit=50&offset=0"

# Only Polygons
curl "http://localhost:8000/api/files/9b1c.../measurements?geometry_type=Polygon"

# Large dataset pagination
curl "http://localhost:8000/api/files/9b1c.../measurements?limit=100&offset=100"
```

## Architecture

### Layer Diagram
```
api/          (Waiter)   → HTTP ↔ service calls
services/     (Manager)  → Orchestrate workflows
domain/       (Rules)    → Data shapes, state machine, errors, interfaces
geo/          (Chefs)    → Read, normalize, project, measure
infrastructure/ (Storage) → DB, file storage, background tasks
```

**Dependency rule:** `api → services → domain ← geo / infrastructure`. Services depend only on `domain/ports.py` Protocols, never on concrete implementations.

### Application Structure
```
app/
  main.py                  # App factory, exception handlers, startup recovery
  config.py                # Settings via pydantic-settings
  domain/
    models.py              # GeoFile, ParsedFeature, ParsedDataset, Measurement, FeatureRecord, Page
    status.py              # FileStatus + allowed transitions
    errors.py              # DomainError hierarchy with HTTP codes
    ports.py               # 7 interfaces (Protocol)
  geo/
    readers/
      base.py              # ReaderRegistry
      shapefile_zip.py     # ShapefileZipReader
      kml.py               # KmlReader
    normalize.py           # force_2d + make_valid + empty handling
    projection.py          # UtmProjectionStrategy, Projector (implements ProjectionStrategy)
    measurers.py           # Area, Length, NoOp, Unsupported, MeasurerRegistry
  infrastructure/
    db.py                  # Engine, SessionLocal, get_session
    orm.py                 # FileORM, FeatureORM
    repositories.py        # SqlFileRepository, SqlFeatureRepository
    storage.py             # LocalFileStorage
    tasks.py               # BackgroundTaskDispatcher
  services/
    upload_service.py      # Validate → store → PENDING → enqueue
    processing_service.py  # Pipeline: read → normalize → project → measure → persist
    query_service.py       # Read-side for GET endpoints
  api/
    routes.py              # 3 thin endpoints
    schemas.py             # Pydantic request/response models
    deps.py                # Composition root (FastAPI Depends)
tests/
  unit/                    # Domain, geo, infrastructure unit tests
  integration/             # Full API tests with TestClient
```

## Design Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| Strategy | Measurers; ProjectionStrategy | Rules vary by geometry type; projection policy swappable |
| Registry | ReaderRegistry, MeasurerRegistry | Add format/geometry rule by registering, not editing if/elif |
| Null Object | NoOpMeasurer, UnsupportedMeasurer | Points and unknown types handled without special cases |
| Repository | SqlFileRepository, SqlFeatureRepository | Services never see SQL |
| Adapter | LocalFileStorage, BackgroundTaskDispatcher | Infrastructure hidden behind interfaces |
| Pipeline | ProcessingService | Explicit, testable steps |
| State Machine | FileStatus | Illegal status jumps impossible |
| Dependency Injection | api/deps.py | One place wires everything; tests swap in fakes |

## Design Decisions

| Chose | Over | Because |
|-------|------|---------|
| FastAPI | Django/Flask | Async-ready, auto OpenAPI, type-safe, minimal boilerplate |
| BackgroundTasks | Celery/RQ | Simpler for MVP; in-process, no extra infra. Tradeoff: jobs lost on restart (mitigated by startup recovery) |
| SQLite | PostGIS | Zero-config for dev/test; portable. Tradeoff: single writer, no spatial queries |
| Per-feature UTM | Single global equal-area (EPSG:6933) | Accurate for local features; avoids distortion at zone boundaries. Tradeoff: more transformer objects (cached) |
| Fail on missing `.prj` | Guess EPSG:4326 | Silent wrong measurements worse than explicit failure |
| `Protocol` | `ABC` | Test fakes need no inheritance; structural subtyping |
| JSON columns for geometry | PostGIS geometry type | Portable across SQLite/PostgreSQL; no spatial extension needed |
| 409 for incomplete | Empty list + 200 | Clear signal to client: "poll again" vs ambiguous empty result |

## Testing

```bash
# Run all tests
uv run pytest

# Unit tests only
uv run pytest tests/unit/

# Integration tests only
uv run pytest tests/integration/

# With coverage
uv run pytest --cov=app

# Specific test file
uv run pytest tests/unit/test_domain.py -v
```

**Test Strategy:**
- Unit: domain state machine, each measurer, registry fallback, projector (passthrough + UTM N/S), readers (contract)
- Known-answer: ~1 km² square near Kochi ≈ 1,000,000 m² ±0.5%; projected vs geodesic ±0.5%
- Service: ProcessingService with in-memory fake repositories
- Integration: Upload KML → COMPLETED → measurements correct; bad zip → FAILED; 400/404/409

Fixtures generated programmatically in `tests/conftest.py` (no binary files committed).

## Limitations

- In-process background jobs lost on restart (mitigated: startup marks PENDING/PROCESSING → FAILED)
- SQLite single writer (concurrent writes serialized)
- UTM distortion for features spanning >6° longitude
- Antimeridian-crossing geometries not handled specially
- No authentication, rate limiting, or webhooks (future work)

## Troubleshooting

### Common Issues

**Upload fails with 400 "Unsupported file type"**
- Ensure file extension is `.zip` or `.kml` (case-insensitive)
- Raw `.shp` files not accepted - must be zipped with `.shx`, `.dbf`, `.prj`

**Upload fails with 413 "File too large"**
- Increase `MAX_UPLOAD_MB` in `.env` or use a smaller file
- Default limit is 50 MB

**Measurements endpoint returns 409**
- File processing not complete yet
- Poll `/api/files/{id}/` until status is `COMPLETED`
- If status is `FAILED`, check `error_message`

**File status stuck at PENDING**
- Check container logs: `docker logs geospatial-api-api-1`
- Background processing may have failed - check `error_message` on file info

**Database locked errors**
- SQLite serializes writes - ensure no concurrent test runs
- For production, consider PostgreSQL/PostGIS

### Logs
```bash
# Docker
docker logs geospatial-api-api-1 -f

# Local
uv run uvicorn app.main:app --log-level DEBUG
```

## Learnings

<!-- TODO: author to fill in -->

## Future Scope

- Celery/Redis workers for durable background processing
- PostGIS with spatial queries and indexing
- S3/GCS storage adapter
- GeoJSON / KMZ / GeoPackage input formats
- Local equal-area projection strategy (EPSG:6933 or per-feature LAEA)
- Authentication (API keys, JWT) and rate limiting
- Pagination cursors (keyset) for large datasets
- Webhooks on completion
- Observability: structured logs, metrics (Prometheus), tracing (OpenTelemetry)

## License

MIT