"""Tiny JSON-over-HTTP client for the auditor API (standard library only)."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request


class ApiUnavailable(Exception):
    """Could not reach the server (down, no route, timeout). Safe to retry later."""


class ApiError(Exception):
    """Server answered with an error status. Retrying the same request will not help."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base_url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:           # must come before URLError (it is a subclass)
            raise ApiError(e.code, e.read().decode(errors="replace")[:300])
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            raise ApiUnavailable(str(getattr(e, "reason", e)))

    def upload_media(self, key: str, data: bytes, content_type: str = "image/jpeg"):
        """Upload one crop/frame. Put `key` in the detection's `image` field."""
        path = "/media/" + urllib.parse.quote(key, safe="/")
        req = urllib.request.Request(self.base_url + path, data=data, method="POST",
                                     headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, e.read().decode(errors="replace")[:300])
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            raise ApiUnavailable(str(getattr(e, "reason", e)))
