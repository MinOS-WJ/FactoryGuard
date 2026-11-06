from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from factoryguard.devices.gb28181.server import Gb28181Adapter, Gb28181Platform
from factoryguard.devices.onvif import OnvifAdapter
from factoryguard.domain.models import AnalysisPolicy, DeviceProtocol, VideoDevice
from factoryguard.media.ffmpeg import find_ffmpeg
from factoryguard.media.rtsp import RTSPCapture
from tools.camera_sim.cli import main as camera_sim_main
from tools.camera_sim.service import CameraSimulator


def _free_port() -> int:
    """Reserve an unused local port so parallel tests never collide."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _child_creation_flags() -> int:
    """Give the child its own process group so it can be asked to stop."""
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def _stop_process_tree(process: subprocess.Popen[str]) -> None:
    """Stop a child simulator together with the FFmpeg helpers it started.

    ``CTRL_BREAK_EVENT`` lets the simulator run its own shutdown and close
    the encoders.  A hard kill is only the fallback because it would leave
    the helper processes behind.
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            pass
    else:
        process.terminate()
        try:
            process.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            pass
    process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
            timeout=15.0,
        )


def _pid_is_alive(pid: int) -> bool:
    """Return whether a process is still running (Windows, no tasklist)."""
    if os.name != "nt":
        return False
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259  # STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def test_simulator_releases_its_ffmpeg_helpers():
    """Stopping the simulator must not leave encoders behind.

    A long verification session or a long-running console would otherwise
    accumulate stray FFmpeg processes.
    """
    simulator = CameraSimulator(
        count=1,
        protocols=("onvif",),
        rtsp_port=0,
        onvif_port=0,
    )
    simulator.start()
    pids: list[int] = []
    try:
        assert simulator.wait_until_ready(timeout=20.0)
        server = simulator._rtsp_server
        assert server is not None
        pids = [
            relay._publisher.pid for relay in server.relays.values() if relay._publisher is not None
        ]
        assert pids, "the simulator never started an FFmpeg publisher"
        assert all(_pid_is_alive(pid) for pid in pids)
    finally:
        simulator.stop()

    deadline = time.monotonic() + 10.0
    while any(_pid_is_alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not any(_pid_is_alive(pid) for pid in pids), "the simulator left FFmpeg helpers behind"


def test_camera_simulator_cli_help_runs_from_checkout():
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "-m", "tools.camera_sim", "--help"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--count" in completed.stdout
    assert "--protocols" in completed.stdout


pytestmark = pytest.mark.skipif(
    find_ffmpeg() is None, reason="FFmpeg is required for camera simulator tests"
)


@pytest.mark.parametrize(
    ("arguments", "message"),
    (
        (["--count", "0"], "count must be positive"),
        (["--duration", "-1"], "duration must be a non-negative"),
        (["--wait", "nan"], "wait must be a non-negative"),
        (["--rtsp-port", "-1"], "ports must be non-negative"),
    ),
)
def test_camera_simulator_cli_rejects_invalid_arguments(arguments, message, capsys):
    with pytest.raises(SystemExit) as raised:
        camera_sim_main(["--protocols", "onvif", *arguments])
    assert raised.value.code == 2
    assert message in capsys.readouterr().err


def test_camera_simulator_assigns_protocols_and_stable_ids():
    simulator = CameraSimulator(count=4, protocols=("onvif", "gb28181"))
    snapshot = simulator.snapshot()
    assert [camera["protocol"] for camera in snapshot] == [
        "onvif",
        "gb28181",
        "onvif",
        "gb28181",
    ]
    assert len({camera["camera_id"] for camera in snapshot}) == 4
    assert all(
        len(str(camera["device_id"])) == 20
        for camera in snapshot
        if camera["protocol"] == "gb28181"
    )


def test_onvif_camera_simulator_probe_channels_and_live_frame():
    simulator = CameraSimulator(
        count=1,
        protocols=("onvif",),
        rtsp_port=0,
        onvif_port=0,
    )
    adapter: OnvifAdapter | None = None
    try:
        simulator.start()
        camera = simulator.snapshot()[0]
        device = VideoDevice(
            "onvif-sim-device",
            "site",
            "area",
            "ONVIF Simulator",
            DeviceProtocol.ONVIF,
            str(camera["onvif_endpoint"]),
            analysis_policy=AnalysisPolicy.NATIVE_ONLY,
        )
        adapter = OnvifAdapter(device)
        result = adapter.probe(timeout=5.0)
        assert result.ok
        assert result.capabilities.live_video
        channels = adapter.list_channels()
        assert channels and channels[0].id == "profile-main"
        stream = adapter.get_live_stream(channels[0].id)
        frame = RTSPCapture(
            simulator.ffmpeg,
            stream.uri,
            width=320,
            height=180,
            fps=2.0,
        ).capture_one(timeout=12.0)
        assert frame.image.shape == (180, 320, 3)
    finally:
        if adapter is not None:
            adapter.close()
        simulator.stop()


def test_gb28181_camera_simulator_register_catalog_alarm_and_live_frame():
    platform = Gb28181Platform(
        platform_id="340200000000000000",
        domain="3402000000",
        password="secret",
        host="127.0.0.1",
        port=0,
    )
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    actual_port: dict[str, int] = {}

    def run_platform() -> None:
        asyncio.set_event_loop(loop)

        async def boot() -> None:
            _host, port = await platform.start(host="127.0.0.1", port=0)
            actual_port["value"] = port
            ready.set()

        loop.run_until_complete(boot())
        loop.run_forever()

    thread = threading.Thread(target=run_platform, daemon=True)
    thread.start()
    assert ready.wait(15.0)
    simulator = CameraSimulator(
        count=1,
        protocols=("gb28181",),
        rtsp_port=0,
        onvif_port=0,
        platform_port=actual_port["value"],
        password="secret",
    )
    adapter: Gb28181Adapter | None = None
    try:
        simulator.start()
        assert simulator.wait_until_ready(timeout=20.0)
        registration = next(iter(platform.registrations.values()))
        assert registration.channels
        assert simulator.send_alarm() == 1
        assert platform.native_events.get(timeout=10.0).event_code
        device = VideoDevice(
            "gb-sim-device",
            "site",
            "area",
            "GB Simulator",
            DeviceProtocol.GB28181,
            f"gb28181://{registration.device_id}",
            device_id=registration.device_id,
            analysis_policy=AnalysisPolicy.NATIVE_ONLY,
        )
        adapter = Gb28181Adapter(device, platform=platform)
        channels = adapter.list_channels()
        assert channels
        stream = adapter.get_live_stream(channels[0].id)
        frame = RTSPCapture(
            simulator.ffmpeg,
            stream.uri,
            width=320,
            height=180,
            fps=2.0,
        ).capture_one(timeout=12.0)
        assert frame.image.shape == (180, 320, 3)
    finally:
        if adapter is not None:
            adapter.close()
        simulator.stop()
        future = asyncio.run_coroutine_threadsafe(platform.close(), loop)
        try:
            future.result(timeout=3.0)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=3.0)
            loop.close()


