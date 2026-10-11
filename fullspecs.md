# Geospatial File Measurement API — Design Document

Oct 8, 2026 · @Amalgamic Master Plan

## 1. What we are building

A FastAPI backend that takes a geospatial file, measures every shape inside it in real-world units (square meters, meters), and lets the user fetch the results later. Processing runs in the background, so uploads return instantly.

**Inputs.** A `.zip` containing a Shapefile bundle (`.shp`, `.shx`, `.dbf`, `.prj`), or a single `.kml` file.

**Outputs.** For each file: its status, source CRS and feature count. For each feature: index, layer, geometry type, geometry (GeoJSON, original coordinates), properties, and a measurement block.

**Measurement rules.**

| Geometry type | Measurement | Needs projection to meters? |
| --- | --- | --- |
| Polygon, MultiPolygon | Area in m² | Yes |
| LineString, MultiLineString, LinearRing | Length in m | Yes |
| Point, MultiPoint | None (by design) | No |
| Anything else (e.g. GeometryCollection) | None, with an explanatory note | No |

**The one rule that matters most.** Never measure in latitude/longitude degrees. If the file's coordinates are in degrees, convert each shape to a metric projected CRS (its UTM zone) before measuring.

**Out of scope (future work).** Authentication, a separate worker process (Celery), PostGIS spatial queries, cloud storage, formats other than Shapefile and KML.

## 2. Key terms in plain language

| Term | Plain meaning |
| --- | --- |
| Feature | One shape in the file plus its attributes, e.g. one farm plot with its name and owner. |
| Layer | A group of features. A KML Folder or one shapefile in the zip becomes one layer. |
| Geometry | The shape itself: Point, LineString, Polygon, or their Multi versions. |
| Properties | The feature's attributes (name, id, owner...), stored as a JSON object. |
| CRS | Coordinate Reference System: the rulebook that says what the coordinate numbers mean. |
| Source CRS | The CRS the file's coordinates are written in. Read from the file, never guessed. |
| Measurement CRS | The metric CRS we convert to before measuring. Chosen by our code. |
| EPSG code | A catalogue number for a CRS. EPSG:4326 = lat/lon degrees (GPS, KML). |
| Geographic CRS | Coordinates in degrees. Unsafe for area or length. |
| Projected CRS | Coordinates on a flat map, usually in meters. Safe for area and length. |
| UTM zone | One of 60 projected CRSs, each a 6° slice of the Earth. Kochi is zone 43N = EPSG:32643. |
| Geodesic measurement | Measuring on the curved Earth model directly. Used as our cross-check. |
| Interface (Protocol) | A job description: method names and types, no implementation. |
| Adapter / implementation | A real class that does the job an interface describes. |
| Session | SQLAlchemy's workspace for one unit of database work. Borrows a connection from the pool. |

## 3. Architecture

The app has five layers, and dependencies point inward: everything depends on the domain, and the domain depends on nothing.

&#91;embedded content: layered architecture · 5 layers, dependencies point inward\]

A request enters at the API, the services coordinate, and the geo and infrastructure adapters do the real work behind the domain's interfaces.

### 3.1 Layers in plain terms

| Layer | Restaurant role | Job | May import |
| --- | --- | --- | --- |
| `api/` | Waiter | Turn HTTP into service calls and results into JSON | services, domain |
| `services/` | Manager | Decide what happens in what order | domain only |
| `domain/` | House rules | Data shapes, status rules, errors, interfaces | nothing in the app |
| `geo/` | Chefs and tools | Read files, project, measure | domain, geopandas, shapely, pyproj |
| `infrastructure/` | Storeroom and order book | Database, file storage, background jobs | domain, SQLAlchemy, FastAPI BackgroundTasks |

### 3.2 Folder structure

