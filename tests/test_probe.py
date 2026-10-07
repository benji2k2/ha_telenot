"""tools/probe.py against the simulated panel."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from custom_components.telenot import protocol as p
from custom_components.telenot.state import ADDR_ARMED_HOME, ADDR_DISARMED, ADDR_FAULT

from .sim_panel import Names, SimPanel

ORIGINAL_DECODER = p.FrameDecoder

_spec = importlib.util.spec_from_file_location(
    "probe", Path(__file__).resolve().parents[1] / "tools" / "probe.py"
)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


@pytest.fixture
async def panel():
    sim = SimPanel(Names(entries={0x0004: ("MK-Window left", 5), 0x0574: ("Window", 0)}))
    await sim.start()
    yield sim
    await sim.stop()


async def _run(panel: SimPanel, *argv: str) -> tuple[int, list[str]]:
    lines: list[str] = []
    args = probe.parse(["127.0.0.1", "--port", str(panel.port), "--fast", "--settle", "0.2", *argv])
    return await probe.run(args, lines.append), lines


async def test_watch_is_read_only_and_reports_changes(panel: SimPanel) -> None:
    async def change() -> None:
        await asyncio.sleep(0.3)
        panel.set(0x0004, True)

    task = asyncio.create_task(change())
    rc, lines = await _run(panel, "--duration", "0.8")
    await task
    assert rc == 0
    assert any("0x0004" in line and "True" in line for line in lines)
    assert all(f == p.CONF_ACK for f in panel.received)


async def test_gap_reports_what_changed(panel: SimPanel) -> None:
    async def change() -> None:
        await asyncio.sleep(0.6)  # inside the gap
        panel.set(ADDR_FAULT, True)

    task = asyncio.create_task(change())
    rc, lines = await _run(panel, "--gap", "1.0")
    await task
    assert rc == 0
    assert any("changed during the gap: 0x0534 fault False → True" in line for line in lines)
    assert panel.connections == 2
    assert all(f == p.CONF_ACK for f in panel.received)


async def test_frame_errors_are_logged_with_raw_bytes(panel: SimPanel) -> None:
    async def garbage() -> None:
        await asyncio.sleep(0.3)
        for writer in panel._writers:  # noqa: SLF001
            writer.write(bytes.fromhex("6805056800"))  # broken header

    task = asyncio.create_task(garbage())
    rc, lines = await _run(panel, "--duration", "0.8")
    await task
    assert rc == 0
    assert any("frame error" in line and "6805056800" in line for line in lines)
    assert p.FrameDecoder is ORIGINAL_DECODER  # restored after the run


async def test_command_needs_confirm(panel: SimPanel) -> None:
    rc, lines = await _run(panel, "--command", "arm_home")
    assert rc == 2
    assert panel.commands == []
    rc, lines = await _run(panel, "--command", "arm_home", "--confirm")
    assert rc == 0
    assert len(panel.commands) == 1
    assert ADDR_ARMED_HOME in panel.active and ADDR_DISARMED not in panel.active
    assert any("event armed_home: 0x0531 armed home" in line and "GMS" in line for line in lines)


async def test_scan_writes_json(panel: SimPanel, tmp_path: Path) -> None:
    out = tmp_path / "scan.json"
    rc, _ = await _run(panel, "--scan", str(out))
    assert rc == 0
    stored = json.loads(out.read_text(encoding="utf-8"))
    assert {d["address"] for d in stored} == {0x0004, 0x0574}


def test_names_accept_both_formats(tmp_path: Path) -> None:
    f = tmp_path / "names.json"
    f.write_text(
        json.dumps([{"adresse": "0x0004", "name": "MK-  Window"}, {"address": 5, "name": "IR"}]),
        encoding="utf-8",
    )
    names = probe.load_names(str(f))
    assert names[0x0004] == "MK- Window"
    assert names[5] == "IR"
    assert names[ADDR_FAULT] == "fault"