def test_camera_simulator_cli_process_is_reachable_by_the_platform():
    """The documented ``python -m tools.camera_sim`` flow must be usable."""
    import subprocess
    import sys
    import threading
    import time

    onvif_port = _free_port()
    rtsp_port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tools.camera_sim",
            "--count",
            "1",
            "--protocols",
            "onvif",
            "--onvif-port",
            str(onvif_port),
            "--rtsp-port",
            str(rtsp_port),
            "--duration",
            "45",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_child_creation_flags(),
    )

    # Drain the output in the background so the child is never blocked.
    def _drain() -> None:
        assert process.stdout is not None
        process.stdout.read()

    threading.Thread(target=_drain, daemon=True).start()
    adapter: OnvifAdapter | None = None
    try:
        time.sleep(2.5)
        endpoint = f"http://127.0.0.1:{onvif_port}/onvif/sim-cam-01/device"
        device = VideoDevice(
            "cli-sim-device",
            "site",
            "area",
            "ONVIF Simulator",
            DeviceProtocol.ONVIF,
            endpoint,
            analysis_policy=AnalysisPolicy.NATIVE_ONLY,
        )
        adapter = OnvifAdapter(device)
        result = adapter.probe(timeout=8.0)
        assert result.ok
        channels = adapter.list_channels()
        assert channels
        assert adapter.get_live_stream(channels[0].id).uri.startswith(
            f"rtsp://127.0.0.1:{rtsp_port}/"
        )
    finally:
        if adapter is not None:
            adapter.close()
        # Ask the simulator to stop so it can release its FFmpeg helpers,
        # then make sure nothing survives the process group.
        _stop_process_tree(process)
        if process.stdout is not None:
            process.stdout.close()


