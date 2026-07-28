"""Health endpoint and launch helpers for the persistent contact worker."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import urlopen


WORKER_SERVICE_NAME = "creator-contact-worker"
WORKER_WAITING_STEP = "等待常驻达人联系 Worker 领取任务"
WORKER_CLAIMED_STEP = "常驻达人联系 Worker 已领取任务"


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class _HealthHandler(BaseHTTPRequestHandler):
    server_version = "CreatorContactWorker/1.0"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path.rstrip("/") not in {"", "/health"}:
            self.send_error(404)
            return
        payload = json.dumps(
            {
                "service": WORKER_SERVICE_NAME,
                "status": "ready",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class WorkerHealthServer:
    """Hold one fixed loopback port for the lifetime of a worker."""

    def __init__(self, host: str, port: int):
        self.server = _ReusableThreadingHTTPServer(
            (host, port),
            _HealthHandler,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="creator-contact-worker-health",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def worker_endpoint_ready(
    host: str,
    port: int,
    *,
    timeout_seconds: float = 0.5,
) -> bool:
    """Only accept the expected worker health response, not any open port."""
    try:
        with urlopen(
            f"http://{host}:{port}/health",
            timeout=timeout_seconds,
        ) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("service") == WORKER_SERVICE_NAME
        and payload.get("status") == "ready"
    )


def wait_for_worker_endpoint(
    host: str,
    port: int,
    *,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if worker_endpoint_ready(host, port):
            return True
        time.sleep(0.1)
    return worker_endpoint_ready(host, port)
