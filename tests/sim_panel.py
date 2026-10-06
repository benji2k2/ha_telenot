"""Simulated complex 400 behind a serial-to-TCP converter, for tests.

Behaves like the captures: polls with SEND_NORM, sends the input and output status telegrams
every few polls, confirms commands and answers occupancy and text queries. Test hooks can
drop the connection, fall silent, swallow frames or reject commands.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from custom_components.telenot import protocol as p
from custom_components.telenot.state import (
    ADDR_ALARM,
    ADDR_ARMED_AWAY,
    ADDR_ARMED_HOME,
    ADDR_DISARMED,
    ADDR_READY_AWAY,
    ADDR_READY_HOME,
)

SEND_NORM = bytes.fromhex("6802026840024216")


def ndat(*records: bytes) -> bytes:
    return p.encode_frame(bytes((0x73, 0x02)) + b"".join(records))


def record(record_type: int, payload: bytes) -> bytes:
    return bytes((len(payload), record_type)) + payload


def block(base: int, extension: int, active: set[int], length: int) -> bytes:
    status = bytearray([0xFF] * length)
    for address in active:
        offset = address - base
        if 0 <= offset < 8 * length:
            status[offset // 8] &= ~(1 << (offset % 8))
    return record(p.REC_BLOCK_STATUS, bytes((0, base >> 8, base & 0xFF, extension)) + status)


@dataclass
class Names:
    """Occupied addresses and their names/detection areas for the scan."""

    entries: dict[int, tuple[str, int]] = field(default_factory=dict)
    not_occupied: set[int] = field(default_factory=set)


class SimPanel:
    def __init__(self, names: Names | None = None, poll_interval: float = 0.02) -> None:
        self.names = names or Names()
        self.poll_interval = poll_interval
        self.status_every = 3  # polls between status telegrams
        self.active: set[int] = {ADDR_DISARMED, ADDR_READY_HOME, ADDR_READY_AWAY}
        self.received: list[bytes] = []
        self.commands: list[bytes] = []
        self.status_sent = 0
        self.connections = 0
        # test hooks
        self.silent = False
        self.swallow: set[bytes] = set()  # frames to ignore once each
        self.reject_next_command = False
        self.nak_next_command = False
        self._server: asyncio.base_events.Server | None = None
        self._writers: list[asyncio.StreamWriter] = []
        self.port = 0

    async def start(self, port: int = 0) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        await self.drop()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def drop(self) -> None:
        """Close every client connection (converter reboot, network glitch)."""
        for writer in self._writers:
            writer.close()
        self._writers.clear()
        await asyncio.sleep(0)

    def set(self, address: int, active: bool) -> None:
        (self.active.add if active else self.active.discard)(address)

    def status_frames(self) -> list[bytes]:
        inputs = {a for a in self.active if a < 0x0500}
        outputs = {a for a in self.active if a >= 0x0500}
        return [
            ndat(block(0x0000, p.EXT_INPUTS, inputs, 30)),
            ndat(block(0x0500, p.EXT_OUTPUTS, outputs, 48)),
        ]

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # A converter serves one client: a new one replaces the old.
        await self.drop()
        self._writers.append(writer)
        self.connections += 1
        decoder = p.FrameDecoder()
        polls = 0
        try:
            while not writer.is_closing():
                if not self.silent:
                    polls += 1
                    if polls % self.status_every == 0:
                        for frame in self.status_frames():
                            writer.write(frame)
                            self.status_sent += 1
                    writer.write(SEND_NORM)
                    await writer.drain()
                try:
                    data = await asyncio.wait_for(reader.read(4096), self.poll_interval)
                except TimeoutError:
                    continue
                if not data:
                    break
                for frame in decoder.feed(data):
                    self.received.append(frame.raw)
                    await self._answer(frame.raw, writer)
        except (ConnectionError, OSError):
            pass
        finally:
            if writer in self._writers:
                self._writers.remove(writer)
            writer.close()

    async def _answer(self, raw: bytes, writer: asyncio.StreamWriter) -> None:
        if raw == p.CONF_ACK:
            return
        if raw in self.swallow:
            self.swallow.discard(raw)
            return
        record_type, address, query_type = raw[7], (raw[9] << 8) | raw[10], raw[12]
        if record_type == p.REC_MESSAGE:
            self.commands.append(raw)
            if self.nak_next_command:
                self.nak_next_command = False
                writer.write(bytes.fromhex("6802026801020316"))  # CONFIRM_NAK
                return
            if self.reject_next_command:
                self.reject_next_command = False
                writer.write(p.encode_frame(bytes((0x00, 0x02)) + record(p.REC_ERROR, b"\x00\x18")))
                return
            writer.write(p.CONF_ACK)
            self._apply_command(address, raw[12])
        elif record_type == p.REC_QUERY and query_type == p.QUERY_OCCUPIED:
            writer.write(p.CONF_ACK)
            inputs = {a for a in self.names.entries if a < 0x0500} | self.names.not_occupied
            outputs = {a for a in self.names.entries if a >= 0x0500}
            writer.write(ndat(block(0x0000, p.EXT_OCCUPIED_INPUTS, inputs, 30)))
            writer.write(ndat(block(0x0500, p.EXT_OCCUPIED_OUTPUTS, outputs, 48)))
        elif record_type == p.REC_QUERY and query_type == p.QUERY_TEXT:
            if address in self.names.not_occupied or address not in self.names.entries:
                writer.write(p.encode_frame(bytes((0x00, 0x02)) + record(p.REC_ERROR, b"\x00\x19")))
                return
            name, area = self.names.entries[address]
            writer.write(p.CONF_ACK)
            info = bytes((0x01, address >> 8, address & 0xFF, p.EXT_TEXT, 0xFE, area))
            text = name.encode("latin-1").ljust(16)
            writer.write(ndat(record(p.REC_AREA, info), record(p.REC_TEXT, text)))
        await writer.drain()

    def _apply_command(self, address: int, art: int) -> None:
        modes = {ADDR_DISARMED, ADDR_ARMED_HOME, ADDR_ARMED_AWAY}
        if art == p.ART_RESET:
            self.active.discard(ADDR_ALARM)
        elif address in modes:
            self.active -= modes
            self.active.add(address)
