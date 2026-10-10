"""Home Assistant part against the simulated panel."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PORT,
    EVENT_HOMEASSISTANT_STOP,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.telenot.const import CONF_CODE, CONF_CODE_FOR, CONF_DETECTORS, DOMAIN
from custom_components.telenot.diagnostics import async_get_config_entry_diagnostics
from custom_components.telenot.state import (
    ADDR_ALARM,
    ADDR_ARMED_AWAY,
    ADDR_DISARMED,
    ADDR_FAULT,
    ADDR_READY_AWAY,
    ADDR_READY_HOME,
)

from .sim_panel import Names, SimPanel

PANEL = "alarm_control_panel.telenot_complex_400"

NAMES = Names(
    entries={
        0x0004: ("MK-Atelier links", 5),
        0x0005: ("MK-Atelier rechts", 5),
        0x0006: ("IR- Diele", 2),
        0x0014: ("Akku-Stoerung", 0),
        0x00B0: ("Service-Bedient.", 0),
        0x0571: ("Bewegungsmelder Diele", 0),
        0x0574: ("Fenster         Atelier", 0),
        0x05F4: ("Fenster         Atelier", 0),
        0x0512: ("Buzzer Entry", 0),
        0x0515: ("", 0),
    }
)


async def until(condition, timeout: float = 3.0) -> None:  # noqa: ANN001
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.02)


@pytest.fixture
async def panel():
    sim = SimPanel(NAMES)
    await sim.start()
    yield sim
    await sim.stop()


def stored_detectors() -> list[dict]:
    out = []
    for address, (name, area) in NAMES.entries.items():
        kind = "input" if address < 0x0500 else "output"
        out.append(
            {
                "address": address,
                "kind": kind,
                "name": " ".join(name.split()),
                "detection_area": area,
            }
        )
    return out


@pytest.fixture
async def entry(hass: HomeAssistant, panel: SimPanel) -> MockConfigEntry:
    e = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"127.0.0.1:{panel.port}",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: panel.port, CONF_DETECTORS: stored_detectors()},
        options={CONF_CODE: "1234", CONF_CODE_FOR: ["arm_away"]},
    )
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    yield e
    await hass.config_entries.async_unload(e.entry_id)
    await hass.async_block_till_done()


# ───────────────────────── config flow ─────────────────────────


async def test_flow_with_scan(hass: HomeAssistant, panel: SimPanel) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: panel.port}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "scan"}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    detectors = {d["address"]: d for d in result["data"][CONF_DETECTORS]}
    assert detectors[0x0574]["name"] == "Fenster Atelier"  # padding collapsed
    assert detectors[0x0004]["detection_area"] == 5
    assert result["options"] == {CONF_CODE: "", CONF_CODE_FOR: ["arm_away"]}
    # the flow's own connection was closed; the new entry holds the only one
    await hass.async_block_till_done()
    created = hass.config_entries.async_entries(DOMAIN)[0]
    assert created.state is ConfigEntryState.LOADED
    assert len(panel._writers) == 1
    await hass.config_entries.async_unload(created.entry_id)


async def test_flow_skip_scan(hass: HomeAssistant, panel: SimPanel) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: panel.port}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "skip_scan"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DETECTORS] == []
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(hass.config_entries.async_entries(DOMAIN)[0].entry_id)


async def test_flow_cannot_connect(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: 9}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_flow_already_configured(hass: HomeAssistant, entry: MockConfigEntry, panel) -> None:  # noqa: ANN001
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: panel.port}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ───────────────────────── setup and entities ─────────────────────────


async def test_setup_not_ready_without_panel(hass: HomeAssistant) -> None:
    e = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "127.0.0.1", CONF_PORT: 9, CONF_DETECTORS: []}
    )
    e.add_to_hass(hass)
    await hass.config_entries.async_setup(e.entry_id)
    assert e.state is ConfigEntryState.SETUP_RETRY


async def test_devices_and_entities(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    by_id = {
        ident[1]: device
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        for ident in device.identifiers
    }
    panel_dev = by_id.get(entry.entry_id)
    area = by_id.get(f"{entry.entry_id}_mb5")
    assert panel_dev and area
    assert area.name == "Fenster Atelier"
    assert area.via_device_id == panel_dev.id

    assert hass.states.get(PANEL).state == AlarmControlPanelState.DISARMED
    # only what the panel knows: no virtual night mode
    assert hass.states.get(PANEL).attributes["supported_features"] == (
        AlarmControlPanelEntityFeature.ARM_HOME | AlarmControlPanelEntityFeature.ARM_AWAY
    )
    assert hass.states.get("binary_sensor.telenot_complex_400_ready_for_away").state == STATE_ON
    assert (
        hass.states.get("binary_sensor.telenot_complex_400_connection_to_the_panel").state
        == STATE_ON
    )
    window = hass.states.get("binary_sensor.fenster_atelier")
    assert window.state == STATE_OFF
    assert window.attributes["device_class"] == "window"

    reg = er.async_get(hass)
    # single detectors are disabled by default, system faults enabled
    left = reg.async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_0004")
    assert reg.async_get(left).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    battery = reg.async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_0014")
    assert reg.async_get(battery).disabled_by is None
    keypad = reg.async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_00b0")
    assert reg.async_get(keypad).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert reg.async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_mb5_bypassed")


def _device_identifiers(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    return {
        ident
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        for domain, ident in device.identifiers
        if domain == DOMAIN
    }


async def test_rescan_without_an_area_removes_its_device(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    assert f"{entry.entry_id}_mb5" in _device_identifiers(hass, entry)
    detectors = [d for d in entry.data[CONF_DETECTORS] if d["address"] not in (0x0574, 0x05F4)]
    detectors = [d for d in detectors if d["detection_area"] != 5]
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_DETECTORS: detectors})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    remaining = _device_identifiers(hass, entry)
    assert f"{entry.entry_id}_mb5" not in remaining
    assert entry.entry_id in remaining
    assert hass.states.get("binary_sensor.fenster_atelier") is None


async def test_named_output_buzzer(hass: HomeAssistant, entry, panel) -> None:  # noqa: ANN001
    """A named output (the entry delay buzzer) is shown; unnamed outputs are not."""
    buzzer = "binary_sensor.telenot_complex_400_buzzer_entry"
    state = hass.states.get(buzzer)
    assert state is not None and state.state == STATE_OFF
    assert state.attributes["device_class"] == "sound"
    panel.set(0x0512, True)
    await until(lambda: hass.states.get(buzzer).state == STATE_ON)
    reg = er.async_get(hass)
    assert reg.async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_0515") is None


async def test_arm_away_needs_code_home_does_not(hass: HomeAssistant, entry, panel) -> None:  # noqa: ANN001
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "alarm_control_panel", "alarm_arm_away", {"entity_id": PANEL}, blocking=True
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_away",
            {"entity_id": PANEL, "code": "0000"},
            blocking=True,
        )
    assert panel.commands == []
    await hass.services.async_call(
        "alarm_control_panel", "alarm_arm_away", {"entity_id": PANEL, "code": "1234"}, blocking=True
    )
    await until(lambda: hass.states.get(PANEL).state == AlarmControlPanelState.ARMED_AWAY)
    await hass.services.async_call(
        "alarm_control_panel", "alarm_disarm", {"entity_id": PANEL}, blocking=True
    )
    await until(lambda: hass.states.get(PANEL).state == AlarmControlPanelState.DISARMED)
    # Home Assistant asks for a code for every arm mode once one needs it;
    # arm home does not check it.
    await hass.services.async_call(
        "alarm_control_panel", "alarm_arm_home", {"entity_id": PANEL, "code": "0000"}, blocking=True
    )
    await until(lambda: hass.states.get(PANEL).state == AlarmControlPanelState.ARMED_HOME)


async def test_keypad_when_a_mode_needs_the_code(hass: HomeAssistant, entry, panel) -> None:  # noqa: ANN001
    # Without code_arm_required the frontend arms straight away and never shows its keypad.
    attrs = hass.states.get(PANEL).attributes
    assert attrs["code_arm_required"] is True
    assert attrs["code_format"] == "number"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "alarm_control_panel", "alarm_arm_home", {"entity_id": PANEL}, blocking=True
        )
    assert panel.commands == []


async def test_no_keypad_for_arming_without_code_for_arm(hass: HomeAssistant, panel) -> None:  # noqa: ANN001
    e = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"127.0.0.1:{panel.port}",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: panel.port, CONF_DETECTORS: stored_detectors()},
        options={CONF_CODE: "1234", CONF_CODE_FOR: ["disarm"]},
    )
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(PANEL).attributes["code_arm_required"] is False
    await hass.services.async_call(
        "alarm_control_panel", "alarm_arm_away", {"entity_id": PANEL}, blocking=True
    )
    await until(lambda: hass.states.get(PANEL).state == AlarmControlPanelState.ARMED_AWAY)
    await hass.config_entries.async_unload(e.entry_id)
    await hass.async_block_till_done()


async def test_not_ready(hass: HomeAssistant, entry, panel) -> None:  # noqa: ANN001
    panel.set(ADDR_READY_AWAY, False)
    await until(
        lambda: (
            hass.states.get("binary_sensor.telenot_complex_400_ready_for_away").state == STATE_OFF
        )
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_away",
            {"entity_id": PANEL, "code": "1234"},
            blocking=True,
        )


async def test_rejected_command_raises(hass: HomeAssistant, entry, panel) -> None:  # noqa: ANN001
    panel.reject_next_command = True
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "alarm_control_panel", "alarm_disarm", {"entity_id": PANEL}, blocking=True
        )


async def test_alarm_and_reset_button(hass: HomeAssistant, entry, panel) -> None:  # noqa: ANN001
    panel.set(ADDR_ALARM, True)
    await until(lambda: hass.states.get(PANEL).state == AlarmControlPanelState.TRIGGERED)
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.telenot_complex_400_reset_alarm"}, blocking=True
    )
    await until(lambda: hass.states.get(PANEL).state == AlarmControlPanelState.DISARMED)


async def test_event_log_entity(hass: HomeAssistant, entry, panel) -> None:  # noqa: ANN001
    event_id = "event.telenot_complex_400_event_log"
    await hass.services.async_call(
        "alarm_control_panel", "alarm_arm_home", {"entity_id": PANEL, "code": "1234"}, blocking=True
    )
    await until(lambda: hass.states.get(event_id).attributes.get("event_type") == "armed_home")
    attrs = hass.states.get(event_id).attributes
    assert attrs["source"] == "GMS"
    assert attrs["panel_time"] == "2026-10-07T08:28:44"
    assert attrs["address"] == "0x0531"
    assert attrs["code"] == "0x62"
    await hass.services.async_call(
        "alarm_control_panel", "alarm_disarm", {"entity_id": PANEL}, blocking=True
    )
    await until(lambda: hass.states.get(event_id).attributes.get("event_type") == "disarmed")
    await until(lambda: hass.states.get(PANEL).state == AlarmControlPanelState.DISARMED)


async def test_failed_command_is_an_event(hass: HomeAssistant, entry, panel) -> None:  # noqa: ANN001
    event_id = "event.telenot_complex_400_event_log"
    panel.set(ADDR_READY_AWAY, False)
    await until(
        lambda: (
            hass.states.get("binary_sensor.telenot_complex_400_ready_for_away").state == STATE_OFF
        )
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_away",
            {"entity_id": PANEL, "code": "1234"},
            blocking=True,
        )
    attrs = hass.states.get(event_id).attributes
    assert attrs["event_type"] == "command_failed"
    assert attrs["command"] == "arm_away"
    assert attrs["reason"] == "not_ready"
    assert panel.commands == []


async def test_unavailable_on_disconnect_and_back(hass: HomeAssistant, entry, panel) -> None:  # noqa: ANN001
    panel.silent = True
    await until(lambda: hass.states.get(PANEL).state == STATE_UNAVAILABLE)
    conn = hass.states.get("binary_sensor.telenot_complex_400_connection_to_the_panel")
    assert conn.state == STATE_OFF  # the connection sensor itself stays available
    panel.silent = False
    await until(lambda: hass.states.get(PANEL).state == AlarmControlPanelState.DISARMED, timeout=5)
    await hass.async_block_till_done()
    # Regression (P4): entities on input addresses and the connection sensor came back too,
    # not only those whose bits changed in the telegram that completed the status.
    assert hass.states.get("binary_sensor.telenot_complex_400_akku_stoerung").state == STATE_OFF
    assert conn.entity_id and hass.states.get(conn.entity_id).state == STATE_ON
    assert hass.states.get("binary_sensor.fenster_atelier").state == STATE_OFF


async def _restart(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """HA restart as the integration sees it: unload now, set up again later."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def _start(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_state_changes_while_ha_is_down(hass: HomeAssistant, entry, panel) -> None:  # noqa: ANN001
    """Whatever happens at the panel during a restart is read back from the full status."""
    await _restart(hass, entry)
    panel.set(ADDR_DISARMED, False)
    panel.set(ADDR_ARMED_AWAY, True)  # armed at the keypad
    panel.set(ADDR_FAULT, True)
    panel.set(ADDR_READY_AWAY, False)
    panel.set(0x0014, True)  # battery fault input
    panel.set(0x0574, True)  # detection area 5 open
    await _start(hass, entry)
    assert hass.states.get(PANEL).state == AlarmControlPanelState.ARMED_AWAY
    assert hass.states.get("binary_sensor.telenot_complex_400_fault").state == STATE_ON
    assert hass.states.get("binary_sensor.telenot_complex_400_ready_for_away").state == STATE_OFF
    assert hass.states.get("binary_sensor.telenot_complex_400_akku_stoerung").state == STATE_ON
    assert hass.states.get("binary_sensor.fenster_atelier").state == STATE_ON
    # and back, again while HA is down
    await _restart(hass, entry)
    panel.active = {ADDR_DISARMED, ADDR_READY_HOME, ADDR_READY_AWAY}
    await _start(hass, entry)
    assert hass.states.get(PANEL).state == AlarmControlPanelState.DISARMED
    assert hass.states.get("binary_sensor.telenot_complex_400_fault").state == STATE_OFF
    assert hass.states.get("binary_sensor.telenot_complex_400_akku_stoerung").state == STATE_OFF
    assert hass.states.get("binary_sensor.fenster_atelier").state == STATE_OFF