def test_camera_simulator_cli_registers_and_alarms_over_gb28181():
    """The documented GB/T 28181 flow must reach a real platform over SIP."""
    import subprocess
    import sys
    import threading
    import time

    platform = Gb28181Platform(
        platform_id="340200000000000000",
        domain="3402000000",
        password="secret",
        host="127.0.0.1",
        port=0,
    )
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    actual_port: dict[str, int] = {}

    def run_platform() -> None:
        asyncio.set_event_loop(loop)

        async def boot() -> None:
            _host, port = await platform.start(host="127.0.0.1", port=0)
            actual_port["value"] = port
            ready.set()

        loop.run_until_complete(boot())
        loop.run_forever()

    thread = threading.Thread(target=run_platform, daemon=True)
    thread.start()
    assert ready.wait(15.0)

    onvif_port = _free_port()
    rtsp_port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tools.camera_sim",
            "--count",
            "1",
            "--protocols",
            "gb28181",
            "--rtsp-port",
            str(rtsp_port),
            "--onvif-port",
            str(onvif_port),
            "--sip-host",
            "127.0.0.1",
            "--sip-port",
            str(actual_port["value"]),
            "--password",
            "secret",
            "--wait",
            "15",
            "--send-alarm",
            "--duration",
            "45",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_child_creation_flags(),
    )

    def _drain() -> None:
        assert process.stdout is not None
        process.stdout.read()

    threading.Thread(target=_drain, daemon=True).start()
    try:
        deadline = time.monotonic() + 40.0
        while time.monotonic() < deadline and not platform.registrations:
            time.sleep(0.1)
        assert platform.registrations, "the simulated device did not register"

        registration = next(iter(platform.registrations.values()))
        assert registration.channels, "the platform did not receive the catalogue"

        event = platform.native_events.get(timeout=10.0)
        assert str(event.source) == "gb28181"
        assert event.event_code
    finally:
        # Ask the simulator to stop so it can release its FFmpeg helpers,
        # then make sure nothing survives the process group.
        _stop_process_tree(process)
        if process.stdout is not None:
            process.stdout.close()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=15.0)
        loop.run_until_complete(platform.close())
        loop.close()


def test_onvif_camera_simulator_delivers_an_alarm_to_the_platform():
    """The simulator must be able to raise a real ONVIF alarm."""
    simulator = CameraSimulator(
        count=1,
        protocols=("onvif",),
        rtsp_port=0,
        onvif_port=0,
    )
    adapter: OnvifAdapter | None = None
    try:
        simulator.start()
        camera = simulator.snapshot()[0]
        device = VideoDevice(
            "onvif-alarm-device",
            "site",
            "area",
            "ONVIF Simulator",
            DeviceProtocol.ONVIF,
            str(camera["onvif_endpoint"]),
            analysis_policy=AnalysisPolicy.NATIVE_ONLY,
        )
        adapter = OnvifAdapter(device)
        result = adapter.probe(timeout=5.0)
        assert result.ok
        assert "motionalarm" in result.capabilities.event_types

        subscription = adapter.subscribe_events("ch1")
        assert simulator.send_alarm() == 1

        event = next(subscription.iterator)
        assert str(event.source) == "onvif"
        assert event.event_code
        assert event.channel_id
    finally:
        if adapter is not None:
            adapter.close()
        simulator.stop()
