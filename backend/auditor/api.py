"""HTTP API.  Run:  uvicorn auditor.api:app --host 0.0.0.0 --port 8000

DB location comes from the AUDITOR_DB env var (default: ./auditor.db).
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .change_detection import ChangeConfig, Detection
from .report import render_report_html
from .service import run_comparison
from .store import NotFound, Store

app = FastAPI(title="Go2 Site Auditor")
# Permissive CORS for local development of the web app; tighten before any real deployment.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@contextmanager
def open_store():
    store = Store(os.environ.get("AUDITOR_DB", "auditor.db"))
    try:
        yield store
    finally:
        store.close()


class DetectionIn(BaseModel):
    cls: str
    x: float
    y: float
    conf: float = Field(1.0, ge=0, le=1)
    image: str | None = None


class PatrolIn(BaseModel):
    area: str
    name: str


class PassIn(BaseModel):
    detections: list[DetectionIn]


class CompareIn(BaseModel):
    baseline_id: int | None = None   # default: previous patrol in the same area
    match_radius: float | None = Field(None, gt=0)
    move_threshold: float | None = Field(None, gt=0)
    relocate_radius: float | None = Field(None, ge=0)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/patrols", status_code=201)
def create_patrol(body: PatrolIn):
    with open_store() as s:
        pid = s.create_patrol(body.area, body.name)
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


@app.post("/patrols/{patrol_id}/passes", status_code=201)
def add_pass(patrol_id: int, body: PassIn):
    with open_store() as s:
        try:
            idx = s.add_pass(patrol_id, [Detection(**d.model_dump()) for d in body.detections])
        except NotFound as e:
            raise HTTPException(404, str(e))
        return {"patrol_id": patrol_id, "pass_idx": idx, "detections": len(body.detections)}


@app.post("/patrols/{patrol_id}/compare", status_code=201)
def compare(patrol_id: int, body: CompareIn | None = None):
    body = body or CompareIn()
    cfg = ChangeConfig()
    for field in ("match_radius", "move_threshold", "relocate_radius"):
        v = getattr(body, field)
        if v is not None:
            setattr(cfg, field, v)
    with open_store() as s:
        try:
            return run_comparison(s, patrol_id, body.baseline_id, cfg)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))


@app.get("/reports/{report_id}")
def get_report(report_id: int):
    with open_store() as s:
        try:
            return s.get_report(report_id)
        except NotFound as e:
            raise HTTPException(404, str(e))


@app.get("/reports/{report_id}/html", response_class=HTMLResponse)
def get_report_html(report_id: int):
    with open_store() as s:
        try:
            r = s.get_report(report_id)
            return render_report_html(r, s.get_patrol(r["baseline_id"]), s.get_patrol(r["current_id"]))
        except NotFound as e:
            raise HTTPException(404, str(e))