async def test_options_code(hass: HomeAssistant, entry: MockConfigEntry, panel) -> None:  # noqa: ANN001
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "code"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CODE: "", CONF_CODE_FOR: []}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    await until(
        lambda: hass.states.get(PANEL) and hass.states.get(PANEL).state != STATE_UNAVAILABLE
    )
    await hass.services.async_call(
        "alarm_control_panel", "alarm_arm_away", {"entity_id": PANEL}, blocking=True
    )
    await until(lambda: hass.states.get(PANEL).state == AlarmControlPanelState.ARMED_AWAY)


async def test_options_rescan(hass: HomeAssistant, entry: MockConfigEntry, panel) -> None:  # noqa: ANN001
    panel.names.entries[0x0007] = ("MK- Diele", 3)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "rescan"}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert any(d["address"] == 0x0007 for d in entry.data[CONF_DETECTORS])
    del panel.names.entries[0x0007]


async def test_diagnostics_redacts(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    assert "127.0.0.1" not in text
    assert "1234" not in text
    assert "Atelier" not in text
    assert diag["connection"]["available"] is True
    assert diag["state"]["arm_state"] == "disarmed"


CONNECTION = "binary_sensor.telenot_complex_400_connection_to_the_panel"


def _record_states(hass: HomeAssistant, entity_id: str) -> list[str | None]:
    seen: list[str | None] = []

    @callback
    def _changed(event: Event) -> None:
        if event.data["entity_id"] == entity_id:
            new = event.data["new_state"]
            seen.append(new.state if new else None)

    hass.bus.async_listen("state_changed", _changed)
    return seen


async def test_setup_does_not_wait_for_the_full_status(hass: HomeAssistant, panel) -> None:  # noqa: ANN001
    """After a restart the panel resumes its status only after ~13 s: set up anyway."""
    panel.status_delay = 3.5  # longer than the old wait for the full status (3 s in tests)
    seen = _record_states(hass, CONNECTION)
    e = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "127.0.0.1", CONF_PORT: panel.port, CONF_DETECTORS: stored_detectors()},
    )
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert e.state is ConfigEntryState.LOADED
    # Not there yet: unavailable, not "off" - a heartbeat automation sends nothing then.
    assert hass.states.get(CONNECTION).state == STATE_UNAVAILABLE
    assert hass.states.get(PANEL).state == STATE_UNAVAILABLE
    await until(lambda: hass.states.get(PANEL).state == AlarmControlPanelState.DISARMED, timeout=8)
    await hass.async_block_till_done()
    assert hass.states.get(CONNECTION).state == STATE_ON
    assert STATE_OFF not in seen
    assert await hass.config_entries.async_unload(e.entry_id)


