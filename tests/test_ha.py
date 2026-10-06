"""Home Assistant part against the simulated panel."""

from __future__ import annotations

import asyncio

from homeassistant.components.alarm_control_panel import AlarmControlPanelState
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.telenot.const import CONF_CODE, CONF_CODE_FOR, CONF_DETECTORS, DOMAIN
from custom_components.telenot.diagnostics import async_get_config_entry_diagnostics
from custom_components.telenot.state import ADDR_ALARM, ADDR_READY_AWAY

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
    devices = dr.async_get(hass)
    panel_dev = devices.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    area = devices.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_mb5")})
    assert panel_dev and area
    assert area.name == "Fenster Atelier"
    assert area.via_device_id == panel_dev.id

    assert hass.states.get(PANEL).state == AlarmControlPanelState.DISARMED
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
    await hass.services.async_call(
        "alarm_control_panel", "alarm_arm_night", {"entity_id": PANEL}, blocking=True
    )
    await until(lambda: hass.states.get(PANEL).state == AlarmControlPanelState.ARMED_NIGHT)


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


async def test_unavailable_on_disconnect_and_back(hass: HomeAssistant, entry, panel) -> None:  # noqa: ANN001
    panel.silent = True
    await until(lambda: hass.states.get(PANEL).state == STATE_UNAVAILABLE)
    conn = hass.states.get("binary_sensor.telenot_complex_400_connection_to_the_panel")
    assert conn.state == STATE_OFF  # the connection sensor itself stays available
    panel.silent = False
    await until(lambda: hass.states.get(PANEL).state == AlarmControlPanelState.DISARMED, timeout=5)


async def test_night_restored_after_restart(hass: HomeAssistant, panel: SimPanel) -> None:
    from custom_components.telenot.state import ADDR_ARMED_HOME, ADDR_DISARMED

    panel.set(ADDR_DISARMED, False)
    panel.set(ADDR_ARMED_HOME, True)
    mock_restore_cache(hass, [State(PANEL, AlarmControlPanelState.ARMED_NIGHT)])
    e = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "127.0.0.1", CONF_PORT: panel.port, CONF_DETECTORS: []},
    )
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(PANEL).state == AlarmControlPanelState.ARMED_NIGHT
    await hass.config_entries.async_unload(e.entry_id)


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
