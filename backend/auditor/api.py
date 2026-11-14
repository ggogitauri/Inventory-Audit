"""HTTP API.  Run:  uvicorn auditor.api:app --host 0.0.0.0 --port 8000

DB location comes from the AUDITOR_DB env var (default: ./auditor.db).
"""
from __future__ import annotations

import os
import re
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .change_detection import ChangeConfig, Detection
from .contract import ALGORITHM_VERSION, CONTRACT_VERSION
from .report import render_report_html
from .service import promote_patrol, run_comparison
from .store import Conflict, NotFound, Store

app = FastAPI(title="Go2 Site Auditor", version=ALGORITHM_VERSION)
# Permissive by default for local development; set AUDITOR_CORS_ORIGINS=https://host,...
_origins = os.environ.get("AUDITOR_CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _origins == "*" else [o.strip() for o in _origins.split(",") if o.strip()],
    allow_methods=["*"], allow_headers=["*"],
)

APP_DIR = Path(os.environ.get("AUDITOR_APP_DIR",
                              Path(__file__).resolve().parents[2] / "app"))
if (APP_DIR / "index.html").exists():
    app.mount("/app", StaticFiles(directory=str(APP_DIR), html=True), name="app")

REQUEST_COUNTS: Counter = Counter()


@app.middleware("http")
async def auth_and_metrics(request: Request, call_next):
    """Optional API key + tiny request counter.

    Set AUDITOR_API_KEY to require `X-API-Key: ...` (or ?api_key=...) on everything
    except the health check and the review UI. Unset means open local development.
    """
    key = os.environ.get("AUDITOR_API_KEY")
    path = request.url.path
    public = path in ("/health", "/") or path.startswith("/app")
    if key and not public:
        supplied = request.headers.get("x-api-key") or request.query_params.get("api_key")
        if supplied != key:
            REQUEST_COUNTS[(request.method, 401)] += 1
            return JSONResponse({"detail": "invalid or missing API key"}, status_code=401)
    response = await call_next(request)
    REQUEST_COUNTS[(request.method, response.status_code)] += 1
    return response


@contextmanager
def open_store():
    store = Store(os.environ.get("AUDITOR_DB", "auditor.db"))
    try:
        yield store
    finally:
        store.close()


_MEDIA_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$")
_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def media_dir() -> Path:
    return Path(os.environ.get("AUDITOR_MEDIA", "media")).resolve()


def _media_path(key: str) -> Path:
    if len(key) > 200 or not _MEDIA_KEY.match(key):
        raise HTTPException(400, "invalid media key")
    return media_dir() / key


class DetectionIn(BaseModel):
    cls: str
    x: float
    y: float
    conf: float = Field(1.0, ge=0, le=1)
    image: str | None = None
    pose_sigma: float | None = Field(None, ge=0)
    embedding: list[float] | None = None

    def to_detection(self) -> Detection:
        return Detection(self.cls, self.x, self.y, self.conf, image=self.image,
                         pose_sigma=self.pose_sigma,
                         embedding=tuple(self.embedding) if self.embedding is not None else None)


class PatrolIn(BaseModel):
    area: str
    name: str
    uid: str | None = None                 # client idempotency key
    map_version: str | None = None         # maps are only compared within a version
    schema_version: int = CONTRACT_VERSION
    meta: dict | None = None               # detector version, robot, notes, ...


class PassIn(BaseModel):
    detections: list[DetectionIn]
    uid: str | None = None                 # client idempotency key
    captured_at: float | None = None
    pose: dict | None = None               # {x,y,theta,sigma,...} in map frame
    meta: dict | None = None


class CompareIn(BaseModel):
    baseline_id: int | None = None   # default: previous patrol in the same area
    baseline_mode: Literal["previous", "state"] | None = None
    match_radius: float | None = Field(None, gt=0)
    move_threshold: float | None = Field(None, gt=0)
    relocate_radius: float | None = Field(None, ge=0)
    min_conf: float | None = Field(None, ge=0, le=1)
    correct_drift: bool | None = None
    drift_max_shift: float | None = Field(None, gt=0)
    drift_max_rotation: float | None = Field(None, ge=0)
    drift_min_pairs: int | None = Field(None, ge=3)
    appearance_weight: float | None = Field(None, ge=0)
    appearance_scale: float | None = Field(None, gt=0)