async def test_home_assistant_stop_closes_the_connection(hass: HomeAssistant, entry, panel) -> None:  # noqa: ANN001
    """HA does not unload entries when it stops: the stop event closes the connection."""
    seen = _record_states(hass, CONNECTION)
    assert panel._writers
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    await until(lambda: not panel._writers)  # converter slot free for the next start
    assert entry.runtime_data.client._task is None
    assert seen == []  # no "off" while stopping: no false heartbeat alarm


async def test_reload_writes_no_lost_connection(hass: HomeAssistant, entry, panel) -> None:  # noqa: ANN001
    seen = _record_states(hass, CONNECTION)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await until(lambda: hass.states.get(CONNECTION).state == STATE_ON, timeout=5)
    assert STATE_OFF not in seen


def test_panel_states_named_like_the_panel() -> None:
    """The panel's terms (unscharf, intern/extern scharf) instead of the generic ones."""
    base = Path(__file__).parent.parent / "custom_components" / "telenot"
    strings = json.loads((base / "strings.json").read_text(encoding="utf-8"))
    de = json.loads((base / "translations" / "de.json").read_text(encoding="utf-8"))
    states = strings["entity"]["alarm_control_panel"]["panel"]["state"]
    assert set(states) >= {"disarmed", "armed_home", "armed_away", "triggered"}
    assert de["entity"]["alarm_control_panel"]["panel"]["state"]["disarmed"] == "Unscharf"
    assert set(de["entity"]["alarm_control_panel"]["panel"]["state"]) == set(states)