```text
app/
  main.py                  app factory, exception handlers, startup recovery
  config.py                Settings from environment / .env
  domain/
    models.py              GeoFile, ParsedFeature, ParsedDataset, Measurement, FeatureRecord, Page
    status.py              FileStatus + allowed transitions
    errors.py              DomainError hierarchy
    ports.py               the 7 interfaces (typing.Protocol)
  geo/
    readers/base.py        ReaderRegistry
    readers/shapefile_zip.py
    readers/kml.py
    normalize.py           force_2d, make_valid, empty handling
    projection.py          UtmProjectionStrategy, Projector
    measurers.py           Area, Length, NoOp, Unsupported, MeasurerRegistry
  infrastructure/
    db.py                  engine (pool), SessionLocal, get_session
    orm.py                 SQLAlchemy tables
    repositories.py        SqlFileRepository, SqlFeatureRepository
    storage.py             LocalFileStorage
    tasks.py               BackgroundTaskDispatcher
  services/
    upload_service.py
    processing_service.py
    query_service.py
  api/
    routes.py  schemas.py  deps.py
tests/ unit/  integration/  conftest.py
```

### 3.3 The dependency rule

Services import interfaces from `domain/ports.py`, never concrete classes. Only `api/deps.py` knows that `FileRepository` means `SqlFileRepository`. Swapping SQLite for PostGIS, or local disk for S3, changes one adapter and one line in `deps.py`.

### 3.4 Design patterns used

| Pattern | Where | Why |
| --- | --- | --- |
| Strategy | Measurers; ProjectionStrategy | Rules vary by geometry type; projection policy is swappable |
| Registry | ReaderRegistry, MeasurerRegistry | Add a format or geometry rule by registering, not editing if/elif |
| Null Object | NoOpMeasurer, UnsupportedMeasurer | Points and unknown types are handled without special cases |
| Repository | SqlFileRepository, SqlFeatureRepository | Services never see SQL |
| Adapter | LocalFileStorage, BackgroundTaskDispatcher | Infrastructure hidden behind interfaces |
| Pipeline | ProcessingService | Explicit, testable steps |
| State machine | FileStatus | Illegal status jumps are impossible |
| Dependency injection | api/deps.py | One place wires everything; tests swap in fakes |

## 4. Domain model (the data shapes every layer shares)

The `domain/` package is plain Python with no FastAPI, SQLAlchemy or file I/O. Every other layer speaks in these types.

### 4.1 Data classes (`domain/models.py`)

| Class | Fields | Created by | Used by |
| --- | --- | --- | --- |
| `GeoFile` | id, filename, file\_type, stored\_path, status, crs, feature\_count, error\_message, created\_at, completed\_at | UploadService | Repositories, services, API |
| `ParsedFeature` | index, layer, geometry (shapely), properties (dict) | Readers | ProcessingService |
| `ParsedDataset` | crs (pyproj CRS, never None), features (list of ParsedFeature) | Readers | ProcessingService |
| `Measurement` | area\_m2, length\_m, geodesic\_area\_m2, geodesic\_length\_m, measurement\_crs, note (all optional) | Measurers | ProcessingService |
| `FeatureRecord` | file\_id, feature\_index, layer, geometry\_type, geometry (GeoJSON dict), properties, measurement | ProcessingService | FeatureRepository, QueryService |
| `Page[T]` | items, total, limit, offset | FeatureRepository | QueryService |

`GeoFile` is the only mutable one, because its status changes. The rest are frozen dataclasses: once created, they never change, which removes a whole class of bugs.

### 4.2 Status state machine (`domain/status.py`)

A file moves through four states. Only these moves are legal; anything else raises `InvalidStatusTransition`.

| From | Allowed next states | Who triggers it |
| --- | --- | --- |
| PENDING | PROCESSING, FAILED | ProcessingService starts work; startup recovery |
| PROCESSING | COMPLETED, FAILED | ProcessingService finishes or fails; startup recovery |
| COMPLETED | none (final) | — |
| FAILED | none (final) | — |