class PromoteIn(BaseModel):
    min_votes: int | None = Field(None, ge=1)


class ReviewIn(BaseModel):
    status: Literal["open", "confirmed", "dismissed"]
    note: str | None = None


class ZoneIn(BaseModel):
    x: float
    y: float
    radius: float = Field(..., gt=0)
    label: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index():
    p = APP_DIR / "index.html"
    if p.exists():
        return FileResponse(p)
    return JSONResponse({"service": "Go2 Site Auditor", "docs": "/docs"})


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    lines = ["# HELP auditor_http_requests_total HTTP requests by method and status.",
             "# TYPE auditor_http_requests_total counter"]
    for (method, status), n in sorted(REQUEST_COUNTS.items()):
        lines.append(f'auditor_http_requests_total{{method="{method}",status="{status}"}} {n}')
    return "\n".join(lines) + "\n"


@app.get("/schema")
def schema():
    """Machine-readable contract summary for the robot-side uploader."""
    return {
        "contract_version": CONTRACT_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "patrol": {"area": "str", "name": "str", "uid": "str?", "map_version": "str?",
                   "schema_version": "int", "meta": "object?"},
        "pass": {"uid": "str?", "captured_at": "float?", "pose": "object?",
                 "meta": "object?", "detections": "Detection[]"},
        "detection": {"cls": "str", "x": "float", "y": "float", "conf": "float",
                      "image": "str?", "pose_sigma": "float?", "embedding": "float[]?"},
        "media": {"upload": "POST /media/{key} (raw bytes, image/*)",
                  "read": "GET /media/{key}"},
    }


@app.post("/media/{key:path}", status_code=201)
async def upload_media(key: str, request: Request):
    """Upload the crop/frame that supports a detection. Body is the raw image bytes.

    The client chooses a collision-free key (e.g. `<patrol_uid>/<pass_idx>/<det_id>.jpg`)
    and puts that key in `Detection.image`.
    """
    path = _media_path(key)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in _IMAGE_TYPES:
        raise HTTPException(415, f"unsupported content type {ctype!r}; use {sorted(_IMAGE_TYPES)}")
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty body")
    limit = int(os.environ.get("AUDITOR_MAX_IMAGE_BYTES", 8 * 1024 * 1024))
    if len(data) > limit:
        raise HTTPException(413, f"image exceeds {limit} bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"key": key, "url": f"/media/{key}", "bytes": len(data), "content_type": ctype}


@app.get("/media/{key:path}")
def get_media(key: str):
    path = _media_path(key)
    if not path.is_file():
        raise HTTPException(404, f"media {key}")
    return FileResponse(path)


@app.post("/patrols", status_code=201)
def create_patrol(body: PatrolIn):
    with open_store() as s:
        if body.uid:
            existing = s.find_patrol_by_uid(body.uid)
            if existing is not None:
                if existing["area"] != body.area or existing["name"] != body.name:
                    raise HTTPException(409, f"patrol uid {body.uid} already used for "
                                             f"{existing['area']}/{existing['name']}")
                return existing
        pid = s.create_patrol(body.area, body.name, uid=body.uid,
                              map_version=body.map_version,
                              schema_version=body.schema_version, meta=body.meta)
        return s.get_patrol(pid)


@app.get("/patrols")
def list_patrols(area: str | None = Query(None)):
    with open_store() as s:
        return s.list_patrols(area)


@app.get("/patrols/{patrol_id}")
def get_patrol(patrol_id: int):
    with open_store() as s:
        try:
            return s.get_patrol(patrol_id)
        except NotFound as e:
            raise HTTPException(404, str(e))


@app.get("/patrols/{patrol_id}/passes")
def get_passes(patrol_id: int):
    with open_store() as s:
        try:
            return s.get_pass_records(patrol_id)
        except NotFound as e:
            raise HTTPException(404, str(e))


@app.post("/patrols/{patrol_id}/passes", status_code=201)
def add_pass(patrol_id: int, body: PassIn):
    with open_store() as s:
        try:
            idx = s.add_pass(patrol_id, [d.to_detection() for d in body.detections],
                             uid=body.uid, captured_at=body.captured_at,
                             pose=body.pose, meta=body.meta)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except Conflict as e:
            raise HTTPException(409, str(e))
        return {"patrol_id": patrol_id, "pass_idx": idx, "detections": len(body.detections)}


@app.post("/patrols/{patrol_id}/compare", status_code=201)
def compare(patrol_id: int, body: CompareIn | None = None):
    body = body or CompareIn()
    cfg = ChangeConfig()
    for field in ("match_radius", "move_threshold", "relocate_radius", "min_conf",
                  "correct_drift", "drift_max_shift", "drift_max_rotation", "drift_min_pairs",
                  "appearance_weight", "appearance_scale"):
        v = getattr(body, field)
        if v is not None:
            setattr(cfg, field, v)
    with open_store() as s:
        try:
            return run_comparison(s, patrol_id, body.baseline_id, cfg,
                                  baseline_mode=body.baseline_mode)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))


