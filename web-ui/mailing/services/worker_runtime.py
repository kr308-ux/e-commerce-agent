"""Loopback health endpoint for the persistent email worker."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import urlopen


EMAIL_WORKER_SERVICE_NAME = "creator-email-worker"


class _ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") not in {"", "/health"}:
            self.send_error(404)
            return
        body = json.dumps(
            {"service": EMAIL_WORKER_SERVICE_NAME, "status": "ready"}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class EmailWorkerHealthServer:
    def __init__(self, host: str, port: int):
        self.server = _ReusableServer((host, port), _HealthHandler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="creator-email-worker-health",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def email_worker_endpoint_ready(
    host: str,
    port: int,
    *,
    timeout_seconds: float = 0.5,
) -> bool:
    try:
        with urlopen(
            f"http://{host}:{port}/health",
            timeout=timeout_seconds,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False
    return (
        response.status == 200
        and payload.get("service") == EMAIL_WORKER_SERVICE_NAME
        and payload.get("status") == "ready"
    )
