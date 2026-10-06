"""Connection layer against the simulated panel over real TCP."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from custom_components.telenot import protocol as p
from custom_components.telenot.client import CommandResult, TelenotClient, Timing
from custom_components.telenot.state import (
    ADDR_ALARM,
    ADDR_ARMED_HOME,
    ADDR_READY_AWAY,
    ArmState,
)

from .sim_panel import Names, SimPanel

FAST = Timing(
    connect_timeout=1.0,
    backoff_min=0.05,
    backoff_max=0.2,
    liveness=0.5,
    command_timeout=0.2,
    command_attempts=3,
    query_timeout=0.2,
    query_attempts=3,
)


async def until(condition: Callable[[], bool], timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


@pytest.fixture
async def panel():
    sim = SimPanel(
        Names(
            entries={
                0x0004: ("MK-Window left", 5),
                0x0005: ("MK-Window right", 5),
                0x0014: ("Battery fault", 0),
                0x0574: ("Window study", 5),
            },
            not_occupied={0x00A8},
        )
    )
    await sim.start()
    yield sim
    await sim.stop()


@pytest.fixture
async def client(panel):
    c = TelenotClient("127.0.0.1", panel.port, FAST)
    c.start()
    await until(lambda: c.available)
    yield c
    await c.stop()


async def test_connects_and_reads_state(client, panel):
    assert client.state.arm_state is ArmState.DISARMED
    assert client.state.ready_home and client.state.ready_away
    assert panel.connections == 1


async def test_every_poll_and_status_telegram_is_acknowledged(client, panel):
    await asyncio.sleep(0.3)
    acks = sum(1 for f in panel.received if f == p.CONF_ACK)
    # every SEND_NORM gets an answer (ACK when idle) and every status telegram an ACK
    assert acks >= panel.status_sent
    assert client.stats["frame_errors"] == 0


async def test_arm_away_is_confirmed_and_state_follows(client, panel):
    assert await client.arm_away() is CommandResult.OK
    assert panel.commands == [p.encode_command(0x0532, p.EXT_OUTPUTS, p.ART_ARM_AWAY)]
    await until(lambda: client.state.arm_state is ArmState.ARMED_AWAY)


async def test_reset_frame_is_well_formed(client, panel):
    assert await client.reset() is CommandResult.OK
    assert panel.commands[-1] == bytes.fromhex("680909687301050200053302520716")


async def test_rejected_command(client, panel):
    panel.reject_next_command = True
    assert await client.disarm() is CommandResult.REJECTED
    assert len(panel.commands) == 1  # no retry after a rejection


async def test_nak_is_retried(client, panel):
    panel.nak_next_command = True
    assert await client.disarm() is CommandResult.OK
    assert len(panel.commands) == 2


async def test_unanswered_command_is_retried_then_times_out(client, panel):
    frame = p.encode_command(0x0530, p.EXT_OUTPUTS, p.ART_DISARM)
    panel.swallow = {frame}
    assert await client.disarm() is CommandResult.OK  # second attempt answered
    panel.swallow = set()

    async def swallow_all():
        while True:
            panel.swallow.add(frame)
            await asyncio.sleep(0.005)

    task = asyncio.create_task(swallow_all())
    try:
        assert await client.disarm() is CommandResult.TIMEOUT
    finally:
        task.cancel()


async def test_not_ready_is_refused_locally(client, panel):
    panel.set(ADDR_READY_AWAY, False)
    await until(lambda: client.state.ready_away is False)
    before = len(panel.commands)
    assert await client.arm_away() is CommandResult.NOT_READY
    assert len(panel.commands) == before


async def test_states_keep_flowing_after_reconnect(client, panel):
    """Regression: telenot-bridge stopped processing data after every reconnect."""
    changes: list[set[int]] = []
    client.add_listener(changes.append)
    await panel.drop()
    await until(lambda: not client.available)
    await until(lambda: client.available)
    assert panel.connections == 2
    changes.clear()
    panel.set(ADDR_ALARM, True)
    await until(lambda: client.state.arm_state is ArmState.TRIGGERED)
    assert any(ADDR_ALARM in c for c in changes)
    assert await client.reset() is CommandResult.OK
    await until(lambda: client.state.arm_state is ArmState.DISARMED)


async def test_silent_panel_triggers_reconnect(client, panel):
    panel.silent = True
    await until(lambda: not client.available, timeout=2)
    panel.silent = False
    await until(lambda: client.available, timeout=3)
    assert panel.connections >= 2


async def test_waiting_command_fails_on_disconnect_and_is_not_resent(client, panel):
    panel.silent = True  # no send window any more
    await asyncio.sleep(0.1)  # let polls already on the wire be answered
    task = asyncio.create_task(client.arm_home())
    await asyncio.sleep(0.05)
    await panel.drop()
    assert await task is CommandResult.NOT_CONNECTED
    panel.silent = False
    await until(lambda: client.available)
    await asyncio.sleep(0.2)
    assert panel.commands == []


async def test_reconnects_when_converter_comes_back(panel):
    port = panel.port
    await panel.stop()
    c = TelenotClient("127.0.0.1", port, FAST)
    c.start()
    try:
        await asyncio.sleep(0.3)
        assert not c.available
        await panel.start(port)
        await until(lambda: c.available)
    finally:
        await c.stop()


async def test_command_without_connection():
    c = TelenotClient("127.0.0.1", 9, FAST)
    assert await c.disarm() is CommandResult.NOT_CONNECTED


async def test_scan(client, panel):
    progress: list[float] = []
    panel.swallow = {p.encode_text_query(0x0005)}  # first query for 0x0005 gets lost
    detectors = {d.address: d for d in await client.scan(progress.append)}
    assert set(detectors) == {0x0004, 0x0005, 0x0014, 0x00A8, 0x0574}
    assert detectors[0x0004].name == "MK-Window left"
    assert detectors[0x0004].detection_area == 5
    assert detectors[0x0005].status == "named"  # answered on the retry
    assert detectors[0x00A8].status == "not_occupied"
    assert detectors[0x0574].kind == "output"
    assert progress[-1] == 1.0
    # only read-only frames besides acknowledgements
    assert all(f[7] == p.REC_QUERY for f in panel.received if f != p.CONF_ACK)


async def test_listener_reports_availability_changes(client, panel):
    events: list[set[int]] = []
    remove = client.add_listener(events.append)
    await panel.drop()
    await until(lambda: set() in events)
    remove()
    assert client.state.arm_state is ArmState.UNKNOWN or client.available


async def test_stop_is_clean(panel):
    c = TelenotClient("127.0.0.1", panel.port, FAST)
    c.start()
    await until(lambda: c.available)
    await c.stop()
    assert not c.connected
    await asyncio.sleep(0.1)
    assert panel.connections == 1
    assert ADDR_ARMED_HOME not in panel.active
