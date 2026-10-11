#!/usr/bin/env python3
"""
Generate test fixtures for the Geospatial File Measurement API.

    uv run python make_test_files.py        # writes ./test_files/

Every feature has a `name` property. test_files/expected.json holds the expected
result per file and per feature, computed the way the pipeline should:
force_2d -> make_valid -> UTM zone from centroid (if lon/lat) -> measure.
"""
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from pyproj import Geod, Transformer
from shapely.affinity import translate
from shapely.geometry import (GeometryCollection, LineString, MultiLineString,
                              MultiPoint, MultiPolygon, Point, Polygon)
from shapely.geometry.polygon import orient
from shapely.ops import transform

OUT = Path("test_files")
TOO_BIG_MB = 60  # set this ABOVE your MAX_UPLOAD_MB
GEOD = Geod(ellps="WGS84")
EXPECTED: dict = {}

KIND = {"Polygon": "area", "MultiPolygon": "area",
        "LineString": "length", "MultiLineString": "length", "LinearRing": "length",
        "Point": "none", "MultiPoint": "none"}

# ---------------------------------------------------------------- CRS helpers
_tf = {}
def tf(src, dst):
    if (src, dst) not in _tf:
        _tf[(src, dst)] = Transformer.from_crs(src, dst, always_xy=True)
    return _tf[(src, dst)].transform

def utm_epsg(lon, lat):
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone

class Site:
    """Build shapes in local metres around a centre; emit them in UTM or lon/lat."""
    def __init__(self, lon, lat):
        self.epsg = utm_epsg(lon, lat)
        self.x0, self.y0 = tf(4326, self.epsg)(lon, lat)
    def utm(self, g):
        return None if g is None else translate(g, self.x0, self.y0)
    def ll(self, g):
        return None if g is None else transform(tf(self.epsg, 4326), self.utm(g))

KOCHI  = Site(76.2673, 9.9312)     # UTM 43N -> EPSG:32643
LONDON = Site(-0.1276, 51.5072)    # UTM 30N -> EPSG:32630
SYDNEY = Site(151.2093, -33.8688)  # UTM 56S -> EPSG:32756 (southern hemisphere)

# ------------------------------------------------------- local shapes (metres)
def square(side, cx=0.0, cy=0.0):
    h = side / 2
    return Polygon([(cx-h, cy-h), (cx+h, cy-h), (cx+h, cy+h), (cx-h, cy+h)])

def bowtie(side, cx=0.0, cy=0.0):
    """Self-intersecting ring. make_valid -> 2 triangles = half the square's area."""
    h = side / 2
    return Polygon([(cx-h, cy-h), (cx+h, cy+h), (cx+h, cy-h), (cx-h, cy+h)])

PLOT_1KM   = square(1000)                                              # 1,000,000 m2
PLOT_HOLE  = Polygon(square(1000, 3000).exterior.coords,
                     [square(500, 3000).exterior.coords])              #   750,000 m2
PLOT_MULTI = MultiPolygon([square(500, 6000, 0), square(500, 6000, 1000)])  # 500,000 m2
PLOT_BOW   = bowtie(1000, -3000, 3000)                                 #   500,000 m2 after repair
ROAD       = LineString([(0, -2000), (1000, -2000)])                   #     1,000 m
TRAIL      = LineString([(-5000, -3000), (-2000, -3000), (-2000, 1000)])  # 7,000 m
CANALS     = MultiLineString([[(0, -5000), (500, -5000)],
                              [(0, -5500), (500, -5500)]])             #     1,000 m
WELL       = Point(0, 2000)
TANKS      = MultiPoint([(100, 2000), (200, 2000)])
MIXED      = GeometryCollection([Point(0, 3000), LineString([(0, 3000), (300, 3000)])])

# ------------------------------------------------------------ expected values
def geod_area(g):
    if g.geom_type == "Polygon":
        return abs(GEOD.geometry_area_perimeter(orient(g, 1.0))[0])
    if hasattr(g, "geoms"):
        return sum(geod_area(p) for p in g.geoms)
    return 0.0

def expected_for(geom, src_epsg=4326):
    gtype = None if geom is None else geom.geom_type
    if geom is None or geom.is_empty:
        return {"geometry_type": gtype, "measured": False, "expect_note": True}
    kind = KIND.get(gtype)
    if kind is None:
        return {"geometry_type": gtype, "measured": False, "expect_note": True}
    if kind == "none":
        return {"geometry_type": gtype, "measured": False, "expect_note": False}

    g = shapely.make_valid(shapely.force_2d(geom))
    if src_epsg == 4326:
        c = g.centroid
        m_epsg = utm_epsg(c.x, c.y)
        proj, ll = transform(tf(4326, m_epsg), g), g
    else:
        m_epsg, proj, ll = src_epsg, g, transform(tf(src_epsg, 4326), g)

    out = {"geometry_type": gtype, "measured": True, "measurement_crs": f"EPSG:{m_epsg}"}
    if kind == "area":
        out["area_m2"] = round(proj.area, 3)
        out["geodesic_area_m2"] = round(geod_area(ll), 3)
    else:
        out["length_m"] = round(proj.length, 3)
        out["geodesic_length_m"] = round(GEOD.geometry_length(ll), 3)
    return out

