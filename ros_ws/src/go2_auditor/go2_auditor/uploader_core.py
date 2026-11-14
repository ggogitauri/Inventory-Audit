"""Queueing logic that turns detection messages into API calls. Pure Python (no ROS).

Guarantees and limits
- Order is preserved: create patrol -> passes -> compare.
- If the server is unreachable, jobs stay queued and are retried by pump() (forever).
- A job the server rejects (HTTP 4xx/5xx) is logged and dropped so it cannot block the queue.
- Delivery is at-least-once: if a request succeeds but its reply is lost, the retry can create a
  duplicate patrol or pass. Acceptable for a prototype; fix with idempotency keys later.
"""
from __future__ import annotations

from collections import deque

from .api_client import ApiClient, ApiError, ApiUnavailable


class _PrintLog:
    def info(self, m): print("[info]", m)
    def warn(self, m): print("[warn]", m)
    def error(self, m): print("[error]", m)


class UploaderCore:
    def __init__(self, client: ApiClient, auto_compare: bool = True, log=None):
        self.client = client
        self.auto_compare = auto_compare
        self.log = log or _PrintLog()
        self.jobs: deque = deque()
        self.patrol_ids: dict = {}
        self.reports: list = []
        self._queued_patrols: set = set()
        self._seen_passes: set = set()
        self._compared_patrols: set = set()
        self._warned_down = False

    # -- input -----------------------------------------------------------
    def handle(self, msg: dict) -> None:
        missing = [k for k in ("area", "patrol", "pass_idx", "detections") if k not in msg]
        if missing or not isinstance(msg.get("detections"), list):
            self.log.error(f"ignoring malformed message (missing/invalid: {missing or 'detections'})")
            return
        # Prefer the client-generated patrol uid: it is stable across retries and makes a
        # same-named patrol on a later day a distinct patrol.
        patrol_uid = msg.get("patrol_uid") or str(msg["patrol"])
        key = (str(msg["area"]), patrol_uid)
        seen = (key, int(msg["pass_idx"]))
        if seen in self._seen_passes:
            return                                       # duplicate delivery
        self._seen_passes.add(seen)
        if key not in self._queued_patrols:
            self._queued_patrols.add(key)
            self.jobs.append({
                "op": "create", "key": key,
                "body": {
                    "area": str(msg["area"]), "name": str(msg["patrol"]),
                    "uid": msg.get("patrol_uid"),
                    "map_version": msg.get("map_version"),
                    "schema_version": msg.get("schema_version", 2),
                    "meta": msg.get("patrol_meta"),
                },
            })
        dets = [{k: d[k] for k in ("cls", "x", "y", "conf", "image", "pose_sigma", "embedding")
                 if k in d} for d in msg["detections"]]
        self.jobs.append({
            "op": "pass", "key": key, "idx": int(msg["pass_idx"]), "detections": dets,
            "body": {
                "uid": msg.get("pass_uid"),
                "captured_at": msg.get("captured_at"),
                "pose": msg.get("pose"),
                "meta": msg.get("pass_meta"),
                "detections": dets,
            },
        })
        if msg.get("last") and self.auto_compare and key not in self._compared_patrols:
            self._compared_patrols.add(key)
            self.jobs.append({"op": "compare", "key": key})
        self.pump()

    # -- output ----------------------------------------------------------
    def pump(self) -> bool:
        """Send as many queued jobs as possible. Returns True when the queue is empty."""
        while self.jobs:
            job = self.jobs[0]
            try:
                self._run(job)
            except ApiUnavailable as e:
                if not self._warned_down:
                    self.log.warn(f"API unreachable ({e}); {len(self.jobs)} job(s) queued, will retry")
                    self._warned_down = True
                return False
            except ApiError as e:
                if job["op"] == "compare" and e.status == 422:
                    # Precondition not met (e.g. first patrol in an area): the patrol is stored and
                    # can be compared later, so this is not an error.
                    self.log.warn(f"comparison skipped: {e.detail}")
                else:
                    self.log.error(f"dropping {job['op']} job: {e}")
            if self._warned_down:
                self.log.info("API reachable again")
                self._warned_down = False
            self.jobs.popleft()
        return True

    def _run(self, job: dict) -> None:
        key = job["key"]
        if job["op"] == "create":
            body = job.get("body") or {"area": key[0], "name": key[1]}
            res = self.client.request("POST", "/patrols", body)
            self.patrol_ids[key] = res["id"]
            self.log.info(f"created patrol {res['id']} ({key[0]}/{body.get('name', key[1])})")
            return
        pid = self.patrol_ids.get(key)
        if pid is None:
            raise ApiError(0, f"no patrol id for {key} (creation failed earlier)")
        if job["op"] == "pass":
            body = job.get("body") or {"detections": job["detections"]}
            self.client.request("POST", f"/patrols/{pid}/passes", body)
            self.log.info(f"uploaded pass {job['idx']} ({len(job['detections'])} detections) to patrol {pid}")
        elif job["op"] == "compare":
            rep = self.client.request("POST", f"/patrols/{pid}/compare", {})
            self.reports.append(rep)
            s = rep["summary"]
            self.log.info(f"comparison done: moved={s['moved']} missing={s['missing']} new={s['new']} "
                          f"unchanged={s['unchanged']}")
            self.log.info(f"Report ready: {self.client.base_url}/reports/{rep['id']}/html")
