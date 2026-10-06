"""State model of the complex 400 built from block status and message records. No I/O.

Address map (complex 400, security area 1, verified against the panel on 2026-10-06):

- 0x0000…0x04FF inputs (detection points), block status with extension 0x01
- 0x0500…       outputs and system status, block status with extension 0x02
  - 0x0530 disarmed, 0x0531 armed home, 0x0532 armed away, 0x0533 alarm, 0x0534 fault,
    0x0535 ready for home, 0x0536 ready for away
  - 0x0570 + (n-1) detection area n
  - 0x05F0 + (n-1) detection area n bypassed
"""

from __future__ import annotations

from enum import Enum

from .protocol import EXT_INPUTS, EXT_OUTPUTS, BlockStatus, Frame, Message

ADDR_DISARMED = 0x0530
ADDR_ARMED_HOME = 0x0531
ADDR_ARMED_AWAY = 0x0532
ADDR_ALARM = 0x0533
ADDR_FAULT = 0x0534
ADDR_READY_HOME = 0x0535
ADDR_READY_AWAY = 0x0536
ADDR_DETECTION_AREA = 0x0570
ADDR_AREA_BYPASSED = 0x05F0


def detection_area_address(number: int) -> int:
    """Status address of detection area ``number`` (1-based)."""
    return ADDR_DETECTION_AREA + number - 1


def bypassed_address(number: int) -> int:
    """Address of "detection area ``number`` bypassed" (1-based)."""
    return ADDR_AREA_BYPASSED + number - 1


class ArmState(Enum):
    """Arm state of security area 1 as the panel reports it."""

    DISARMED = "disarmed"
    ARMED_HOME = "armed_home"
    ARMED_AWAY = "armed_away"
    TRIGGERED = "triggered"
    UNKNOWN = "unknown"


class PanelState:
    """Latest known bit of every address, fed from received frames."""

    def __init__(self) -> None:
        self._bits: dict[int, bool] = {}
        self.inputs_seen = False
        self.outputs_seen = False

    @property
    def complete(self) -> bool:
        """Both status telegrams have been received at least once."""
        return self.inputs_seen and self.outputs_seen

    def clear(self) -> None:
        """Forget everything, e.g. after the connection was lost."""
        self._bits.clear()
        self.inputs_seen = self.outputs_seen = False

    def is_active(self, address: int) -> bool | None:
        return self._bits.get(address)

    def snapshot(self) -> dict[int, bool]:
        """Copy of every known bit."""
        return dict(self._bits)

    def apply_frame(self, frame: Frame) -> set[int]:
        """Apply all status records of a frame; return the addresses whose bit changed."""
        changed: set[int] = set()
        for record in frame.records():
            if (block := record.as_block_status()) is not None:
                changed |= self.apply_block(block)
            elif (message := record.as_message()) is not None:
                changed |= self.apply_message(message)
        return changed

    def apply_block(self, block: BlockStatus) -> set[int]:
        if block.extension not in (EXT_INPUTS, EXT_OUTPUTS):
            return set()  # occupancy answers (0x71/0x72) are not states
        if block.extension == EXT_INPUTS:
            self.inputs_seen = True
        else:
            self.outputs_seen = True
        return {a for a in block.addresses() if self._set(a, bool(block.is_active(a)))}

    def apply_message(self, message: Message) -> set[int]:
        """A spontaneous message updates the single address it names."""
        if message.extension not in (EXT_INPUTS, EXT_OUTPUTS):
            return set()
        return {message.address} if self._set(message.address, message.active) else set()

    def _set(self, address: int, active: bool) -> bool:
        """Store a bit; True if it is new or changed."""
        old = self._bits.get(address)
        self._bits[address] = active
        return old != active

    @property
    def arm_state(self) -> ArmState:
        """Derived like the ESP bridge: alarm wins, then away, home, disarmed."""
        if not self.outputs_seen:
            return ArmState.UNKNOWN
        if self.is_active(ADDR_ALARM):
            return ArmState.TRIGGERED
        if self.is_active(ADDR_ARMED_AWAY):
            return ArmState.ARMED_AWAY
        if self.is_active(ADDR_ARMED_HOME):
            return ArmState.ARMED_HOME
        if self.is_active(ADDR_DISARMED):
            return ArmState.DISARMED
        return ArmState.UNKNOWN

    @property
    def ready_home(self) -> bool | None:
        return self.is_active(ADDR_READY_HOME)

    @property
    def ready_away(self) -> bool | None:
        return self.is_active(ADDR_READY_AWAY)
