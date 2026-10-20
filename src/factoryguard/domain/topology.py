from __future__ import annotations

"""The physical hierarchy used by the detection runtime.

The registry deliberately lives in the domain layer.  UI and persistence
adapters can present or store the tree, while inference only needs a resolved
``stream_id`` and its immutable context.
"""

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .models import Area, CameraDevice, Site, VideoStream


@dataclass(frozen=True, slots=True)
class EntityPath:
    """Resolved site -> nested areas -> camera -> stream path."""

    site: Site
    areas: tuple[Area, ...]
    camera: CameraDevice
    stream: VideoStream

    @property
    def area(self) -> Area:
        """The nearest area containing the camera."""
        return self.areas[-1]

    @property
    def ids(self) -> dict[str, str]:
        return {
            "site_id": self.site.id,
            "area_id": self.area.id,
            "camera_id": self.camera.id,
            "stream_id": self.stream.id,
        }

    @property
    def display_name(self) -> str:
        return " / ".join((self.site.name, *(area.name for area in self.areas), self.camera.name, self.stream.name))

    def as_dict(self) -> dict[str, Any]:
        return {
            "site": asdict(self.site),
            "areas": [asdict(area) for area in self.areas],
            "camera": asdict(self.camera),
            "stream": asdict(self.stream),
            **self.ids,
            "display_name": self.display_name,
        }