`GeoFile` exposes `start_processing()`, `mark_completed(crs, feature_count)` and `mark_failed(message)`. Each calls `ensure_transition()` first, so no code can set the status field directly.

### 4.3 Error hierarchy (`domain/errors.py`)

All expected problems are subclasses of `DomainError`. Each knows its HTTP code and a stable error code string.

| Exception | Raised when | Raised by | Result |
| --- | --- | --- | --- |
| `UnsupportedFileType` | Extension is not .zip or .kml | UploadService | HTTP 400 |
| `FileTooLarge` | Upload exceeds MAX\_UPLOAD\_MB | UploadService | HTTP 413 |
| `FileNotFound` | Unknown file id | QueryService | HTTP 404 |
| `ProcessingNotComplete` | Measurements requested before COMPLETED | QueryService | HTTP 409 |
| `InvalidGeoData` | Bad zip, no .shp, missing .shx/.dbf/.prj, unreadable file | Readers | File marked FAILED with the message |
| `InvalidStatusTransition` | Illegal status move (a programming bug) | GeoFile | Logged; file marked FAILED |

## 5. Interfaces (ports) and their contracts

Seven interfaces live in `domain/ports.py`, written as `typing.Protocol`. Services only ever depend on these, never on concrete classes. Each contract below states what goes in, what comes out, and which errors are allowed.

| Interface | Methods | Implementation today | Called by | Future implementation |
| --- | --- | --- | --- | --- |
| `GeoFileReader` | `can_read(path) -> bool`, `read(path) -> ParsedDataset` | `ShapefileZipReader`, `KmlReader` | ReaderRegistry → ProcessingService | `KmzReader`, `GeoJsonReader` |
| `ProjectionStrategy` | `choose_crs(geom, source) -> CRS` | `UtmProjectionStrategy` | Projector | `LocalEqualAreaStrategy` |
| `GeometryMeasurer` | `requires_projection: bool`, `measure(geom) -> Measurement` | Area, Length, NoOp, Unsupported | MeasurerRegistry → ProcessingService | New geometry rules |
| `FileStorage` | `save(file_id, filename, stream) -> Path` | `LocalFileStorage` | UploadService | `S3FileStorage` |
| `TaskDispatcher` | `enqueue_processing(file_id) -> None` | `BackgroundTaskDispatcher` | UploadService | `CeleryTaskDispatcher` |
| `FileRepository` | `add(f)`, `get(id) -> GeoFile or None`, `save(f)`, `list_by_status(status)` | `SqlFileRepository` | UploadService, ProcessingService, QueryService, startup | PostGIS repository |
| `FeatureRepository` | `add_many(file_id, records)`, `list(file_id, limit, offset, geometry_type) -> Page` | `SqlFeatureRepository` | ProcessingService, QueryService | PostGIS repository |

### 5.1 Contract details

**GeoFileReader**

- `can_read` decides by file extension only, is cheap, and never raises.
- `read` returns a dataset whose `crs` is never None and whose feature indexes are unique.
- `read` returns geometries untouched; cleaning happens later in `normalize`.
- On any bad content, `read` raises `InvalidGeoData` with a human-readable message. It never raises other exception types for bad input.
- Zip readers clean up their temp directory, even on failure.

**ProjectionStrategy**

- Receives a geometry in a geographic source CRS and returns a projected CRS in meters suitable for that geometry's location.
- Must be deterministic: same geometry in, same CRS out.

**GeometryMeasurer**

- `requires_projection` tells the caller whether to convert to meters first.
- `measure` receives a geometry already in the right CRS and returns a `Measurement`. It never raises for any geometry it is registered for.
- `UnsupportedMeasurer` is the fallback: it returns a `Measurement` with only a `note`.

**FileStorage**

- `save` writes the stream to a location unique to `file_id` and returns the path. It must not trust the client's filename for the path (sanitise it).

