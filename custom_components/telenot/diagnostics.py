"""Diagnostics: connection and state summary without host, code or detector names."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import TelenotConfigEntry
from .const import CONF_CODE, CONF_DETECTORS

TO_REDACT = {CONF_HOST, CONF_CODE, "name"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TelenotConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data
    client = data.client
    state = client.state
    return {
        "entry": {
            "data": async_redact_data(
                {k: v for k, v in entry.data.items() if k != CONF_DETECTORS}, TO_REDACT
            ),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
            "detectors": async_redact_data(entry.data.get(CONF_DETECTORS, []), TO_REDACT),
        },
        "connection": {
            "available": client.available,
            "connected": client.connected,
            "stats": dict(client.stats),
            "last_command_result": client.last_result.value if client.last_result else None,
        },
        "state": {
            "arm_state": state.arm_state.value,
            "ready_home": state.ready_home,
            "ready_away": state.ready_away,
        },
        "inventory": {
            "detection_areas": sorted(data.inventory.areas),
            "system_points": len(data.inventory.system_points),
        },
    }
