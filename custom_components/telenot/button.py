"""Button to reset the alarm (security area 1)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TelenotConfigEntry
from .client import CommandResult
from .const import DOMAIN
from .entity import TelenotEntity, panel_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TelenotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([TelenotResetButton(entry)])


class TelenotResetButton(TelenotEntity, ButtonEntity):
    _attr_translation_key = "reset"

    def __init__(self, entry: TelenotConfigEntry) -> None:
        super().__init__(entry, "reset", panel_device(entry))

    async def async_press(self) -> None:
        result = await self._client.reset()
        if result is not CommandResult.OK:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"result": result.value},
            )
