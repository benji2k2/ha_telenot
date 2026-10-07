"""State model: arm state and readiness."""

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


def test_changed_addresses():
    s = PanelState()
    first = s.apply_block(outputs(ADDR_DISARMED))
    assert ADDR_DISARMED in first and len(first) == 256  # everything is new
    assert s.apply_block(outputs(ADDR_DISARMED)) == set()


def test_event_log_entries_do_not_change_the_state():
    """The panel's echo of a GMS "disarm": address 0x0530, code 0xE1 (bit 0x80 set)."""
    s = PanelState()
    s.apply_block(outputs(ADDR_DISARMED))
    message = bytes((0x05, p.REC_MESSAGE, 0x00, 0x05, 0x30, 0x01, 0xE1))
    frame = p.Frame(bytes((0x73, 0x02)) + message, b"")
    assert s.apply_frame(frame) == set()
    assert s.arm_state is ArmState.DISARMED
    assert frame.event() == p.PanelEvent(0x0530, 0x01, 0xE1, None, None)


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


def test_keypad_inputs_get_their_role():
    """Every keypad has four inputs named after the keypad; the role follows the position."""
    from custom_components.telenot.inventory import build

    stored = [
        {"address": 0x00B4 + i, "kind": "input", "name": "Keypad 1", "detection_area": 0}
        for i in range(3)
    ] + [
        {
            "address": 0x00B7,
            "kind": "input",
            "name": "Keypad 1 BT Freip. Taste",
            "detection_area": 0,
        },
        {"address": 0x0003, "kind": "input", "name": "IR- Hall", "detection_area": 0},
    ]
    points = {pt.address: pt for pt in build(stored).system_points}
    assert points[0x00B4].name == "Keypad 1 – Deckelkontakt"
    assert points[0x00B4].device_class == "tamper"
    assert points[0x00B6].name == "Keypad 1 – Keine Antwort"
    assert points[0x00B6].device_class == "problem"
    assert points[0x00B7].name == "Keypad 1 – Freie Taste / Bedrohung"
    assert points[0x00B7].device_class is None
    assert points[0x0003].name == "IR- Hall" and points[0x0003].device_class is None
