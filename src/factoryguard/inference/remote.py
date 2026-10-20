"""HTTP client for a shared central inference service."""

from __future__ import annotations

import base64
import json
import zlib
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

from factoryguard.domain.models import Detection, Frame

from .base import InferenceHealth


class CentralInferenceClient:
    """Send frames to one shared inference host over the local network."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        timeout: float = 10.0,
        provider_label: str = "central",
    ) -> None:
        normalized = base_url.strip().rstrip("/")
        if not normalized.lower().startswith(("http://", "https://")):
            raise ValueError("central inference URL must use HTTP or HTTPS")
        if timeout <= 0:
            raise ValueError("central inference timeout must be positive")
        self.base_url = normalized
        self.api_key = api_key
        self.timeout = timeout
        self.provider_label = provider_label

    def health_check(self) -> InferenceHealth:
        try:
            payload = self._request("GET", "/health")
        except (OSError, RuntimeError, ValueError, URLError) as exc:
            return InferenceHealth("degraded", self.provider_label, detail=self._redact(str(exc)))
        return InferenceHealth(
            str(payload.get("state", "degraded")),
            str(payload.get("provider", self.provider_label)),
            model_version=str(payload.get("model_version", "")) or None,
            detail=str(payload.get("detail", "")) or None,
        )

    def reset(self) -> None:
        self.health_check()

    def infer(self, frame: Frame) -> tuple[Detection, ...]:
        if frame.image is None:
            raise ValueError("central inference requires a decoded frame image")
        return self.infer_image(frame.image, stream_id=frame.stream_id)

    def infer_image(self, image: np.ndarray, *, stream_id: str = "") -> tuple[Detection, ...]:
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("image must be an HxWx3 array")
        payload = {
            "stream_id": stream_id,
            "shape": list(image.shape),
            "dtype": str(image.dtype),
            "encoding": "zlib+base64",
            "frame": base64.b64encode(zlib.compress(image.tobytes(), level=3)).decode("ascii"),
        }
        response = self._request("POST", "/v1/infer", payload)
        detections = response.get("detections", ())
        if not isinstance(detections, list):
            raise ValueError("central inference response must contain a detections list")
        return tuple(
            Detection(
                label=str(item["label"]),
                confidence=float(item["confidence"]),
                bbox=tuple(float(value) for value in item["bbox"]),
                track_id=str(item["track_id"]) if item.get("track_id") else None,
            )
            for item in detections
            if isinstance(item, dict)
        )

    def _request(self, method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"central inference HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"central inference unavailable: {exc.reason}") from exc
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("central inference returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("central inference returned a non-object response")
        return value

    def _redact(self, detail: str) -> str:
        return detail.replace(self.api_key, "***") if self.api_key else detail


__all__ = ["CentralInferenceClient"]
