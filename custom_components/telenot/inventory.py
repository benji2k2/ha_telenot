"""Turns a scan result into detection areas and detectors. No Home Assistant imports.

Address ranges of the complex 400 (see state.py): inputs below 0x0500; area status
0x0530…0x0537; detection area n at 0x0570 + n - 1; "detection area n bypassed" at
0x05F0 + n - 1. Inputs carry their detection area number from the scan (0 = system).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from .client import Detector
from .state import ADDR_AREA_BYPASSED, ADDR_DETECTION_AREA

MAX_DETECTION_AREAS = 128

# Keypads ("Bedienteile") occupy four inputs each from 0x00B0 on (keypad n at 0x00B0 + 4 n).
# The panel names all four after the keypad; their role follows from the position.
ADDR_KEYPADS = 0x00B0
MAX_KEYPADS = 16
KEYPAD_ROLES: list[tuple[str, str | None]] = [
    ("Deckelkontakt", "tamper"),
    ("Deckelkontakt Anzeigeteil", "tamper"),
    ("Keine Antwort", "problem"),
    ("Freie Taste / Bedrohung", None),
]


@dataclass(frozen=True, slots=True)
class Point:
    """One detector (input) as stored in the config entry."""

    address: int
    name: str
    detection_area: int
    device_class: str | None = None  # known from the address; else guessed from the name


@dataclass(slots=True)
class DetectionArea:
    number: int
    name: str
    points: list[Point] = field(default_factory=list)
    bypassed_known: bool = False

    @property
    def state_address(self) -> int:
        return ADDR_DETECTION_AREA + self.number - 1

    @property
    def bypassed_address(self) -> int:
        return ADDR_AREA_BYPASSED + self.number - 1


@dataclass(slots=True)
class Inventory:
    areas: dict[int, DetectionArea] = field(default_factory=dict)
    system_points: list[Point] = field(default_factory=list)  # detection area 0 or unknown
    names: dict[int, str] = field(default_factory=dict)  # every named address, for events


def is_keypad_input(address: int) -> bool:
    return 0 <= address - ADDR_KEYPADS < 4 * MAX_KEYPADS


def clean_name(name: str | None) -> str:
    """Collapse the padding the panel uses to align texts on its keypad."""
    return re.sub(r"\s+", " ", name or "").strip()


def to_storage(detectors: list[Detector]) -> list[dict]:
    """Scan result → JSON-serialisable list for the config entry."""
    return [
        {
            "address": d.address,
            "kind": d.kind,
            "name": clean_name(d.name),
            "detection_area": d.detection_area,
        }
        for d in detectors
        if d.status == "named"
    ]


def build(stored: list[dict]) -> Inventory:
    """Config entry data → detection areas with their detectors."""
    inventory = Inventory()
    inventory.names = {item["address"]: item["name"] for item in stored if item["name"]}
    for item in stored:
        address = item["address"]
        offset = address - ADDR_DETECTION_AREA
        if item["kind"] == "output" and 0 <= offset < MAX_DETECTION_AREAS:
            number = offset + 1
            inventory.areas[number] = DetectionArea(number, item["name"] or f"MB {number}")
    for item in stored:
        address = item["address"]
        offset = address - ADDR_AREA_BYPASSED
        if (
            item["kind"] == "output"
            and 0 <= offset < MAX_DETECTION_AREAS
            and (area := inventory.areas.get(offset + 1)) is not None
        ):
            area.bypassed_known = True
        if item["kind"] != "input":
            continue
        name, device_class = item["name"] or f"0x{address:04X}", None
        if is_keypad_input(address):
            role, device_class = KEYPAD_ROLES[(address - ADDR_KEYPADS) % 4]
            keypad = re.sub(r"\s*BT Freip\. Taste$", "", name)
            name = f"{keypad} – {role}"
        point = Point(address, name, item["detection_area"] or 0, device_class)
        area = inventory.areas.get(point.detection_area)
        (area.points if area else inventory.system_points).append(point)
    return inventory


# Name fragments of German panel texts → device class of a binary sensor.
_KINDS: list[tuple[str, str]] = [
    (r"sabotage|deckel|\bdk\b|dk-|gehäuse|gehaeuse", "tamper"),
    (r"akku|batterie", "battery"),
    (r"netz|störung|stoerung", "problem"),
    (r"rauch|brand", "smoke"),
    (r"wasser", "moisture"),
    (r"bewegung|\bir\b|ir-|\bim\b|im-|pir|bm-", "motion"),
    (r"fenster|\bmk\b|mk-|magnet", "window"),
    (r"tür|tuer|haustür", "door"),
    (r"sirene|signalgeber|\bsg\b|akustisch|optisch", "sound"),
]


def guess_device_class(name: str) -> str | None:
    """Device class from the plain-text name the installer gave the detector."""
    lowered = name.lower()
    for pattern, device_class in _KINDS:
        if re.search(pattern, lowered):
            return device_class
    return None
