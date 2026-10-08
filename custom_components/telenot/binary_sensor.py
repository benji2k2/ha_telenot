"""Binary sensors: panel status, detection areas and their detectors."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TelenotConfigEntry
from .entity import TelenotEntity, area_device, panel_device
from .inventory import Point, guess_device_class, is_keypad_input
from .state import ADDR_ALARM, ADDR_FAULT, ADDR_READY_AWAY, ADDR_READY_HOME

# Panel status bits: translation key, address, device class.
_PANEL = [
    ("ready_home", ADDR_READY_HOME, None),
    ("ready_away", ADDR_READY_AWAY, None),
    ("alarm", ADDR_ALARM, BinarySensorDeviceClass.SAFETY),
    ("fault", ADDR_FAULT, BinarySensorDeviceClass.PROBLEM),
]

# System detectors that are worth showing by default.
_ENABLED_SYSTEM_CLASSES = {"battery", "problem", "tamper"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TelenotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    inventory = entry.runtime_data.inventory
    panel = panel_device(entry)
    entities: list[BinarySensorEntity] = [
        TelenotBitSensor(entry, key, address, panel, device_class, translation_key=key)
        for key, address, device_class in _PANEL
    ]
    entities.append(TelenotConnectionSensor(entry))
    for point in inventory.system_points:
        entities.append(TelenotPointSensor(entry, point, panel, system=True))
    entities.extend(
        TelenotPointSensor(entry, point, panel, output=True) for point in inventory.outputs
    )
    for area in inventory.areas.values():
        device = area_device(entry, area)
        area_class = guess_device_class(area.name)
        entities.append(
            TelenotBitSensor(entry, f"mb{area.number}", area.state_address, device, area_class)
        )
        if area.bypassed_known:
            entities.append(
                TelenotBitSensor(
                    entry,
                    f"mb{area.number}_bypassed",
                    area.bypassed_address,
                    device,
                    None,
                    translation_key="bypassed",
                    category=EntityCategory.DIAGNOSTIC,
                    enabled=False,
                )
            )
        entities.extend(TelenotPointSensor(entry, point, device) for point in area.points)
    async_add_entities(entities)


class TelenotBitSensor(TelenotEntity, BinarySensorEntity):
    """On while the panel reports the address as active."""

    def __init__(
        self,
        entry: TelenotConfigEntry,
        key: str,
        address: int,
        device: DeviceInfo,
        device_class: str | None,
        *,
        translation_key: str | None = None,
        category: EntityCategory | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(entry, key, device)
        self._address = address
        self.addresses = frozenset({address})
        self._attr_device_class = BinarySensorDeviceClass(device_class) if device_class else None
        self._attr_entity_category = category
        self._attr_entity_registry_enabled_default = enabled
        if translation_key:
            self._attr_translation_key = translation_key
        else:
            self._attr_name = None  # the device name, e.g. "Fenster Küche"
        self._attr_extra_state_attributes = {"address": f"0x{address:04X}"}

    @property
    def is_on(self) -> bool | None:
        return self._client.state.is_active(self._address)


class TelenotPointSensor(TelenotBitSensor):
    """A single detector (disabled by default unless a system fault or tamper) or a named
    output such as the entry delay buzzer (shown)."""

    def __init__(
        self,
        entry: TelenotConfigEntry,
        point: Point,
        device: DeviceInfo,
        *,
        system: bool = False,
        output: bool = False,
    ) -> None:
        device_class = point.device_class or guess_device_class(point.name)
        super().__init__(
            entry,
            f"{point.address:04x}",
            point.address,
            device,
            device_class,
            # Keypad inputs stay off: an absent keypad may report "no answer" for good.
            # Named outputs (e.g. the entry delay buzzer) were labelled on purpose: shown.
            enabled=output
            or (
                system
                and device_class in _ENABLED_SYSTEM_CLASSES
                and not is_keypad_input(point.address)
            ),
        )
        self._attr_name = point.name
        self._attr_extra_state_attributes = {
            "address": f"0x{point.address:04X}",
            "detection_area": point.detection_area,
        }


class TelenotConnectionSensor(TelenotEntity, BinarySensorEntity):
    """Whether the panel talks to Home Assistant.

    Unavailable only after a start until the panel sent its full status once (that can take
    ~13 s); from then on "on" or "off". So a restart reads as "not there yet", not as a lost
    connection.
    """

    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: TelenotConfigEntry) -> None:
        super().__init__(entry, "connection", panel_device(entry))

    @property
    def available(self) -> bool:
        return self._client.was_available

    @property
    def is_on(self) -> bool:
        return self._client.available

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        stats = self._client.stats
        last = self._client.last_result
        return {
            "connects": stats["connects"],
            "frame_errors": stats["frame_errors"],
            "last_command_result": last.value if last else None,
        }
