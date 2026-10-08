"""Telenot complex 400 alarm panel over its GMS serial interface (via RS232-to-TCP)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from . import const
from .client import TelenotClient
from .const import CONF_DETECTORS, DOMAIN, MANUFACTURER, MODEL
from .inventory import Inventory, build

PLATFORMS: list[Platform] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
]


@dataclass(slots=True)
class TelenotData:
    client: TelenotClient
    inventory: Inventory
    panel_device_id: str


type TelenotConfigEntry = ConfigEntry[TelenotData]


async def wait_available(client: TelenotClient, timeout: float) -> bool:
    """Wait until the panel talks and both status telegrams arrived."""
    try:
        async with asyncio.timeout(timeout):
            while not client.available:
                await asyncio.sleep(0.1)
    except TimeoutError:
        return False
    return True


async def wait_talking(client: TelenotClient, timeout: float) -> bool:
    """Wait until the converter is connected and the panel sends frames."""
    try:
        async with asyncio.timeout(timeout):
            while not client.connected:
                await asyncio.sleep(0.1)
    except TimeoutError:
        return False
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TelenotConfigEntry) -> bool:
    client = TelenotClient(entry.data[CONF_HOST], entry.data[CONF_PORT], const.CLIENT_TIMING)
    client.start()
    # Only wait until the panel talks: its full status can take ~13 s after a restart, and
    # the entities simply stay unavailable until then.
    if not await wait_talking(client, const.TALK_WAIT):
        await client.stop()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"host": entry.data[CONF_HOST]},
        )
    panel = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        model=MODEL,
        name=f"Telenot {MODEL}",
    )
    inventory = build(entry.data.get(CONF_DETECTORS, []))
    _remove_stale_devices(hass, entry, inventory)
    entry.runtime_data = TelenotData(client, inventory, panel.id)
    entry.async_on_unload(client.stop)

    async def _async_stop(_event: Event) -> None:
        # Home Assistant does not unload entries when it stops: close the connection here,
        # so the converter's only client slot is free when it starts again.
        await client.stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _remove_stale_devices(
    hass: HomeAssistant, entry: TelenotConfigEntry, inventory: Inventory
) -> None:
    """Detection areas that a new scan no longer reports lose their device (and entities)."""
    keep = {entry.entry_id} | {f"{entry.entry_id}_mb{number}" for number in inventory.areas}
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not any(domain == DOMAIN and ident in keep for domain, ident in device.identifiers):
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TelenotConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
