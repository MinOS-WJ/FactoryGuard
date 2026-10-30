"""Local ONNX fallback model profiles and scope bindings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import uuid4


@dataclass(slots=True)
class ModelProfile:
    id: str
    name: str
    task_type: str = "detection"
    backend: str = "onnx_cpu"
    model_uri: str = ""
    input_width: int = 640
    input_height: int = 640
    labels: tuple[str, ...] = ("person",)
    confidence: float = 0.25
    iou: float = 0.45
    version: str = "1.0"
    enabled: bool = True

    def __post_init__(self) -> None:
        self.id = str(self.id).strip()
        self.name = str(self.name).strip()
        self.task_type = str(self.task_type).strip().lower() or "detection"
        self.backend = str(self.backend).strip().lower() or "onnx_cpu"
        self.model_uri = str(self.model_uri).strip()
        self.labels = tuple(str(value).strip() for value in self.labels if str(value).strip())
        self.version = str(self.version).strip() or "1.0"
        if not self.id or not self.name:
            raise ValueError("model profile id and name are required")
        if self.input_width <= 0 or self.input_height <= 0:
            raise ValueError("model input dimensions must be positive")
        if not 0 < float(self.confidence) <= 1 or not 0 < float(self.iou) <= 1:
            raise ValueError("model confidence and IoU must be between 0 and 1")
        if not self.labels:
            raise ValueError("model profile requires at least one label")

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "task_type": self.task_type,
            "backend": self.backend,
            "model_uri": self.model_uri,
            "input_width": self.input_width,
            "input_height": self.input_height,
            "labels": list(self.labels),
            "confidence": float(self.confidence),
            "iou": float(self.iou),
            "version": self.version,
            "enabled": bool(self.enabled),
        }

    @classmethod
    def from_public_dict(cls, value: Mapping[str, Any]) -> ModelProfile:
        return cls(
            id=str(value.get("id", "")),
            name=str(value.get("name", "")),
            task_type=str(value.get("task_type", "detection")),
            backend=str(value.get("backend", "onnx_cpu")),
            model_uri=str(value.get("model_uri", "")),
            input_width=int(value.get("input_width", 640)),
            input_height=int(value.get("input_height", 640)),
            labels=tuple(str(item) for item in value.get("labels", ("person",))),
            confidence=float(value.get("confidence", 0.25)),
            iou=float(value.get("iou", 0.45)),
            version=str(value.get("version", "1.0")),
            enabled=bool(value.get("enabled", True)),
        )


def new_model_profile_id() -> str:
    return f"model-{uuid4().hex[:10]}"


@dataclass(slots=True)
class ModelBinding:
    """Bind a model profile to a site, area, camera or stream."""

    id: str
    model_profile_id: str
    scope_type: str
    scope_id: str
    priority: int = 0
    enabled: bool = True

    def __post_init__(self) -> None:
        self.id = str(self.id).strip()
        self.model_profile_id = str(self.model_profile_id).strip()
        self.scope_type = str(self.scope_type).strip().lower()
        self.scope_id = str(self.scope_id).strip()
        if not self.id or not self.model_profile_id or not self.scope_id:
            raise ValueError("model binding id, model profile and scope are required")
        if self.scope_type not in {"site", "area", "camera", "stream"}:
            raise ValueError("model binding scope must be site, area, camera or stream")
        if isinstance(self.priority, bool):
            raise ValueError("model binding priority must be an integer")

    def matches(self, *, site_id: str | None, area_id: str | None, camera_id: str | None, stream_id: str) -> bool:
        candidates = {
            "site": site_id,
            "area": area_id,
            "camera": camera_id,
            "stream": stream_id,
        }
        return self.enabled and candidates[self.scope_type] == self.scope_id

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "model_profile_id": self.model_profile_id,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "priority": int(self.priority),
            "enabled": bool(self.enabled),
        }

    @classmethod
    def from_public_dict(cls, value: Mapping[str, Any]) -> ModelBinding:
        return cls(
            id=str(value.get("id", "")),
            model_profile_id=str(value.get("model_profile_id", "")),
            scope_type=str(value.get("scope_type", "")),
            scope_id=str(value.get("scope_id", "")),
            priority=int(value.get("priority", 0)),
            enabled=bool(value.get("enabled", True)),
        )


def new_model_binding_id() -> str:
    return f"binding-{uuid4().hex[:10]}"


__all__ = ["ModelBinding", "ModelProfile", "new_model_binding_id", "new_model_profile_id"]
