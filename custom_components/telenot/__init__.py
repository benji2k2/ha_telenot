"""Telenot complex 400 alarm panel over its GMS serial interface (via RS232-to-TCP)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
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


async def async_setup_entry(hass: HomeAssistant, entry: TelenotConfigEntry) -> bool:
    client = TelenotClient(entry.data[CONF_HOST], entry.data[CONF_PORT], const.CLIENT_TIMING)
    client.start()
    if not await wait_available(client, const.CONNECT_WAIT):
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
    entry.runtime_data = TelenotData(client, build(entry.data.get(CONF_DETECTORS, [])), panel.id)
    entry.async_on_unload(client.stop)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TelenotConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