# ----------------------------------------------------------------- KML writer
def _c(cs):
    return " ".join(",".join(f"{v:.9f}" for v in c) for c in cs)

def kml_geom(g):
    t = g.geom_type
    if t == "Point":
        return f"<Point><coordinates>{_c(g.coords)}</coordinates></Point>"
    if t == "LineString":
        return f"<LineString><coordinates>{_c(g.coords)}</coordinates></LineString>"
    if t == "Polygon":
        ring = lambda r: f"<LinearRing><coordinates>{_c(r.coords)}</coordinates></LinearRing>"
        inner = "".join(f"<innerBoundaryIs>{ring(r)}</innerBoundaryIs>" for r in g.interiors)
        return f"<Polygon><outerBoundaryIs>{ring(g.exterior)}</outerBoundaryIs>{inner}</Polygon>"
    return "<MultiGeometry>" + "".join(kml_geom(p) for p in g.geoms) + "</MultiGeometry>"

def placemark(name, g, props):
    ext = "".join(f'<Data name="{k}"><value>{v}</value></Data>' for k, v in props.items())
    return f"<Placemark><name>{name}</name><ExtendedData>{ext}</ExtendedData>{kml_geom(g)}</Placemark>"

def write_kml(path, folders):
    body = "".join(f"<Folder><name>{fn}</name>{''.join(placemark(*p) for p in pms)}</Folder>"
                   for fn, pms in folders.items())
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
                    f"<name>{path.stem}</name>{body}</Document></kml>", encoding="utf-8")

def kml_file(fname, folders, sample=None):
    write_kml(OUT / fname, folders)
    allf = [(n, g) for pms in folders.values() for n, g, _ in pms]
    EXPECTED[fname] = {"status": "COMPLETED", "crs": "EPSG:4326", "feature_count": len(allf),
                       "features": {n: expected_for(g) for n, g in allf[:sample]}}

# ----------------------------------------------------------- shapefile writer
def write_layer(folder, layer, feats, epsg):
    rows = [{"name": n, **p} for n, _, p in feats]
    gdf = gpd.GeoDataFrame(rows, geometry=[g for _, g, _ in feats], crs=f"EPSG:{epsg}")
    if "survey_dt" in gdf:
        gdf["survey_dt"] = pd.to_datetime(gdf["survey_dt"])
    folder.mkdir(parents=True, exist_ok=True)
    gdf.to_file(folder / f"{layer}.shp", driver="ESRI Shapefile")

