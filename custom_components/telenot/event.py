"""Event entity: the panel's event log (armed, disarmed, alarms, faults) as Home Assistant events.

The panel sends an event log entry next to its status telegrams – after every GMS command,
and per VdS 2465 also for alarms, faults and restarts. The entry's message type ("Meldungsart")
tells the kind in its lower 7 bits; bit 0x80 marks the withdrawal ("Rücknahme"). Kinds as in
carhensi/telenot-esp-bridge (``MeldungsArt``). The state of every entity still comes from the
status telegrams only; these events add who did what and when, as the panel logged it.
"""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TelenotConfigEntry
from .client import CommandResult
from .entity import TelenotEntity, panel_device
from .protocol import PanelEvent

# Lower 7 bits of the message type → event type (0x61/0x62 are handled separately).
_KINDS = {
    0x10: "fire",
    0x21: "hold_up",
    0x22: "intrusion",
    0x23: "tamper",
    0x30: "fault",
    0x32: "mains_fault",
    0x33: "battery_fault",
    0x34: "transmission_fault",
    0x40: "technical",
    0x41: "technical_alarm",
    0x51: "shutdown",
    0x52: "reset",
    0x53: "restart",
}
# "command_failed" is not from the panel's log: a command from Home Assistant that the panel
# did not carry out (attributes command, reason) – reported at once, e.g. "not ready".
EVENT_TYPES = ["armed_away", "armed_home", "disarmed", *_KINDS.values(), "other", "command_failed"]


def event_type(event: PanelEvent) -> str:
    """Event type of a log entry. Verified on 2026-10-07: GMS "arm home" → 0x62 at 0x0531,
    "disarm" → 0xE1 at 0x0530 (0x61 withdrawn = area no longer armed)."""
    kind, withdrawn = event.art & 0x7F, bool(event.art & 0x80)
    if kind == 0x61:
        return "disarmed" if withdrawn else "armed_away"
    if kind == 0x62:
        return "disarmed" if withdrawn else "armed_home"
    return _KINDS.get(kind, "other")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TelenotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([TelenotEventLog(entry)])


class TelenotEventLog(TelenotEntity, EventEntity):
    _attr_translation_key = "event_log"
    _attr_event_types = EVENT_TYPES

    def __init__(self, entry: TelenotConfigEntry) -> None:
        super().__init__(entry, "event_log", panel_device(entry))
        self._names = entry.runtime_data.inventory.names

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._client.add_event_listener(self._event))
        self.async_on_remove(self._client.add_failure_listener(self._failure))

    @callback
    def _failure(self, command: str, result: CommandResult) -> None:
        self._trigger_event(
            "command_failed",
            {"command": command, "reason": result.value, "source": "Home Assistant"},
        )
        self.async_write_ha_state()

    @callback
    def _event(self, event: PanelEvent) -> None:
        self._trigger_event(
            event_type(event),
            {
                "active": not event.art & 0x80,
                "address": f"0x{event.address:04X}",
                "name": self._names.get(event.address),
                "code": f"0x{event.art:02X}",
                "source": event.source,
                "panel_time": event.time.isoformat() if event.time else None,
            },
        )
        self.async_write_ha_state()
