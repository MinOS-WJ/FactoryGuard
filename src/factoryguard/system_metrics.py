"""Local Windows resource metrics without an external monitoring agent."""

from __future__ import annotations

import ctypes
import os
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    @property
    def value(self) -> int:
        return int(self.low) | (int(self.high) << 32)


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint32),
        ("memory_load", ctypes.c_uint32),
        ("total_phys", ctypes.c_uint64),
        ("avail_phys", ctypes.c_uint64),
        ("total_page_file", ctypes.c_uint64),
        ("avail_page_file", ctypes.c_uint64),
        ("total_virtual", ctypes.c_uint64),
        ("avail_virtual", ctypes.c_uint64),
        ("avail_extended_virtual", ctypes.c_uint64),
    ]


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("page_fault_count", ctypes.c_uint32),
        ("peak_working_set_size", ctypes.c_size_t),
        ("working_set_size", ctypes.c_size_t),
        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
        ("quota_paged_pool_usage", ctypes.c_size_t),
        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
        ("quota_non_paged_pool_usage", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
    ]


ResourceState = Literal["ok", "warn", "error"]


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    cpu_percent: float
    memory_total_bytes: int
    memory_available_bytes: int
    memory_used_percent: float
    process_memory_bytes: int
    process_cpu_percent: float
    disk_total_bytes: int
    disk_free_bytes: int
    logical_cpu_count: int
    thread_count: int
    captured_at: float


def _existing_disk_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    while not path.exists() and path.parent != path:
        path = path.parent
    return path if path.exists() else Path.cwd()


class ResourceSampler:
    """Sample host and current-process resources using lightweight Windows APIs."""

    def __init__(self) -> None:
        self._system_cpu: tuple[int, int, int] | None = None
        self._process_cpu: tuple[int, int, float] | None = None
        self._sample_count = 0
        self._cpu_window: deque[float] | None = None
        # Prime counters so the first UI/CLI sample reports a real interval.
        self._cpu_percent()
        self._process_cpu_percent(time.perf_counter())

    def sample(self, disk_root: str | Path) -> ResourceSnapshot:
        if self._sample_count == 0:
            time.sleep(0.05)
        self._sample_count += 1
        now = time.perf_counter()
        total_memory, available_memory = self._memory()
        disk = shutil.disk_usage(_existing_disk_path(disk_root))
        cpu_percent = self._smoothed_cpu_percent()
        return ResourceSnapshot(
            cpu_percent=cpu_percent,
            memory_total_bytes=total_memory,
            memory_available_bytes=available_memory,
            memory_used_percent=(
                (total_memory - available_memory) / total_memory * 100.0 if total_memory else 0.0
            ),
            process_memory_bytes=self._process_memory_bytes(),
            process_cpu_percent=self._process_cpu_percent(now),
            disk_total_bytes=int(disk.total),
            disk_free_bytes=int(disk.free),
            logical_cpu_count=int(os.cpu_count() or 0),
            thread_count=threading.active_count(),
            captured_at=now,
        )

    def _smoothed_cpu_percent(self) -> float:
        """Measure CPU across a short window so a burst is not a fault."""
        current = self._cpu_percent()
        if self._cpu_window is None:
            self._cpu_window = deque(maxlen=4)
        self._cpu_window.append(current)
        if len(self._cpu_window) < 2:
            return current
        return sum(self._cpu_window) / len(self._cpu_window)

    def _cpu_percent(self) -> float:
        if os.name != "nt":
            return 0.0
        if self._system_cpu is None:
            # Prime the counters and measure over a short window so a burst of
            # work is not reported as a sustained fault.
            time.sleep(0.05)
            self._system_cpu = None
        try:
            idle = _FileTime()
            kernel = _FileTime()
            user = _FileTime()
            if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            ):
                return 0.0
            current = (idle.value, kernel.value, user.value)
            previous = self._system_cpu
            self._system_cpu = current
            if previous is None:
                return 0.0
            total_delta = (current[1] + current[2]) - (previous[1] + previous[2])
            idle_delta = current[0] - previous[0]
            if total_delta <= 0:
                return 0.0
            return max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))
        except (AttributeError, OSError, ValueError):
            return 0.0

    @staticmethod
    def _memory() -> tuple[int, int]:
        if os.name != "nt":
            return 0, 0
        try:
            status = _MemoryStatusEx()
            status.length = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return 0, 0
            return int(status.total_phys), int(status.avail_phys)
        except (AttributeError, OSError, ValueError):
            return 0, 0

    @staticmethod
    def _process_memory_bytes() -> int:
        if os.name != "nt":
            return 0
        try:
            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            get_memory_info = ctypes.windll.kernel32.K32GetProcessMemoryInfo
            get_memory_info.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(_ProcessMemoryCounters),
                ctypes.c_uint32,
            ]
            get_memory_info.restype = ctypes.c_int
            ok = get_memory_info(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(counters),
                counters.cb,
            )
            return int(counters.working_set_size) if ok else 0
        except (AttributeError, OSError, ValueError):
            return 0

    def _process_cpu_percent(self, now: float) -> float:
        if os.name != "nt":
            return 0.0
        try:
            creation = _FileTime()
            exit_time = _FileTime()
            kernel = _FileTime()
            user = _FileTime()
            get_process_times = ctypes.windll.kernel32.GetProcessTimes
            get_process_times.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(_FileTime),
                ctypes.POINTER(_FileTime),
                ctypes.POINTER(_FileTime),
                ctypes.POINTER(_FileTime),
            ]
            get_process_times.restype = ctypes.c_int
            if not get_process_times(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return 0.0
            current = (kernel.value + user.value, now)
            previous = self._process_cpu
            self._process_cpu = current
            if previous is None:
                return 0.0
            wall_delta = max(0.0, now - previous[1])
            cpu_delta = max(0, current[0] - previous[0]) / 10_000_000.0
            return max(0.0, cpu_delta / wall_delta * 100.0) if wall_delta > 0 else 0.0
        except (AttributeError, OSError, ValueError):
            return 0.0


def classify_resource_state(snapshot: ResourceSnapshot) -> ResourceState:
    """Grade the machine state without blaming the operator for outside load.

    Disk space and memory exhaustion are problems the operator can fix, so
    they may raise an error.  Whole-machine CPU load is often caused by
    other programs, therefore it only ever produces a warning.  A busy
    machine must not make the health check fail and send the merchant
    looking for a fault that does not exist.
    """
    free_ratio = snapshot.disk_free_bytes / max(snapshot.disk_total_bytes, 1)
    if snapshot.memory_used_percent >= 95 or free_ratio < 0.05:
        return "error"
    if snapshot.cpu_percent >= 75 or snapshot.memory_used_percent >= 85 or free_ratio < 0.15:
        return "warn"
    return "ok"


def format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(max(0, value))
    for unit in units:
        if amount < 1024.0 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024.0
    return f"{amount:.1f} TB"


__all__ = [
    "ResourceSampler",
    "ResourceSnapshot",
    "ResourceState",
    "classify_resource_state",
    "format_bytes",
]