**TaskDispatcher**

- `enqueue_processing` returns immediately. The job must run later, with its own database session, and must never raise into the request.

**FileRepository and FeatureRepository**

- Take and return domain objects only; SQLAlchemy objects never leave `infrastructure/`.
- `get` returns None for unknown ids (the service decides whether that is an error).
- `add_many` inserts all records in one transaction: all or nothing.
- `list` returns features ordered by `feature_index`, plus the total count for pagination.

### 5.2 Registries (not interfaces, but how strategies are chosen)

- `ReaderRegistry(readers)` → `for_path(path)` returns the first reader whose `can_read` is True, else raises `UnsupportedFileType`.
- `MeasurerRegistry(mapping, fallback)` → `for_type(geom_type)` returns the mapped measurer, else the fallback. It never fails.

## 6. Flow 1: Upload (`POST /api/files/`)

The upload request does only fast, safe work: validate, store, record, enqueue, respond. It returns `202 Accepted` in milliseconds; no geospatial work happens here.

| Step | Component | Calls (interface) | What happens | On failure |
| --- | --- | --- | --- | --- |
| 1 | `routes.upload_file` | `UploadService.upload` | Receives the multipart `UploadFile`. Contains no logic. | — |
| 2 | `UploadService` | — | Lower-cases the extension. Rejects anything except `.zip` and `.kml`. A raw `.shp` gets a specific message. | `UnsupportedFileType` → 400 |
| 3 | `UploadService` | — | Checks size against `MAX_UPLOAD_MB` while streaming. | `FileTooLarge` → 413 |
| 4 | `UploadService` | — | Generates `file_id = uuid4()`. | — |
| 5 | `UploadService` | `FileStorage.save` | Writes the file to `uploads/{file_id}/{safe_name}`. | Unexpected error → 500 |
| 6 | `UploadService` | `FileRepository.add` | Inserts a `GeoFile` row with status PENDING. | Unexpected error → 500 |
| 7 | `UploadService` | `TaskDispatcher.enqueue_processing` | Schedules processing to run after the response is sent. | — |
| 8 | `routes.upload_file` | — | Returns `202 {id, filename, status: PENDING}`. | — |

**Why this order.** The file is stored before the database row exists, so a row never points at a missing file. The job is enqueued last, so it never runs for a row that was not saved.

**Who talks to whom.** Route → UploadService → FileStorage, FileRepository, TaskDispatcher. The route never touches storage or the database directly.

## 7. Flow 2: Background processing pipeline

After the 202 response is sent, `BackgroundTaskDispatcher` runs `ProcessingService.process(file_id)` in a threadpool thread, with its own database session.

&#91;embedded content: processing pipeline · file-level checks, then a per-feature loop\]

A problem with the whole file (bad zip, no CRS) fails the file. A problem with one feature only adds a note to that feature; the rest are still measured.

### 7.1 Step by step

| Step | Component | Interface call | What happens |
| --- | --- | --- | --- |
| 1 | BackgroundTaskDispatcher | — | Opens a new session from `SessionLocal`, builds a ProcessingService, calls `process(file_id)` |
| 2 | ProcessingService | `FileRepository.get`, `save` | Loads the GeoFile, calls `start_processing()`, saves |
| 3 | ProcessingService | `ReaderRegistry.for_path` | Picks the reader whose `can_read` is True |
| 4 | ShapefileZipReader or KmlReader | `GeoFileReader.read` | Returns a ParsedDataset (CRS + features) or raises `InvalidGeoData` |
| 5 | `normalize()` | — | Drops Z values, repairs invalid shapes, flags empty ones |
| 6 | ProcessingService | `MeasurerRegistry.for_type` | Gets the measurer for this geometry type, or the Unsupported fallback |
| 7 | Projector | `ProjectionStrategy.choose_crs` | Only if `requires_projection`: source already metric → unchanged; degrees → UTM zone from the centroid |
| 8 | Measurer | `GeometryMeasurer.measure` | Area or length in the projected CRS; geodesic cross-check on WGS 84 |
| 9 | ProcessingService | — | Builds a FeatureRecord. Geometry is kept in the source CRS |
| 10 | ProcessingService | `FeatureRepository.add_many` | Inserts all records in one transaction |
| 11 | ProcessingService | `FileRepository.save` | `mark_completed(crs, feature_count)` |

