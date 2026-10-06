"""GMS protocol of the Telenot complex 400 – pure functions, no I/O.

Framing is IEC 60870-5 FT1.2 (``68 L L 68 <user data> <sum> 16``), the user data carries
VdS 2465 records. Byte layouts follow carhensi/telenot-esp-bridge (Apache-2.0), where they are
verified against captures of a real panel; see NOTICE.

Bit convention of block status records (0x24): a ``0`` bit means *active* (occupied, open,
armed, …), a ``1`` bit means inactive.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum

START = 0x68
END = 0x16

# Control field (C) of frames we send; address field (A) of commands and queries.
C_SEND_NDAT = 0x73
A_COMMAND = 0x01
A_QUERY = 0x02

# Record types (VdS 2465 "Satztyp").
REC_MESSAGE = 0x02
REC_AREA = 0x0C
REC_QUERY = 0x10
REC_ERROR = 0x11
REC_BLOCK_STATUS = 0x24
REC_DATETIME = 0x50
REC_TEXT = 0x54
REC_IDENT = 0x56

# Address extensions.
EXT_INPUTS = 0x01
EXT_OUTPUTS = 0x02
EXT_OCCUPIED_INPUTS = 0x71
EXT_OCCUPIED_OUTPUTS = 0x72
EXT_TEXT = 0x73

# Query types of record 0x10.
QUERY_OCCUPIED = 0x24
QUERY_TEXT = 0x0C

# Message types ("Meldungsart") of commands.
ART_RESET = 0x52
ART_ARM_AWAY = 0x61
ART_ARM_HOME = 0x62
ART_DISARM = 0xE1

# Error codes in record 0x11 meaning "nothing there".
ERRORS_NOT_OCCUPIED = (0x18, 0x19)

CONF_ACK = bytes((0x68, 0x02, 0x02, 0x68, 0x00, 0x02, 0x02, 0x16))


class Function(Enum):
    """Meaning of the control field of a received frame."""

    SEND_NORM = "send_norm"  # poll from the panel – our only send window
    SEND_NDAT = "send_ndat"  # data from the panel – must be acknowledged
    CONFIRM_ACK = "confirm_ack"  # panel acknowledges our frame
    CONFIRM_NAK = "confirm_nak"  # panel rejects our frame
    OTHER = "other"


def checksum(user_data: bytes) -> int:
    """Sum of the user data modulo 256."""
    return sum(user_data) & 0xFF


def encode_frame(user_data: bytes) -> bytes:
    """Wrap user data in an FT1.2 frame with variable length."""
    if len(user_data) > 255:
        raise ValueError("user data too long")
    n = len(user_data)
    return bytes((START, n, n, START)) + user_data + bytes((checksum(user_data), END))


def encode_command(address: int, extension: int, art: int) -> bytes:
    """SEND_NDAT with one 0x02 message record (arm, disarm, reset)."""
    return encode_frame(
        bytes(
            (C_SEND_NDAT, A_COMMAND, 0x05, REC_MESSAGE, 0x00)
            + (address >> 8, address & 0xFF, extension, art)
        )
    )


def encode_query(address: int, extension: int, query_type: int) -> bytes:
    """SEND_NDAT with one 0x10 query record (read only)."""
    return encode_frame(
        bytes(
            (C_SEND_NDAT, A_QUERY, 0x05, REC_QUERY, 0x00)
            + (address >> 8, address & 0xFF, extension, query_type)
        )
    )


def encode_occupied_query() -> bytes:
    """Ask which inputs/outputs are occupied (two answer records, 0x71 and 0x72)."""
    return encode_query(0x0000, EXT_OCCUPIED_INPUTS, QUERY_OCCUPIED)


def encode_text_query(address: int) -> bytes:
    """Ask for the plain-text name and detection area of one address."""
    return encode_query(address, EXT_TEXT, QUERY_TEXT)


def decode_text(raw: bytes) -> str:
    """Name from record 0x54. The panel stores the HD44780-A00 LCD character set."""
    special = {0xE1: "ä", 0xEF: "ö", 0xF5: "ü", 0xE2: "ß"}
    return "".join(special.get(b, chr(b) if 0x20 <= b <= 0x7E else "?") for b in raw).rstrip()


@dataclass(frozen=True, slots=True)
class Message:
    """Record 0x02: a single message (spontaneous event or command)."""

    device: int
    address: int
    extension: int
    art: int

    @property
    def active(self) -> bool:
        return self.art & 0x80 == 0


@dataclass(frozen=True, slots=True)
class BlockStatus:
    """Record 0x24: a bit field starting at ``base``."""

    device: int
    base: int
    extension: int
    status: bytes

    def addresses(self) -> range:
        return range(self.base, self.base + 8 * len(self.status))

    def is_active(self, address: int) -> bool | None:
        offset = address - self.base
        if offset < 0 or offset >= 8 * len(self.status):
            return None
        return not (self.status[offset // 8] >> (offset % 8)) & 1

    def active_addresses(self) -> list[int]:
        return [a for a in self.addresses() if self.is_active(a)]


@dataclass(frozen=True, slots=True)
class AreaInfo:
    """Record 0x0C: address, area bit mask and detection area of a text answer."""

    device: int
    address: int
    extension: int
    areas: int
    detection_area: int


@dataclass(frozen=True, slots=True)
class PanelError:
    """Record 0x11: the panel rejected a frame or the address is not occupied."""

    device: int
    code: int

    @property
    def not_occupied(self) -> bool:
        return self.code in ERRORS_NOT_OCCUPIED


@dataclass(frozen=True, slots=True)
class Record:
    """One VdS 2465 record: type and payload."""

    type: int
    payload: bytes

    def as_message(self) -> Message | None:
        p = self.payload
        if self.type != REC_MESSAGE or len(p) < 5:
            return None
        return Message(p[0], (p[1] << 8) | p[2], p[3], p[4])

    def as_block_status(self) -> BlockStatus | None:
        p = self.payload
        if self.type != REC_BLOCK_STATUS or len(p) < 4:
            return None
        return BlockStatus(p[0], (p[1] << 8) | p[2], p[3], bytes(p[4:]))

    def as_area_info(self) -> AreaInfo | None:
        p = self.payload
        if self.type != REC_AREA or len(p) < 6:
            return None
        return AreaInfo(p[0], (p[1] << 8) | p[2], p[3], p[4], p[5])

    def as_error(self) -> PanelError | None:
        p = self.payload
        if self.type != REC_ERROR or len(p) < 2:
            return None
        return PanelError(p[0], p[1])

    def as_text(self) -> str | None:
        if self.type != REC_TEXT:
            return None
        return decode_text(self.payload)


@dataclass(frozen=True, slots=True)
class Frame:
    """A checked FT1.2 frame."""

    user_data: bytes
    raw: bytes

    @property
    def control(self) -> int | None:
        return self.user_data[0] if self.user_data else None

    @property
    def function(self) -> Function:
        c = self.control
        if c is None:
            return Function.OTHER
        low = c & 0x0F
        if low == 3:
            return Function.SEND_NDAT
        if low == 1:
            return Function.CONFIRM_NAK
        if low == 0:
            return Function.SEND_NORM if c & 0x40 else Function.CONFIRM_ACK
        return Function.OTHER

    def records(self) -> Iterator[Record]:
        """Records from byte 2 of the user data on; stops at a truncated record."""
        buf, pos = self.user_data[2:], 0
        while pos + 2 <= len(buf):
            length = buf[pos]
            if pos + 2 + length > len(buf):
                return
            yield Record(buf[pos + 1], bytes(buf[pos + 2 : pos + 2 + length]))
            pos += 2 + length


class FrameDecoder:
    """Reassembles frames from a byte stream and resynchronises on garbage."""

    MAX_BUFFER = 1024

    def __init__(self) -> None:
        self._buf = bytearray()
        self.errors = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buf.extend(data)
        if len(self._buf) > self.MAX_BUFFER:
            del self._buf[: len(self._buf) - self.MAX_BUFFER]
        frames: list[Frame] = []
        while True:
            try:
                start = self._buf.index(START)
            except ValueError:
                self._buf.clear()
                return frames
            del self._buf[:start]
            if len(self._buf) < 4:
                return frames
            n = self._buf[1]
            if self._buf[2] != n or self._buf[3] != START:
                del self._buf[:1]
                self.errors += 1
                continue
            total = n + 6
            if len(self._buf) < total:
                return frames
            user = bytes(self._buf[4 : 4 + n])
            if self._buf[4 + n] != checksum(user) or self._buf[5 + n] != END:
                del self._buf[:1]
                self.errors += 1
                continue
            frames.append(Frame(user, bytes(self._buf[:total])))
            del self._buf[:total]
