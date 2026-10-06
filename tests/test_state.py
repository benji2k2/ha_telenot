"""State model: arm state, readiness and the virtual night mode."""

from custom_components.telenot import protocol as p
from custom_components.telenot.state import (
    ADDR_ALARM,
    ADDR_ARMED_AWAY,
    ADDR_ARMED_HOME,
    ADDR_DISARMED,
    ArmState,
    PanelState,
    bypassed_address,
    detection_area_address,
)

from .test_protocol import REAL_INPUTS, REAL_OUTPUTS


def outputs(*active: int) -> p.BlockStatus:
    """Output block 0x0500…0x05FF with the given addresses active (bit 0)."""
    status = bytearray([0xFF] * 32)
    for address in active:
        off = address - 0x0500
        status[off // 8] &= ~(1 << (off % 8))
    return p.BlockStatus(0, 0x0500, p.EXT_OUTPUTS, bytes(status))


def test_real_capture_is_disarmed_and_ready():
    s = PanelState()
    for frame in p.FrameDecoder().feed(REAL_INPUTS + REAL_OUTPUTS):
        s.apply_frame(frame)
    assert s.complete
    assert s.arm_state is ArmState.DISARMED
    assert s.ready_home is True
    assert s.ready_away is True


def test_unknown_until_outputs_seen():
    s = PanelState()
    assert s.arm_state is ArmState.UNKNOWN
    s.apply_block(p.BlockStatus(0, 0x0000, p.EXT_INPUTS, b"\xff"))
    assert s.arm_state is ArmState.UNKNOWN
    assert not s.complete


def test_alarm_wins_over_armed():
    s = PanelState()
    s.apply_block(outputs(ADDR_ARMED_AWAY, ADDR_ALARM))
    assert s.arm_state is ArmState.TRIGGERED


def test_away_and_home():
    s = PanelState()
    s.apply_block(outputs(ADDR_ARMED_AWAY))
    assert s.arm_state is ArmState.ARMED_AWAY
    s.apply_block(outputs(ADDR_ARMED_HOME))
    assert s.arm_state is ArmState.ARMED_HOME


def test_night_flag_shows_night_until_disarmed():
    s = PanelState()
    s.night_flag = True
    s.apply_block(outputs(ADDR_ARMED_HOME))
    assert s.arm_state is ArmState.ARMED_NIGHT
    s.apply_block(outputs(ADDR_DISARMED))
    assert s.arm_state is ArmState.DISARMED
    assert s.night_flag is False
    s.apply_block(outputs(ADDR_ARMED_HOME))
    assert s.arm_state is ArmState.ARMED_HOME


def test_changed_addresses_and_spontaneous_message():
    s = PanelState()
    first = s.apply_block(outputs(ADDR_DISARMED))
    assert ADDR_DISARMED in first and len(first) == 256  # everything is new
    assert s.apply_block(outputs(ADDR_DISARMED)) == set()
    changed = s.apply_message(p.Message(0, ADDR_ALARM, p.EXT_OUTPUTS, 0x22))
    assert changed == {ADDR_ALARM}
    assert s.arm_state is ArmState.TRIGGERED


def test_occupancy_answers_are_not_states():
    s = PanelState()
    assert s.apply_block(p.BlockStatus(0, 0x0000, p.EXT_OCCUPIED_INPUTS, b"\x00")) == set()
    assert s.is_active(0x0000) is None


def test_clear_forgets_everything():
    s = PanelState()
    s.apply_block(outputs(ADDR_DISARMED))
    s.clear()
    assert s.is_active(ADDR_DISARMED) is None
    assert s.arm_state is ArmState.UNKNOWN


def test_detection_area_addresses():
    assert detection_area_address(1) == 0x0570
    assert detection_area_address(12) == 0x057B
    assert bypassed_address(5) == 0x05F4