### 7.2 Reader details

**ShapefileZipReader**

1. Extract to a temp directory, rejecting any entry whose path escapes it (zip-slip guard).
2. Find every `.shp` recursively. None → `InvalidGeoData("No .shp file found in zip")`.
3. For each `.shp`, require sibling `.shx` and `.dbf`; name the missing file in the error.
4. Read with geopandas. CRS is None (no `.prj`) → `InvalidGeoData`. Never guess.
5. Layer name = the shapefile's file name. Delete the temp directory in a `finally` block.

**KmlReader**

1. List layers with `pyogrio.list_layers()`; KML Folders become layers.
2. Read each layer; CRS is EPSG:4326 by the KML standard.
3. Corrupt or empty file → `InvalidGeoData`.

### 7.3 Projection details

- Source is projected and in meters (e.g. a UTM shapefile) → measure as-is; `measurement_crs` = source CRS.
- Source is geographic (degrees) → choose the UTM zone from each feature's centroid, so files spanning several zones are still accurate.
- Transformers are cached per (source, target) pair and always built with `always_xy=True`, so longitude is always x.
- The CRS label uses the EPSG code when one exists, else the authority code (e.g. ESRI:102003), else the CRS name.

## 8. Flows 3 and 4: Reading results

Both read endpoints go Route → QueryService → repositories. They never trigger processing and never modify data.

### 8.1 File info (`GET /api/files/{id}/`)

1. The route calls `QueryService.get_file(file_id)`.
2. QueryService calls `FileRepository.get(file_id)`. If it returns None, it raises `FileNotFound` → 404.
3. The route maps the `GeoFile` to the `FileInfoResponse` schema and returns 200, whatever the status.

This is the endpoint the client polls after an upload, until status is COMPLETED or FAILED.

### 8.2 Measurements (`GET /api/files/{id}/measurements/`)

1. The route validates query parameters: `limit` (default 100, max 1000), `offset` (default 0), optional `geometry_type`.
2. QueryService calls `FileRepository.get`. Unknown id → `FileNotFound` → 404.
3. If status is not COMPLETED, it raises `ProcessingNotComplete` → 409, with the current status in the body. A FAILED file also returns 409, and its error message is available from the file info endpoint.
4. QueryService calls `FeatureRepository.list(file_id, limit, offset, geometry_type)` and gets back a `Page[FeatureRecord]`.
5. The route maps each record to `FeatureMeasurementResponse` and returns 200 with `total`, `limit`, `offset` and `results`.

## 9. Flow 5: Failure handling and startup recovery

The rule: request-time errors become HTTP errors; processing-time errors become a FAILED status with a readable message. Nothing crashes the server, and no failure is silent.

| Where it fails | Example | What the user sees |
| --- | --- | --- |
| Upload validation | `.shp` uploaded alone, `.pdf`, 80 MB file | 400 or 413 with `{"error": {"code", "message"}}` |
| Reading the file | Zip has no `.shp`; `.dbf` missing; no `.prj`; corrupt KML | Status FAILED, `error_message` explains the exact problem |
| One bad feature | GeometryCollection, empty geometry, invalid polygon | File still COMPLETED. That feature gets a `note`; invalid polygons are repaired with `make_valid` |
| Unexpected bug | Any other exception during processing | Status FAILED, message "Internal processing error"; full stack trace in the logs |
| Server restart mid-job | Process killed while a file is PROCESSING | On next startup, the file is marked FAILED: "Interrupted by server restart" |

