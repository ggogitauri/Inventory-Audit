import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from go2_auditor.api_client import ApiClient
from go2_auditor.uploader_core import UploaderCore


class Stub:
    """Records requests and answers like the real API's happy path (or a configured error)."""

    def __init__(self, compare_status=201):
        self.requests = []
        self.compare_status = compare_status
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n) or b"null"
                if self.path.startswith("/media/"):
                    stub.requests.append((self.path, raw))
                    out = {"key": self.path[len("/media/"):], "bytes": len(raw)}
                    status = 201
                else:
                    body = json.loads(raw)
                    stub.requests.append((self.path, body))
                    status, out = 201, {}
                    if self.path == "/patrols":
                        out = {"id": 40 + len([r for r in stub.requests if r[0] == "/patrols"])}
                    elif self.path.endswith("/passes"):
                        out = {"pass_idx": 0}
                    elif self.path.endswith("/compare"):
                        status = stub.compare_status
                        out = {"id": 9, "summary": {"moved": 1, "missing": 0, "new": 0, "unchanged": 5}} \
                            if status == 201 else {"detail": "nothing to compare"}
                raw = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class Log:
    def __init__(self): self.lines = []
    def info(self, m): self.lines.append(("info", m))
    def warn(self, m): self.lines.append(("warn", m))
    def error(self, m): self.lines.append(("error", m))


def free_dead_url():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    return f"http://127.0.0.1:{port}"


def msg(i, n=3, patrol="mon", area="lab"):
    return {"area": area, "patrol": patrol, "pass_idx": i, "n_passes": n, "last": i == n - 1,
            "detections": [{"cls": "chair", "x": 1.0, "y": 2.0, "conf": 0.9}]}


def test_happy_path_order_and_ids():
    stub = Stub()
    try:
        core = UploaderCore(ApiClient(stub.url), log=Log())
        for i in range(3):
            core.handle(msg(i))
        paths = [p for p, _ in stub.requests]
        assert paths == ["/patrols", "/patrols/41/passes", "/patrols/41/passes", "/patrols/41/passes",
                         "/patrols/41/compare"]
        assert core.reports[0]["id"] == 9 and not core.jobs
    finally:
        stub.stop()


def test_outage_queues_then_delivers_in_order():
    stub = Stub()
    try:
        log = Log()
        core = UploaderCore(ApiClient(free_dead_url(), timeout=0.5), log=log)
        for i in range(3):
            core.handle(msg(i))
        assert len(core.jobs) == 5 and stub.requests == []          # nothing lost, nothing sent
        assert sum(1 for lvl, _ in log.lines if lvl == "warn") == 1  # warns once, not per attempt
        core.client.base_url = stub.url                              # network comes back
        assert core.pump() is True
        assert [p for p, _ in stub.requests][0] == "/patrols" and stub.requests[-1][0].endswith("/compare")
        assert len(stub.requests) == 5
    finally:
        stub.stop()


def test_duplicate_pass_is_ignored():
    stub = Stub()
    try:
        core = UploaderCore(ApiClient(stub.url), log=Log())
        core.handle(msg(0)); core.handle(msg(0))
        assert [p for p, _ in stub.requests].count("/patrols/41/passes") == 1
    finally:
        stub.stop()


def test_refused_compare_warns_and_does_not_block():
    stub = Stub(compare_status=422)
    try:
        log = Log()
        core = UploaderCore(ApiClient(stub.url), log=log)
        for i in range(3):
            core.handle(msg(i))
        assert not core.jobs and core.reports == []
        assert any(lvl == "warn" and "comparison skipped" in m for lvl, m in log.lines)
        assert not any(lvl == "error" for lvl, _ in log.lines)
        core.handle(msg(0, patrol="tue"))                            # later patrols still work
        assert stub.requests[-1][0].endswith("/passes")
    finally:
        stub.stop()


def test_malformed_message_ignored():
    stub = Stub()
    try:
        log = Log()
        core = UploaderCore(ApiClient(stub.url), log=log)
        core.handle({"area": "lab"})
        core.handle({"area": "a", "patrol": "b", "pass_idx": 0, "detections": "nope"})
        assert stub.requests == [] and sum(1 for l, _ in log.lines if l == "error") == 2
    finally:
        stub.stop()


def test_auto_compare_off():
    stub = Stub()
    try:
        core = UploaderCore(ApiClient(stub.url), auto_compare=False, log=Log())
        for i in range(3):
            core.handle(msg(i))
        assert not any(p.endswith("/compare") for p, _ in stub.requests)
    finally:
        stub.stop()


def test_other_rejections_are_still_errors():
    stub = Stub(compare_status=500)
    try:
        log = Log()
        core = UploaderCore(ApiClient(stub.url), log=log)
        for i in range(3):
            core.handle(msg(i))
        assert not core.jobs
        assert any(lvl == "error" and "500" in m for lvl, m in log.lines)
    finally:
        stub.stop()


def test_contract_fields_are_forwarded():
    stub = Stub()
    try:
        core = UploaderCore(ApiClient(stub.url), log=Log())
        m = msg(0)
        m.update({"patrol_uid": "patrol-1", "pass_uid": "patrol-1:0", "schema_version": 2,
                  "map_version": "map-1", "captured_at": 123.0,
                  "pose": {"x": 1.0, "y": 2.0, "theta": 0.0},
                  "detections": [{"cls": "chair", "x": 1.0, "y": 2.0, "conf": 0.9,
                                  "pose_sigma": 0.1, "image": "media/crop-1.jpg"}]})
        core.handle(m)
        create = next(b for p, b in stub.requests if p == "/patrols")
        assert create["uid"] == "patrol-1" and create["map_version"] == "map-1"
        assert create["schema_version"] == 2
        upload = next(b for p, b in stub.requests if p.endswith("/passes"))
        assert upload["uid"] == "patrol-1:0" and upload["captured_at"] == 123.0
        assert upload["pose"]["y"] == 2.0
        assert upload["detections"][0]["pose_sigma"] == 0.1
        assert upload["detections"][0]["image"] == "media/crop-1.jpg"
    finally:
        stub.stop()


def test_compare_runs_at_most_once_per_patrol():
    stub = Stub()
    try:
        core = UploaderCore(ApiClient(stub.url), log=Log())
        for i in range(2):
            m = msg(i)
            m["last"] = True                        # a badly-behaved source says "last" twice
            core.handle(m)
        assert [p for p, _ in stub.requests].count("/patrols/41/compare") == 1
    finally:
        stub.stop()


def test_media_upload_sends_raw_bytes():
    stub = Stub()
    try:
        client = ApiClient(stub.url)
        out = client.upload_media("crops/chair 1.jpg", b"\xff\xd8jpeg", "image/jpeg")
        assert out["bytes"] == 6
        assert stub.requests == [("/media/crops/chair%201.jpg", b"\xff\xd8jpeg")]
    finally:
        stub.stop()


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
