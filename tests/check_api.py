#!/usr/bin/env python3
"""Upload every fixture and compare the API with expected.json.

    uv run python tests/check_api.py [http://localhost:8000] [-v]

Run from the project root, after tests/make_test_files.py.
"""
import json
import sys
import time
import traceback
import uuid
from pathlib import Path

import httpx

ARGS = [a for a in sys.argv[1:] if not a.startswith("-")]
VERBOSE = "-v" in sys.argv
BASE = (ARGS[0] if ARGS else "http://localhost:8000").rstrip("/") + "/api/files/"
ROOT = BASE.rsplit("/api/", 1)[0]

FIELD = "file"     # multipart field name your POST endpoint expects
REL_TOL = 1e-4     # 0.01 %
DIR = Path("test_files")
EXP = json.loads((DIR / "expected.json").read_text(encoding="utf-8"))
IDS: dict[str, str] = {}
C = httpx.Client(follow_redirects=True, timeout=120)


# ------------------------------------------------------------------ helpers
def get(url, **kw):
    r = C.get(url, **kw)
    if r.history:  # a redirect happened
        print(f"     ⚠ {url} redirected ({r.history[0].status_code}) -> fix your route path")
    return r


def get_json(url, **kw):
    r = get(url, **kw)
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"GET {url} -> HTTP {r.status_code}, body: {r.text[:300]!r}")


def close(a, b):
    return a is not None and abs(a - b) <= REL_TOL * max(abs(b), 1.0)


def poll(fid, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        info = get_json(f"{BASE}{fid}/")
        if info.get("status") in ("COMPLETED", "FAILED"):
            return info
        time.sleep(0.3)
    raise TimeoutError(f"{fid} still not COMPLETED/FAILED after {timeout}s")


def all_results(fid):
    out, offset = [], 0
    while True:
        page = get_json(f"{BASE}{fid}/measurements/", params={"limit": 1000, "offset": offset})
        out += page["results"]
        offset += len(page["results"])
        if not page["results"] or offset >= page["total"]:
            return out


def feature_name(row):
    props = row.get("properties") or {}
    return props.get("name") or props.get("Name")


# -------------------------------------------------------------- per-file check
def check(fname, exp):
    with open(DIR / fname, "rb") as f:
        r = C.post(BASE, files={FIELD: (fname, f)})

    # files that must be rejected at upload time
    if "http" in exp:
        if r.status_code == exp["http"]:
            return []
        return [f"expected HTTP {exp['http']}, got {r.status_code}: {r.text[:200]}"]

    if r.status_code != 202:
        return [f"upload returned {r.status_code} (wanted 202): {r.text[:200]}"]

    info = poll(r.json()["id"])
    IDS[fname] = info["id"]

    want = exp["status"] if isinstance(exp["status"], list) else [exp["status"]]
    if info["status"] not in want:
        return [f"status {info['status']} (wanted {want}); error_message={info.get('error_message')!r}"]

    # files that must fail during processing
    if info["status"] == "FAILED":
        probs = [] if info.get("error_message") else ["FAILED without an error_message"]
        code = get(f"{BASE}{info['id']}/measurements/").status_code
        if code != 409:
            probs.append(f"measurements on a FAILED file gave {code}, wanted 409")
        return probs

    # files that must complete
    probs = []
    for k in ("feature_count", "crs"):
        if k in exp and info.get(k) != exp[k]:
            probs.append(f"{k}: got {info.get(k)!r}, expected {exp[k]!r}")

    rows = {feature_name(row): row for row in all_results(info["id"])}

    for name, e in exp.get("features", {}).items():
        row = rows.get(name)
        if row is None:
            probs.append(f"{name}: missing from results")
            continue
        m = row.get("measurement") or {}

        if not e["measured"]:
            if m.get("area_m2") is not None or m.get("length_m") is not None:
                probs.append(f"{name}: should not be measured, got {m}")
            if e["expect_note"] and not m.get("note"):
                probs.append(f"{name}: expected an explanatory note")
            continue

        if m.get("measurement_crs") != e["measurement_crs"]:
            probs.append(f"{name}: measurement_crs {m.get('measurement_crs')!r} != {e['measurement_crs']!r}")
        for k in ("area_m2", "length_m", "geodesic_area_m2", "geodesic_length_m"):
            if k in e and not close(m.get(k), e[k]):
                probs.append(f"{name}.{k}: got {m.get(k)}, expected {e[k]}")
    return probs


# ------------------------------------------------------------- extra API checks
def extras():
    bad = str(uuid.uuid4())
    yield "404 on unknown file", get(f"{BASE}{bad}/").status_code == 404
    yield "404 on unknown measurements", get(f"{BASE}{bad}/measurements/").status_code == 404

    if fid := IDS.get("many_plots.kml"):
        page = get_json(f"{BASE}{fid}/measurements/", params={"limit": 100, "offset": 2450})
        yield "pagination tail (total 2500, 50 left)", page["total"] == 2500 and len(page["results"]) == 50
        code = get(f"{BASE}{fid}/measurements/", params={"limit": 1001}).status_code
        yield "limit=1001 rejected with 422", code == 422
        code = get(f"{BASE}{fid}/measurements/", params={"offset": -1}).status_code
        yield "offset=-1 rejected with 422", code == 422

    if fid := IDS.get("kochi_farms_4326.zip"):
        page = get_json(f"{BASE}{fid}/measurements/", params={"geometry_type": "LineString"})
        ok = page["total"] == 2 and all(r["geometry_type"] == "LineString" for r in page["results"])
        yield "geometry_type=LineString filter (2 rows)", ok


# --------------------------------------------------------------------- main
def main():
    try:
        C.get(f"{ROOT}/docs")
    except httpx.ConnectError:
        sys.exit(f"Can't reach {ROOT}. Is uvicorn running?")

    print(f"Testing {BASE}\n")
    failed = 0
    for fname, exp in EXP.items():
        try:
            probs = check(fname, exp)
        except Exception as e:  # keep going; report the crash for this file
            probs = [f"checker error: {e}"]
            if VERBOSE:
                traceback.print_exc()
        failed += bool(probs)
        print(("✅ " if not probs else "❌ ") + fname)
        for p in probs:
            print("     -", p)

    print()
    try:
        for label, ok in extras():
            failed += not ok
            print(("✅ " if ok else "❌ ") + label)
    except Exception as e:
        failed += 1
        print(f"❌ extra checks crashed: {e}")
        if VERBOSE:
            traceback.print_exc()

    print(f"\n{failed} problem(s).")
    print("Manual check: make sure no evil.txt appeared outside your temp/upload dirs:")
    print("  find ~ /tmp -name evil.txt 2>/dev/null")


if __name__ == "__main__":
    main()