### 9.1 How exceptions turn into HTTP responses

`main.py` registers one exception handler for `DomainError`. It reads the exception's `http_status` and `code` and returns the standard error body. Routes therefore contain no try/except blocks.

### 9.2 Startup recovery

In-process background tasks are lost if the server stops. On startup, `main.py` asks `FileRepository.list_by_status(PROCESSING)` and marks each one FAILED. Files still PENDING are also marked FAILED, since their queued task no longer exists. The real fix, a durable queue such as Celery, is listed as future work.

## 10. Wiring: who creates what, and who talks to whom

One file, `api/deps.py`, creates and connects every object (the composition root). No other class creates its own dependencies; they receive them in their constructor.

### 10.1 Who talks to whom

| Caller | Depends on (through interfaces) | Never touches |
| --- | --- | --- |
| Routes | UploadService, QueryService | Repositories, storage, geo code |
| UploadService | FileStorage, FileRepository, TaskDispatcher | Readers, measurers, SQLAlchemy |
| ProcessingService | FileRepository, FeatureRepository, ReaderRegistry, MeasurerRegistry, Projector | HTTP, FastAPI, file uploads |
| QueryService | FileRepository, FeatureRepository | Geo code, storage |
| Projector | ProjectionStrategy | Databases, files |
| Readers, measurers | geopandas, shapely, pyproj | Databases, HTTP |
| Repositories | SQLAlchemy session | Geo code, HTTP |

### 10.2 Object lifetimes

| Object | Lifetime | Why |
| --- | --- | --- |
| Settings, engine (connection pool) | Once per app | Expensive to create; shared safely |
| ReaderRegistry, MeasurerRegistry, Projector | Once per app | Stateless; transformers cached with `lru_cache` |
| Session, repositories, services | Once per request | A session is one unit of work |
| BackgroundTaskDispatcher | Once per request | Wraps that request's `BackgroundTasks` object |
| Background job's session and ProcessingService | Once per job | The request's session is closed by the time the job runs |

### 10.3 Database sessions and the connection pool