def shp_zip(fname, layers, epsg=4326, prefix="", drop=(), extra=None,
            status="COMPLETED", why=None):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for layer, feats in layers.items():
            write_layer(td, layer, feats, epsg)
        with zipfile.ZipFile(OUT / fname, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(td.iterdir()):
                if p.suffix.lower() not in drop:
                    z.write(p, prefix + p.name)
            for arc, data in (extra or {}).items():
                z.writestr(arc, data)
    entry = {"status": status}
    if why:
        entry["why"] = why
    if status == "COMPLETED":
        entry["crs"] = f"EPSG:{epsg}"
        entry["feature_count"] = sum(len(f) for f in layers.values())
        entry["features"] = {n: expected_for(g, epsg)
                             for feats in layers.values() for n, g, _ in feats}
    EXPECTED[fname] = entry

def in_ll(site, items):
    return [(n, site.ll(g), p) for n, g, p in items]

def in_utm(site, items):
    return [(n, site.utm(g), p) for n, g, p in items]

# ---------------------------------------------------------------------- build
def main():
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir()

    plots = [
        ("plot_1km",   PLOT_1KM,   {"owner": "Joseph", "crop": "paddy",   "area_ha": 100.0, "survey_dt": "2024-03-15"}),
        ("plot_hole",  PLOT_HOLE,  {"owner": "ജോസഫ്",  "crop": "coconut", "area_ha": float("nan"), "survey_dt": None}),
        ("plot_multi", PLOT_MULTI, {"owner": "Mary",   "crop": "banana",  "area_ha": 50.0,  "survey_dt": "2023-11-02"}),
    ]
    roads = [("road_1km", ROAD, {"surface": "tar"}),
             ("trail_7km", TRAIL, {"surface": "mud"}),
             ("canals", CANALS, {"surface": "water"})]
    wells = [("well", WELL, {"depth_m": 12.0})]

    # ---- KML: happy path + awkward geometries, 4 folders (= 4 layers)
    kml_file("kochi_survey.kml", {
        "Plots":  in_ll(KOCHI, [(n, g, {"owner": p["owner"]}) for n, g, p in plots])
                  + in_ll(KOCHI, [("plot_bowtie", PLOT_BOW, {"owner": "invalid ring"})]),
        "Roads":  in_ll(KOCHI, [(n, g, {}) for n, g, _ in roads]),
        "Points": in_ll(KOCHI, [("well", WELL, {}), ("tanks", TANKS, {})]),
        "Weird":  in_ll(KOCHI, [("mixed_bag", MIXED, {})])
                  + [("plot_3d", shapely.force_3d(KOCHI.ll(square(500, 0, -8000)), 12.5), {})],
    })
    kml_file("SHOUTY.KML", {"Only": in_ll(KOCHI, [("plot_1km", PLOT_1KM, {}), ("well", WELL, {})])})
    kml_file("many_plots.kml", {"Grid": in_ll(KOCHI, [
        (f"g_{i:02d}_{j:02d}", square(50, i * 200, 20000 + j * 200), {})
        for i in range(50) for j in range(50)])}, sample=3)

    (OUT / "corrupt.kml").write_text((OUT / "kochi_survey.kml").read_text(encoding="utf-8")[:400],
                                     encoding="utf-8")
    EXPECTED["corrupt.kml"] = {"status": "FAILED", "why": "truncated XML"}
    (OUT / "empty.kml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns='
                                   '"http://www.opengis.net/kml/2.2"><Document></Document></kml>')
    EXPECTED["empty.kml"] = {"status": ["COMPLETED", "FAILED"],
                             "why": "your call: 0 features or FAILED, but never a crash"}

    # ---- Shapefile zips: happy paths
    shp_zip("kochi_farms_4326.zip",
            {"plots": in_ll(KOCHI, plots), "roads": in_ll(KOCHI, roads), "wells": in_ll(KOCHI, wells)})
    shp_zip("kochi_farms_utm43n.zip",
            {"plots": in_utm(KOCHI, plots), "roads": in_utm(KOCHI, roads)}, epsg=KOCHI.epsg)
    shp_zip("world_plots.zip", {
        "plots": [("london_1km", LONDON.ll(PLOT_1KM), {}),
                  ("sydney_1km", SYDNEY.ll(PLOT_1KM), {}),
                  ("kochi_1km",  KOCHI.ll(PLOT_1KM),  {})],
        "long_line": [("line_70E_to_80E", LineString([(70, 10), (80, 10)]), {})],
    })
    shp_zip("nested_folders.zip", {"plots": in_ll(KOCHI, plots)}, prefix="data/survey_2024/")
    shp_zip("edge_cases.zip", {
        "edge": in_ll(KOCHI, [("plot_ok", PLOT_1KM, {}),
                              ("plot_bowtie", PLOT_BOW, {}),
                              ("no_geom", None, {})]),
        "edge_3d": [("plot_3d", shapely.force_3d(KOCHI.ll(square(500)), 7.0), {})],
    })

    # ---- Shapefile zips: should end up FAILED with a clear error_message
    p = {"plots": in_ll(KOCHI, plots)}
    shp_zip("no_prj.zip",      p, drop={".prj"}, status="FAILED", why="no CRS; never guess")
    shp_zip("missing_dbf.zip", p, drop={".dbf"}, status="FAILED", why="message should name .dbf")
    shp_zip("missing_shx.zip", p, drop={".shx"}, status="FAILED", why="message should name .shx")
    shp_zip("zip_slip.zip", p, extra={"../../evil.txt": "zip-slip guard failed if this exists"},
            status="FAILED", why="entry escapes the extract dir; check no evil.txt appeared on disk")
    with zipfile.ZipFile(OUT / "no_shp.zip", "w") as z:
        z.writestr("readme.txt", "no shapefile in here")
        z.writestr("data.csv", "a,b\n1,2\n")
    EXPECTED["no_shp.zip"] = {"status": "FAILED", "why": "No .shp file found in zip"}
    (OUT / "not_a_zip.zip").write_text("hello, I am plain text with a .zip extension")
    EXPECTED["not_a_zip.zip"] = {"status": "FAILED", "why": "bad zip"}

    # ---- Rejected at upload time
    with tempfile.TemporaryDirectory() as td:
        write_layer(Path(td), "plots", in_ll(KOCHI, plots), 4326)
        shutil.copy(Path(td) / "plots.shp", OUT / "lonely.shp")
    EXPECTED["lonely.shp"] = {"http": 400, "why": "raw .shp must be zipped"}
    (OUT / "report.pdf").write_bytes(b"%PDF-1.4\n% not a geo file\n")
    EXPECTED["report.pdf"] = {"http": 400}
    with open(OUT / "too_big.zip", "wb") as f:
        for _ in range(TOO_BIG_MB):
            f.write(b"\0" * 1024 * 1024)
    EXPECTED["too_big.zip"] = {"http": 413}

    (OUT / "expected.json").write_text(json.dumps(EXPECTED, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    for f, e in EXPECTED.items():
        print(f"{f:26} -> {e.get('http') or e['status']}")

if __name__ == "__main__":
    main()