"""Base entity: follows the client's listener, unavailable whenever the panel is."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from . import TelenotConfigEntry
from .const import DOMAIN
from .inventory import DetectionArea


def panel_device(entry: TelenotConfigEntry) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})


def area_device(entry: TelenotConfigEntry, area: DetectionArea) -> DeviceInfo:
    """Child device of the panel for one detection area, named as in the panel."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_mb{area.number}")},
        name=area.name,
        model=f"Meldebereich {area.number}",
        via_device_id=entry.runtime_data.panel_device_id,
    )


class TelenotEntity(Entity):
    """Updates on changes of ``addresses`` and on availability changes."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    addresses: frozenset[int] = frozenset()

    def __init__(self, entry: TelenotConfigEntry, key: str, device: DeviceInfo) -> None:
        self._entry = entry
        self._client = entry.runtime_data.client
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device

    @property
    def available(self) -> bool:
        return self._client.available

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._client.add_listener(self._changed))

    def _changed(self, changed: set[int]) -> None:
        if not changed or changed & self.addresses:
            self.async_write_ha_state()
