from dataclasses import replace
from math import isfinite

from factoryguard.system_metrics import (
    ResourceSampler,
    classify_resource_state,
    format_bytes,
)


def test_resource_sampler_returns_local_system_metrics(tmp_path):
    sampler = ResourceSampler()
    first = sampler.sample(tmp_path)
    second = sampler.sample(tmp_path)
    assert isfinite(first.cpu_percent)
    assert isfinite(second.cpu_percent)
    assert first.memory_total_bytes >= first.memory_available_bytes >= 0
    assert first.disk_total_bytes >= first.disk_free_bytes >= 0
    assert first.process_memory_bytes > 0
    assert first.logical_cpu_count >= 1
    assert first.thread_count >= 1
    assert first.process_cpu_percent >= 0.0


def test_resource_sampler_accepts_not_yet_created_paths(tmp_path):
    snapshot = ResourceSampler().sample(tmp_path / "missing" / "nested")
    assert snapshot.disk_total_bytes >= snapshot.disk_free_bytes >= 0


def test_resource_state_uses_shared_cpu_memory_and_disk_thresholds(tmp_path):
    snapshot = replace(
        ResourceSampler().sample(tmp_path),
        cpu_percent=0.0,
        memory_used_percent=0.0,
        disk_total_bytes=100,
        disk_free_bytes=50,
    )

    assert classify_resource_state(snapshot) == "ok"
    assert classify_resource_state(replace(snapshot, cpu_percent=75)) == "warn"
    # Whole-machine load is outside the operator control and never an error.
    assert classify_resource_state(replace(snapshot, cpu_percent=100)) == "warn"
    assert classify_resource_state(replace(snapshot, memory_used_percent=85)) == "warn"
    assert classify_resource_state(replace(snapshot, memory_used_percent=95)) == "error"
    disk_warn = replace(
        snapshot,
        cpu_percent=0,
        memory_used_percent=0,
        disk_total_bytes=100,
        disk_free_bytes=14,
    )
    disk_error = replace(disk_warn, disk_free_bytes=4)
    assert classify_resource_state(disk_warn) == "warn"
    assert classify_resource_state(disk_error) == "error"


def test_format_bytes_uses_compact_binary_units():
    assert format_bytes(0) == "0.0 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(5 * 1024**3).endswith("GB")