- `create_engine()` makes a connection pool once for the whole app. Requests and background jobs share it.
- `SessionLocal` is a session factory. `get_session()` yields one session per request and closes it after the response.
- The background job opens its own session with `with SessionLocal() as session:`. It borrows a connection from the same pool and returns it when done.
- SQLite settings: `check_same_thread=False` (jobs run in another thread), `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` (smooth handling of SQLite's one-writer limit).
- Route handlers and the job function are plain `def`, so Starlette runs them in a threadpool and the event loop never blocks.

## 11. Database schema

Two tables in SQLite: one row per uploaded file, one row per feature. One file has many features.

**Table `files`**

| Column | Type | Notes |
| --- | --- | --- |
| id | TEXT, primary key | UUID string |
| filename | TEXT | Original name as uploaded |
| file\_type | TEXT | `shapefile_zip` or `kml` |
| stored\_path | TEXT | Where FileStorage saved it |
| status | TEXT, indexed | PENDING / PROCESSING / COMPLETED / FAILED |
| crs | TEXT, nullable | Source CRS label, e.g. `EPSG:4326` |
| feature\_count | INTEGER, nullable | Set on completion |
| error\_message | TEXT, nullable | Set on failure |
| created\_at | DATETIME | UTC |
| completed\_at | DATETIME, nullable | UTC, set on COMPLETED or FAILED |

**Table `features`**

| Column | Type | Notes |
| --- | --- | --- |
| id | INTEGER, primary key | Auto-increment |
| file\_id | TEXT, foreign key → files.id, indexed |  |
| feature\_index | INTEGER | Unique per file; used for ordering |
| layer | TEXT | KML folder or shapefile name |
| geometry\_type | TEXT, indexed | Supports the `?geometry_type=` filter |
| geometry | JSON | GeoJSON in the source CRS |
| properties | JSON | Made JSON-safe: dates → ISO strings, NaN → null |
| area\_m2 | REAL, nullable | Projected area |
| length\_m | REAL, nullable | Projected length |
| geodesic\_area\_m2 | REAL, nullable | Cross-check on the ellipsoid |
| geodesic\_length\_m | REAL, nullable | Cross-check on the ellipsoid |
| measurement\_crs | TEXT, nullable | e.g. `EPSG:32643` |
| note | TEXT, nullable | Why a value is missing or what was repaired |

A unique constraint on (file\_id, feature\_index) protects against duplicate inserts.

## 12. API contract

| Method and path | Purpose | Success | Errors |
| --- | --- | --- | --- |
| `POST /api/files/` | Upload a .zip or .kml and start processing | 202 | 400 bad type, 413 too large |
| `GET /api/files/{id}/` | File info and current status (poll this) | 200 | 404 unknown id |
| `GET /api/files/{id}/measurements/` | Per-feature results; `limit`, `offset`, `geometry_type` | 200 | 404 unknown id, 409 not COMPLETED |

**POST response (202)**

```json
{"id": "9b1c...", "filename": "farms.zip", "status": "PENDING"}
```

**File info response (200)**

```json
{"id": "9b1c...", "filename": "farms.zip", "file_type": "shapefile_zip",
 "status": "COMPLETED", "crs": "EPSG:4326", "feature_count": 120,
 "error_message": null, "created_at": "...", "completed_at": "..."}
```

**Measurements response (200)**

```json
{"file_id": "9b1c...", "total": 120, "limit": 100, "offset": 0,
 "results": [{
   "feature_index": 0, "layer": "farms", "geometry_type": "Polygon",
   "crs": "EPSG:4326", "properties": {"name": "Plot A"},
   "geometry": {"type": "Polygon", "coordinates": [[...]]},
   "measurement": {"area_m2": 10234.5, "length_m": null,
                   "geodesic_area_m2": 10231.9, "geodesic_length_m": null,
                   "measurement_crs": "EPSG:32643", "note": null}}]}
```

**Error body (all errors)**

```json
{"error": {"code": "PROCESSING_NOT_COMPLETE", "message": "File is still PROCESSING"}}
```

FastAPI generates interactive OpenAPI docs from the Pydantic schemas at `/docs`.

## 13. Testing strategy and build order

### 13.1 Tests

| Level | What is tested | How |
| --- | --- | --- |
| Unit: domain | Status transitions, legal and illegal | Plain pytest, no I/O |
| Unit: measurers | Each measurer; registry fallback for GeometryCollection | Hand-built shapely geometries |
| Unit: projection | Projected-in-meters passthrough; UTM choice in north and south hemispheres | Known coordinates, e.g. Kochi → EPSG:32643 |
| Contract: readers | Every reader passes one shared suite: CRS never None, bad input raises only InvalidGeoData | Parametrised fixtures |
| Known-answer | A \~1 km × 1 km square near Kochi ≈ 1,000,000 m² within 0.5%; projected vs geodesic within 0.5% | Fixtures generated in `conftest.py` |
| Service | ProcessingService success and failure paths | In-memory fake repositories, no DB |
| Integration | Upload → COMPLETED → measurements; bad zip → FAILED; 400, 404, 409 cases | FastAPI TestClient (background tasks finish before it returns) |

### 13.2 Build order

1. Project setup with uv, folder skeleton, config, tooling; the app serves `/docs`.
2. Domain: models, status, errors, ports, with tests.
3. Geo: measurers and registry, projection, normalize, readers, with known-answer tests.
4. Infrastructure: db and pool, ORM, repositories, storage, dispatcher.
5. Services and API: upload, processing, query, routes, deps, error handlers, integration tests.
6. Docker and the full README.

Each step is testable on its own before the next begins, and each one teaches one layer.
