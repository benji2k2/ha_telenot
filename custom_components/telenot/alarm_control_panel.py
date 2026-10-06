"""Alarm control panel for security area 1."""

from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TelenotConfigEntry
from .client import CommandResult
from .const import CONF_CODE, CONF_CODE_FOR, DEFAULT_CODE_FOR, DOMAIN
from .entity import TelenotEntity, panel_device
from .state import (
    ADDR_ALARM,
    ADDR_ARMED_AWAY,
    ADDR_ARMED_HOME,
    ADDR_DISARMED,
    ADDR_READY_AWAY,
    ADDR_READY_HOME,
    ArmState,
)

_STATES = {
    ArmState.DISARMED: AlarmControlPanelState.DISARMED,
    ArmState.ARMED_HOME: AlarmControlPanelState.ARMED_HOME,
    ArmState.ARMED_AWAY: AlarmControlPanelState.ARMED_AWAY,
    ArmState.TRIGGERED: AlarmControlPanelState.TRIGGERED,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TelenotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([TelenotAlarmPanel(entry)])


class TelenotAlarmPanel(TelenotEntity, AlarmControlPanelEntity):
    """Arm state always comes from the panel, never from the command that was sent."""

    _attr_translation_key = "panel"
    _attr_name = None  # the device name ("Telenot complex 400")
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME | AlarmControlPanelEntityFeature.ARM_AWAY
    )
    # The code is checked here per mode; HA would otherwise demand it for every arm mode.
    _attr_code_arm_required = False
    addresses = frozenset(
        {
            ADDR_DISARMED,
            ADDR_ARMED_HOME,
            ADDR_ARMED_AWAY,
            ADDR_ALARM,
            ADDR_READY_HOME,
            ADDR_READY_AWAY,
        }
    )

    def __init__(self, entry: TelenotConfigEntry) -> None:
        super().__init__(entry, "panel", panel_device(entry))
        self._code: str | None = entry.options.get(CONF_CODE) or None
        self._code_for: list[str] = entry.options.get(CONF_CODE_FOR, DEFAULT_CODE_FOR)
        self._attr_code_format = CodeFormat.NUMBER if self._code else None

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        return _STATES.get(self._client.state.arm_state)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        state = self._client.state
        return {"ready_home": state.ready_home, "ready_away": state.ready_away}

    def _check_code(self, mode: str, code: str | None) -> None:
        if self._code and mode in self._code_for and code != self._code:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_code")

    async def _run(self, result: CommandResult) -> None:
        if result is CommandResult.OK:
            return
        if result is CommandResult.NOT_READY:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_ready")
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="command_failed",
            translation_placeholders={"result": result.value},
        )

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        self._check_code("disarm", code)
        await self._run(await self._client.disarm())

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        self._check_code("arm_home", code)
        await self._run(await self._client.arm_home())

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        self._check_code("arm_away", code)
        await self._run(await self._client.arm_away())
