"""Protocol core against reference telegrams.

ESP_* telegrams come from carhensi/telenot-esp-bridge (crates/telenot-protocol/tests/
reference_telegrams.rs, captured on a real complex 400). REAL_* telegrams were recorded on a
real complex 400 (status only; the panel's ident record 0x56 is zeroed).
"""

from custom_components.telenot import protocol as p


def h(text: str) -> bytes:
    return bytes.fromhex(text.replace(" ", ""))


SEND_NORM = h("68 02 02 68 40 02 42 16")
REAL_INPUTS = h(
    "682e2e687302222400000001fffffffffffffffffffffffffffffffffffffffffffffbffffffffffffff"
    "0656000000fffffff316"
)
REAL_OUTPUTS = h(
    "683e3e687302322400050002feffffffffff9e9e9e9e9e9e9e9effffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffff0656000000fffffff416"
)
ESP_TEXT_MG08 = h(
    "68 24 24 68 73 02 06 0C 01 00 07 73 FE 08 10 54 4D 41 2D 4D 47 30 38 20 20 20 20 20 20 20"
    " 20 20 06 56 00 80 51 FF FF FF 6D 16"
)


def test_command_encoders_match_reference():
    assert p.encode_command(0x0532, p.EXT_OUTPUTS, p.ART_ARM_AWAY) == h(
        "68 09 09 68 73 01 05 02 00 05 32 02 61 15 16"
    )
    assert p.encode_command(0x0531, p.EXT_OUTPUTS, p.ART_ARM_HOME) == h(
        "680909687301050200053102621516"
    )
    assert p.encode_command(0x0530, p.EXT_OUTPUTS, p.ART_DISARM) == h(
        "680909687301050200053002E19316"
    )


def test_reset_checksum_below_0x10_stays_one_byte():
    # The old Node bridge wrote this checksum as a single hex digit ("7").
    frame = p.encode_command(0x0533, p.EXT_OUTPUTS, p.ART_RESET)
    assert frame == h("68 09 09 68 73 01 05 02 00 05 33 02 52 07 16")
    assert len(frame) == 15


def test_query_encoders_match_reference():
    assert p.encode_occupied_query() == h("68 09 09 68 73 02 05 10 00 00 00 71 24 1F 16")
    assert p.encode_text_query(0x0007) == h("68 09 09 68 73 02 05 10 00 00 07 73 0C 10 16")
    assert p.encode_text_query(0x0530) == h("68 09 09 68 73 02 05 10 00 05 30 73 0C 3E 16")


def test_function_of_control_field():
    d = p.FrameDecoder()
    send_norm, data, ack = d.feed(SEND_NORM + REAL_INPUTS + p.CONF_ACK)
    assert send_norm.function is p.Function.SEND_NORM
    assert data.function is p.Function.SEND_NDAT
    assert ack.function is p.Function.CONFIRM_ACK


def test_decoder_reassembles_fragments_and_skips_garbage():
    stream = b"\x00\x16\x68" + REAL_OUTPUTS + SEND_NORM + ESP_TEXT_MG08
    d = p.FrameDecoder()
    frames = []
    for i in range(0, len(stream), 5):
        frames += d.feed(stream[i : i + 5])
    assert [f.raw for f in frames] == [REAL_OUTPUTS, SEND_NORM, ESP_TEXT_MG08]
    assert d.errors >= 1  # the stray 0x68 before the real frame


def test_decoder_rejects_bad_checksum():
    bad = bytearray(SEND_NORM)
    bad[-2] ^= 0xFF
    d = p.FrameDecoder()
    assert d.feed(bytes(bad)) == []
    assert d.feed(SEND_NORM) == [p.Frame(h("40 02"), SEND_NORM)]


def test_block_status_of_real_outputs():
    (frame,) = p.FrameDecoder().feed(REAL_OUTPUTS)
    records = list(frame.records())
    block = records[0].as_block_status()
    assert block is not None
    assert (block.base, block.extension) == (0x0500, p.EXT_OUTPUTS)
    # disarmed, ready home and ready away – as Home Assistant showed at capture time
    assert block.is_active(0x0530) is True
    assert block.is_active(0x0531) is False
    assert block.is_active(0x0532) is False
    assert block.is_active(0x0533) is False
    assert block.is_active(0x0535) is True
    assert block.is_active(0x0536) is True
    assert block.is_active(0x04FF) is None
    assert records[1].type == p.REC_IDENT


def test_block_status_of_real_inputs():
    (frame,) = p.FrameDecoder().feed(REAL_INPUTS)
    block = next(frame.records()).as_block_status()
    assert (block.base, block.extension) == (0x0000, p.EXT_INPUTS)
    assert block.active_addresses() == [0x00B2]


def test_text_answer():
    (frame,) = p.FrameDecoder().feed(ESP_TEXT_MG08)
    area, text, ident = frame.records()
    info = area.as_area_info()
    assert (info.address, info.detection_area, info.extension) == (0x0007, 8, p.EXT_TEXT)
    assert text.as_text() == "MA-MG08"
    assert ident.type == p.REC_IDENT


def test_error_record():
    frame = p.Frame(h("00 02 02 11 00 19"), b"")
    (record,) = frame.records()
    assert record.as_error() == p.PanelError(0x00, 0x19)
    assert record.as_error().not_occupied


def test_message_record():
    frame = p.Frame(h("73 02 05 02 00 05 33 02 40"), b"")
    message = next(frame.records()).as_message()
    assert message == p.Message(0x00, 0x0533, p.EXT_OUTPUTS, 0x40)
    assert message.active
    assert not p.Message(0, 0x0533, p.EXT_OUTPUTS, 0xC0).active


def test_hd44780_umlauts():
    assert p.decode_text(bytes((0x4B, 0xF5, 0x63, 0x68, 0x65, 0x20))) == "Küche"
    assert p.decode_text(bytes((0x53, 0x63, 0x68, 0xEF, 0x6E, 0x01))) == "Schön?"


def test_truncated_record_stops_iteration():
    frame = p.Frame(h("73 02 05 02 00 05"), b"")
    assert list(frame.records()) == []