class EntityRegistry:
    """In-memory source of truth for the nested physical topology.

    IDs are globally unique within each entity kind.  Parent references are
    checked at insertion time so a stream can never silently point to a
    camera in another site or leave a dangling area relationship.
    """

    def __init__(
        self,
        *,
        sites: Iterable[Site] = (),
        areas: Iterable[Area] = (),
        cameras: Iterable[CameraDevice] = (),
        streams: Iterable[VideoStream] = (),
    ) -> None:
        self._sites: dict[str, Site] = {}
        self._areas: dict[str, Area] = {}
        self._cameras: dict[str, CameraDevice] = {}
        self._streams: dict[str, VideoStream] = {}
        for site in sites:
            self.add_site(site)
        pending_areas = list(areas)
        while pending_areas:
            progress = False
            for area in tuple(pending_areas):
                if area.parent_id is not None and area.parent_id not in self._areas:
                    continue
                self.add_area(area)
                pending_areas.remove(area)
                progress = True
            if not progress:
                unresolved = ", ".join(area.id for area in pending_areas)
                raise ValueError(f"area hierarchy contains an unknown parent or cycle: {unresolved}")
        for camera in cameras:
            self.add_camera(camera)
        for stream in streams:
            self.add_stream(stream)

    @property
    def sites(self) -> tuple[Site, ...]:
        return tuple(self._sites.values())

    @property
    def areas(self) -> tuple[Area, ...]:
        return tuple(self._areas.values())

    @property
    def cameras(self) -> tuple[CameraDevice, ...]:
        return tuple(self._cameras.values())

    @property
    def streams(self) -> tuple[VideoStream, ...]:
        return tuple(self._streams.values())

    def add_site(self, site: Site) -> Site:
        self._insert(self._sites, site, "site")
        return site

    def add_area(self, area: Area) -> Area:
        if area.site_id not in self._sites:
            raise ValueError(f"area {area.id!r} references unknown site {area.site_id!r}")
        if area.parent_id is not None:
            parent = self._areas.get(area.parent_id)
            if parent is None:
                raise ValueError(f"area {area.id!r} references unknown parent area {area.parent_id!r}")
            if parent.site_id != area.site_id:
                raise ValueError("nested areas must belong to the same site")
            if area.parent_id == area.id:
                raise ValueError("area cannot be its own parent")
        self._insert(self._areas, area, "area")
        return area

    def add_camera(self, camera: CameraDevice) -> CameraDevice:
        if camera.area_id not in self._areas:
            raise ValueError(f"camera {camera.id!r} references unknown area {camera.area_id!r}")
        self._insert(self._cameras, camera, "camera")
        return camera

    def add_stream(self, stream: VideoStream) -> VideoStream:
        if stream.camera_id not in self._cameras:
            raise ValueError(f"stream {stream.id!r} references unknown camera {stream.camera_id!r}")
        self._insert(self._streams, stream, "stream")
        return stream

    def get_site(self, site_id: str) -> Site:
        return self._get(self._sites, site_id, "site")

    def get_area(self, area_id: str) -> Area:
        return self._get(self._areas, area_id, "area")

    def get_camera(self, camera_id: str) -> CameraDevice:
        return self._get(self._cameras, camera_id, "camera")

    def get_stream(self, stream_id: str) -> VideoStream:
        return self._get(self._streams, stream_id, "stream")

    def stream_path(self, stream_id: str) -> EntityPath:
        stream = self.get_stream(stream_id)
        camera = self.get_camera(stream.camera_id)
        area = self.get_area(camera.area_id)
        site = self.get_site(area.site_id)
        chain: list[Area] = []
        current: Area | None = area
        seen: set[str] = set()
        while current is not None:
            if current.id in seen:
                raise ValueError("area hierarchy contains a cycle")
            seen.add(current.id)
            chain.append(current)
            current = self._areas.get(current.parent_id) if current.parent_id else None
        chain.reverse()
        return EntityPath(site=site, areas=tuple(chain), camera=camera, stream=stream)

    def streams_for_area(self, area_id: str, *, include_descendants: bool = True) -> tuple[VideoStream, ...]:
        self.get_area(area_id)
        area_ids = {area_id}
        if include_descendants:
            changed = True
            while changed:
                changed = False
                for area in self.areas:
                    if area.parent_id in area_ids and area.id not in area_ids:
                        area_ids.add(area.id)
                        changed = True
        camera_ids = {camera.id for camera in self.cameras if camera.area_id in area_ids}
        return tuple(stream for stream in self.streams if stream.camera_id in camera_ids)

    def nested(self) -> tuple[dict[str, Any], ...]:
        """Return a JSON-friendly site/area/camera/stream tree for UI APIs."""
        result: list[dict[str, Any]] = []
        for site in self.sites:
            site_data: dict[str, Any] = {**asdict(site), "areas": []}
            for area in self.areas:
                if area.site_id != site.id or area.parent_id is not None:
                    continue
                site_data["areas"].append(self._area_tree(area))
            result.append(site_data)
        return tuple(result)

    def _area_tree(self, area: Area) -> dict[str, Any]:
        data: dict[str, Any] = {**asdict(area), "children": [], "cameras": []}
        data["cameras"] = [
            {**asdict(camera), "streams": [asdict(stream) for stream in self.streams if stream.camera_id == camera.id]}
            for camera in self.cameras
            if camera.area_id == area.id
        ]
        data["children"] = [self._area_tree(child) for child in self.areas if child.parent_id == area.id]
        return data

    def to_dict(self) -> dict[str, Any]:
        return {
            "sites": [asdict(item) for item in self.sites],
            "areas": [asdict(item) for item in self.areas],
            "cameras": [asdict(item) for item in self.cameras],
            "streams": [asdict(item) for item in self.streams],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EntityRegistry:
        """Build a validated registry from a JSON/YAML-shaped mapping."""
        if not isinstance(payload, dict):
            raise TypeError("topology payload must be a mapping")
        return cls(
            sites=(Site(**item) for item in payload.get("sites", ())),
            areas=(Area(**item) for item in payload.get("areas", ())),
            cameras=(CameraDevice(**item) for item in payload.get("cameras", ())),
            streams=(VideoStream(**item) for item in payload.get("streams", ())),
        )

    @staticmethod
    def _insert(bucket: dict[str, Any], value: Any, kind: str) -> None:
        if value.id in bucket:
            raise ValueError(f"duplicate {kind} id: {value.id}")
        bucket[value.id] = value

    @staticmethod
    def _get(bucket: dict[str, Any], identifier: str, kind: str) -> Any:
        try:
            return bucket[identifier]
        except KeyError as exc:
            raise KeyError(f"unknown {kind} id: {identifier}") from exc


# ``Topology`` is the concise public name used by configuration code.
Topology = EntityRegistry


__all__ = ["EntityPath", "EntityRegistry", "Topology"]
