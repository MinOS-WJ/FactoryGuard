"""Low-cost local HTTP inference server for FactoryGuard deployments."""

from __future__ import annotations

import base64
import hmac
import json
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import numpy as np


class CentralInferenceServer:
    """Serve one shared model engine to several thin FactoryGuard clients."""

    def __init__(
        self,
        engine: Any,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        api_key: str = "",
        max_body_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        if not 0 <= int(port) <= 65535:
            raise ValueError("inference server port must be between 0 and 65535")
        if max_body_bytes < 1024:
            raise ValueError("inference server max body is too small")
        self.engine = engine
        self.host = host
        self.port = int(port)
        self.api_key = api_key
        self.max_body_bytes = max_body_bytes
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            return self.host, self.port
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    @property
    def url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}"

    def start(self) -> None:
        if self._server is not None:
            return
        self._create_server()
        assert self._server is not None
        self._thread = threading.Thread(target=self._server.serve_forever, name="central-inference", daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        if self._server is None:
            self._create_server()
        assert self._server is not None
        self._server.serve_forever()

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    def health_payload(self) -> dict[str, object]:
        health = self.engine.health_check()
        return {
            "state": health.state,
            "provider": health.provider,
            "model_version": health.model_version,
            "detail": health.detail,
        }

    def infer_payload(self, payload: dict[str, Any]) -> dict[str, object]:
        if payload.get("encoding") != "zlib+base64":
            raise ValueError("unsupported frame encoding")
        shape = tuple(int(value) for value in payload.get("shape", ()))
        if len(shape) != 3 or shape[2] != 3 or min(shape) <= 0:
            raise ValueError("frame shape must be HxWx3")
        dtype = np.dtype(str(payload.get("dtype", "uint8")))
        raw = zlib.decompress(base64.b64decode(str(payload.get("frame", ""))))
        expected = int(np.prod(shape)) * dtype.itemsize
        if len(raw) != expected:
            raise ValueError("frame byte length does not match shape and dtype")
        image = np.frombuffer(raw, dtype=dtype).reshape(shape).copy()
        with self._lock:
            detections = self.engine.infer_image(image)
        return {
            "detections": [
                {
                    "label": value.label,
                    "confidence": value.confidence,
                    "bbox": list(value.bbox),
                    "track_id": value.track_id,
                }
                for value in detections
            ]
        }

    def _create_server(self) -> None:
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                if self.path.rstrip("/") != "/health":
                    self._send_json(404, {"error": "not found"})
                    return
                if not self._authorized():
                    return
                self._send_json(200, service.health_payload())

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                if self.path.rstrip("/") != "/v1/infer":
                    self._send_json(404, {"error": "not found"})
                    return
                if not self._authorized():
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send_json(400, {"error": "invalid content length"})
                    return
                if length <= 0 or length > service.max_body_bytes:
                    self._send_json(413, {"error": "request body is empty or too large"})
                    return
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("request body must be a JSON object")
                    response = service.infer_payload(payload)
                except (ValueError, KeyError, TypeError, zlib.error) as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                except Exception as exc:  # noqa: BLE001 - report model failures to the caller
                    self._send_json(500, {"error": str(exc)})
                    return
                self._send_json(200, response)

            def _authorized(self) -> bool:
                if not service.api_key:
                    return True
                expected = f"Bearer {service.api_key}"
                supplied = self.headers.get("Authorization", "")
                if hmac.compare_digest(supplied, expected):
                    return True
                self._send_json(401, {"error": "unauthorized"})
                return False

            def _send_json(self, status: int, payload: dict[str, object]) -> None:
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True


__all__ = ["CentralInferenceServer"]