@app.get("/reports")
def list_reports(area: str | None = Query(None), status: str | None = Query(None),
                 limit: int = Query(100, ge=1, le=500)):
    with open_store() as s:
        return s.list_reports(area=area, status=status, limit=limit)


@app.get("/reports/{report_id}")
def get_report(report_id: int):
    with open_store() as s:
        try:
            return s.get_report(report_id)
        except NotFound as e:
            raise HTTPException(404, str(e))


@app.post("/reports/{report_id}/review")
def review_report(report_id: int, body: ReviewIn):
    with open_store() as s:
        try:
            return s.review_report(report_id, body.status, body.note)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))


@app.post("/patrols/{patrol_id}/promote")
def promote(patrol_id: int, body: PromoteIn | None = None):
    """Promote a patrol to the area's known state (expected inventory)."""
    body = body or PromoteIn()
    with open_store() as s:
        try:
            return promote_patrol(s, patrol_id, body.min_votes)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))


@app.get("/areas/{area}/state")
def get_area_state(area: str):
    with open_store() as s:
        state = s.get_area_state(area)
        if state is None:
            raise HTTPException(404, f"area {area} has no known state")
        return state


@app.delete("/areas/{area}/state", status_code=204)
def clear_area_state(area: str):
    with open_store() as s:
        s.clear_area_state(area)
    return None


@app.get("/areas/{area}/ignore-zones")
def list_ignore_zones(area: str):
    with open_store() as s:
        return s.list_ignore_zones(area)


@app.post("/areas/{area}/ignore-zones", status_code=201)
def add_ignore_zone(area: str, body: ZoneIn):
    with open_store() as s:
        zid = s.add_ignore_zone(area, body.x, body.y, body.radius, body.label)
        return {"id": zid, "area": area, **body.model_dump()}


@app.delete("/ignore-zones/{zone_id}", status_code=204)
def delete_ignore_zone(zone_id: int):
    with open_store() as s:
        if not s.delete_ignore_zone(zone_id):
            raise HTTPException(404, f"ignore zone {zone_id}")
    return None


@app.get("/reports/{report_id}/html", response_class=HTMLResponse)
def get_report_html(report_id: int, request: Request):
    token = request.query_params.get("api_key") or request.headers.get("x-api-key")
    with open_store() as s:
        try:
            r = s.get_report(report_id)
            return render_report_html(r, s.get_patrol(r["baseline_id"]),
                                      s.get_patrol(r["current_id"]), media_token=token)
        except NotFound as e:
            raise HTTPException(404, str(e))
