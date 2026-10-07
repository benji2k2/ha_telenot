"""asyncio connection to the panel through a transparent serial-to-TCP converter.

Rules (see protocol.py):

- Every SEND_NORM poll and every SEND_NDAT telegram of the panel is answered. A poll is
  answered with CONFIRM_ACK unless a command or query is waiting – then that frame takes the
  send window instead. CONFIRM_ACK/NAK from the panel are never answered.
- Commands go out at once, without waiting for the send window: the panel polls only once
  per ~3.4 s cycle but accepts a command at any time and answers within ~0.2 s (seen with
  telenot-bridge, which has always sent this way). As in carhensi/telenot-esp-bridge, only
  when the line has been quiet for a moment – right after a panel burst a command collided
  reproducibly there. Unlike there, a command that arrives during a burst waits only for
  that quiet moment (well under a second), not for the next poll ~3 s later. A command the
  panel ignored times out and is retried in the send window; retries and queries always
  use the send window.
- One frame in flight at a time. Commands are confirmed by the panel's CONFIRM_ACK (an
  embedded error record 0x11 means rejected), retried on NAK or timeout, and never resent
  after a reconnect.
- Event log entries (message records) go to event listeners; they never change the state.
- The connection is rebuilt without limit (backoff), and also when the panel stays silent
  although TCP is up. Everything known about the panel is forgotten on every disconnect.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
from enum import Enum
import logging
import time

from . import protocol as p
from .state import (
    ADDR_ALARM,
    ADDR_ARMED_AWAY,
    ADDR_ARMED_HOME,
    ADDR_DISARMED,
    PanelState,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Timing:
    """All timeouts in seconds; tests shorten them."""

    connect_timeout: float = 10.0
    backoff_min: float = 5.0
    backoff_max: float = 60.0
    liveness: float = 30.0  # no frame for this long → reconnect
    command_timeout: float = 3.0  # per attempt, until CONFIRM_ACK
    command_attempts: int = 3
    query_timeout: float = 4.0  # per attempt, until the answer telegram
    query_attempts: int = 3
    immediate_commands: bool = True  # first attempt without waiting for the send window
    quiet_before_send: float = 0.4  # s since the last frame before sending immediately
    quiet_wait_max: float = 1.5  # s to wait for that quiet moment, else the send window
    send_timeout: float = 5.0  # a write that does not drain in time means a dead socket


class CommandResult(Enum):
    OK = "ok"
    REJECTED = "rejected"  # CONFIRM_ACK carried an error record 0x11
    NAK = "nak"
    TIMEOUT = "timeout"
    NOT_CONNECTED = "not_connected"
    NOT_READY = "not_ready"


@dataclass(slots=True)
class Detector:
    """One occupied address with what the panel told about it."""

    address: int
    kind: str  # "input" | "output"
    name: str | None = None
    detection_area: int | None = None
    areas: int | None = None
    status: str = "pending"  # pending | named | not_occupied | no_answer


@dataclass(slots=True)
class _Job:
    frame: bytes
    kind: str  # "command" | "occupied" | "text"
    max_attempts: int
    timeout: float
    address: int | None = None
    future: asyncio.Future = field(default_factory=asyncio.Future)
    attempts: int = 0
    deadline: float = 0.0
    occupied: dict[int, str] = field(default_factory=dict)
    seen: set[int] = field(default_factory=set)


class TelenotClient:
    """Keeps one connection to the converter alive and tracks the panel state."""

    def __init__(self, host: str, port: int, timing: Timing | None = None) -> None:
        self.host = host
        self.port = port
        self.timing = timing or Timing()
        self.state = PanelState()
        self.connected = False  # TCP up and the panel is talking
        self.last_frame: float | None = None
        self.last_result: CommandResult | None = None
        self.stats = {"connects": 0, "frames": 0, "acks_sent": 0, "frame_errors": 0}
        self._listeners: list[Callable[[set[int]], None]] = []
        self._event_listeners: list[Callable[[p.PanelEvent], None]] = []
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._jobs: deque[_Job] = deque()
        self._in_flight: _Job | None = None
        self._stopping = False

    # ───────────────────────── life cycle ─────────────────────────

    @property
    def available(self) -> bool:
        """The panel is reachable and both status telegrams have arrived."""
        return self.connected and self.state.complete

    def add_listener(self, callback: Callable[[set[int]], None]) -> Callable[[], None]:
        """Callback with the changed addresses; an empty set means availability changed."""
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def add_event_listener(self, callback: Callable[[p.PanelEvent], None]) -> Callable[[], None]:
        """Callback for every event log entry of the panel."""
        self._event_listeners.append(callback)
        return lambda: self._event_listeners.remove(callback)

    def start(self) -> None:
        self._stopping = False
        self._task = asyncio.get_running_loop().create_task(self._run(), name="telenot")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        await self._close()

    async def _run(self) -> None:
        backoff = self.timing.backoff_min
        while not self._stopping:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), self.timing.connect_timeout
                )
            except (OSError, TimeoutError) as err:
                _LOGGER.debug("Connect to %s:%s failed: %s", self.host, self.port, err)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.timing.backoff_max)
                continue
            self.stats["connects"] += 1
            self._writer = writer
            try:
                await self._read_loop(reader)
            except (OSError, ConnectionError) as err:
                _LOGGER.debug("Connection lost: %s", err)
            finally:
                await self._close()
            if self.last_frame is not None:
                backoff = self.timing.backoff_min  # the panel was talking: retry soon
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.timing.backoff_max)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        decoder = p.FrameDecoder()
        self.last_frame = None
        silent_since = time.monotonic()
        while True:
            try:
                data = await asyncio.wait_for(reader.read(4096), 0.25)
            except TimeoutError:
                data = None
            now = time.monotonic()
            if data == b"":
                raise ConnectionError("converter closed the connection")
            if data:
                errors = decoder.errors
                for frame in decoder.feed(data):
                    silent_since = now
                    await self._handle(frame, now)
                self.stats["frame_errors"] += decoder.errors - errors
            self._check_timeouts(now)
            if now - silent_since > self.timing.liveness:
                raise ConnectionError("panel silent")

    async def _close(self) -> None:
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()
        was_connected = self.connected
        self.connected = False
        self.state.clear()
        # Never resend after a reconnect: fail everything that is waiting.
        for job in [self._in_flight, *self._jobs]:
            if job is not None and not job.future.done():
                job.future.set_result(CommandResult.NOT_CONNECTED)
        self._in_flight = None
        self._jobs.clear()
        if was_connected:
            self._notify(set())

    # ───────────────────────── frames ─────────────────────────

    async def _handle(self, frame: p.Frame, now: float) -> None:
        self.stats["frames"] += 1
        self.last_frame = now
        if not self.connected:
            self.connected = True
            self._notify(set())
        function = frame.function
        if function is p.Function.SEND_NORM:
            await self._send(self._next_job_frame(now) or p.CONF_ACK)
        elif function is p.Function.SEND_NDAT:
            await self._send(p.CONF_ACK)
            complete_before = self.state.complete
            changed = self.state.apply_frame(frame)
            self._answer_queries(frame)
            if complete_before != self.state.complete:
                self._notify(set())  # now available: every entity, not only changed bits
            elif changed:
                self._notify(changed)
            if (event := frame.event()) is not None:
                self._notify_event(event)
        elif function in (p.Function.CONFIRM_ACK, p.Function.CONFIRM_NAK):
            self._confirm(frame, function is p.Function.CONFIRM_ACK, now)

    async def _send(self, data: bytes) -> None:
        if self._writer is None:
            return
        if data == p.CONF_ACK:
            self.stats["acks_sent"] += 1
        self._writer.write(data)
        try:
            async with asyncio.timeout(self.timing.send_timeout):
                await self._writer.drain()
        except TimeoutError as err:
            raise ConnectionError("send stalled") from err

    def _notify_event(self, event: p.PanelEvent) -> None:
        _LOGGER.debug("Panel event %s", event)
        for callback in list(self._event_listeners):
            try:
                callback(event)
            except Exception:
                _LOGGER.exception("Event listener failed")

    def _notify(self, changed: set[int]) -> None:
        for callback in list(self._listeners):
            try:
                callback(changed)
            except Exception:
                _LOGGER.exception("Listener failed")

    # ───────────────────────── jobs (commands and queries) ─────────────────────────

    def _next_job_frame(self, now: float) -> bytes | None:
        """Frame that takes this send window, if any."""
        if self._in_flight is not None or not self._jobs:
            return None
        job = self._jobs.popleft()
        job.attempts += 1
        job.deadline = now + job.timeout
        self._in_flight = job
        return job.frame

    def _check_timeouts(self, now: float) -> None:
        job = self._in_flight
        if job is None or now < job.deadline:
            return
        self._in_flight = None
        if job.attempts < job.max_attempts:
            self._jobs.appendleft(job)  # next send window
            return
        if job.kind == "command":
            self._finish(job, CommandResult.TIMEOUT)
        else:
            self._finish(job, "no_answer")

    def _confirm(self, frame: p.Frame, ack: bool, now: float) -> None:
        job = self._in_flight
        if job is None:
            return
        errors = [e for r in frame.records() if (e := r.as_error()) is not None]
        if job.kind == "text" and any(e.not_occupied for e in errors):
            self._in_flight = None
            self._finish(job, "not_occupied")
            return
        if job.kind != "command":
            return  # queries are answered by a SEND_NDAT telegram
        self._in_flight = None
        if errors:
            self._finish(job, CommandResult.REJECTED)
        elif ack:
            self._finish(job, CommandResult.OK)
        elif job.attempts < job.max_attempts:
            self._jobs.appendleft(job)
        else:
            self._finish(job, CommandResult.NAK)

    def _answer_queries(self, frame: p.Frame) -> None:
        job = self._in_flight
        if job is None or job.kind == "command":
            return
        if job.kind == "occupied":
            for record in frame.records():
                block = record.as_block_status()
                if block is None or block.extension not in (
                    p.EXT_OCCUPIED_INPUTS,
                    p.EXT_OCCUPIED_OUTPUTS,
                ):
                    continue
                kind = "input" if block.extension == p.EXT_OCCUPIED_INPUTS else "output"
                for address in block.active_addresses():
                    job.occupied[address] = kind
                job.seen.add(block.extension)
            if job.seen == {p.EXT_OCCUPIED_INPUTS, p.EXT_OCCUPIED_OUTPUTS}:
                self._in_flight = None
                self._finish(job, dict(job.occupied))
            return
        info = next((i for r in frame.records() if (i := r.as_area_info()) is not None), None)
        name = next((t for r in frame.records() if (t := r.as_text()) is not None), None)
        if info is not None and name is not None and info.address == job.address:
            self._in_flight = None
            self._finish(job, (name, info.detection_area, info.areas))

    @staticmethod
    def _finish(job: _Job, result: object) -> None:
        if not job.future.done():
            job.future.set_result(result)

    async def _wait_for_quiet(self) -> bool:
        """Wait until no frame has arrived for ``quiet_before_send`` (bounded)."""
        give_up = time.monotonic() + self.timing.quiet_wait_max
        while self.connected and self.last_frame is not None:
            now = time.monotonic()
            quiet_at = self.last_frame + self.timing.quiet_before_send
            if now >= quiet_at:
                return True
            if quiet_at > give_up:
                return False
            await asyncio.sleep(quiet_at - now)
        return False

    async def _submit(self, job: _Job) -> object:
        if not self.connected:
            return CommandResult.NOT_CONNECTED
        if job.kind == "command" and self.timing.immediate_commands:
            await self._wait_for_quiet()
            if not self.connected:
                return CommandResult.NOT_CONNECTED
        if (
            job.kind == "command"
            and self.timing.immediate_commands
            and self._in_flight is None
            and not self._jobs
            and self.last_frame is not None
            and time.monotonic() - self.last_frame >= self.timing.quiet_before_send
        ):
            # first attempt at once; the deadline check moves a retry to the send window
            job.attempts = 1
            job.deadline = time.monotonic() + job.timeout
            self._in_flight = job
            with contextlib.suppress(OSError, ConnectionError):
                await self._send(job.frame)  # a broken socket fails the job via _close
        else:
            self._jobs.append(job)
        return await job.future

    # ───────────────────────── commands ─────────────────────────

    async def send_command(self, address: int, art: int) -> CommandResult:
        frame = p.encode_command(address, p.EXT_OUTPUTS, art)
        job = _Job(
            frame,
            "command",
            self.timing.command_attempts,
            self.timing.command_timeout,
            address,
        )
        result = await self._submit(job)
        self.last_result = result  # type: ignore[assignment]
        _LOGGER.debug("Command %s → %s", frame.hex(), result)
        return result  # type: ignore[return-value]

    async def disarm(self) -> CommandResult:
        return await self.send_command(ADDR_DISARMED, p.ART_DISARM)

    async def arm_home(self) -> CommandResult:
        if self.state.ready_home is False and not self.state.is_active(ADDR_ARMED_HOME):
            return CommandResult.NOT_READY
        return await self.send_command(ADDR_ARMED_HOME, p.ART_ARM_HOME)

    async def arm_away(self) -> CommandResult:
        if self.state.ready_away is False:
            return CommandResult.NOT_READY
        return await self.send_command(ADDR_ARMED_AWAY, p.ART_ARM_AWAY)

    async def reset(self) -> CommandResult:
        return await self.send_command(ADDR_ALARM, p.ART_RESET)

    # ───────────────────────── scan ─────────────────────────

    async def scan(self, progress: Callable[[float], None] | None = None) -> list[Detector]:
        """Read occupied addresses with name and detection area. Read-only queries only."""
        occupied = await self._submit(
            _Job(
                p.encode_occupied_query(),
                "occupied",
                self.timing.query_attempts,
                self.timing.query_timeout,
            )
        )
        if not isinstance(occupied, dict):
            raise ConnectionError(f"occupancy query failed: {occupied}")
        detectors = [Detector(a, kind) for a, kind in sorted(occupied.items())]
        for index, detector in enumerate(detectors):
            answer = await self._submit(
                _Job(
                    p.encode_text_query(detector.address),
                    "text",
                    self.timing.query_attempts,
                    self.timing.query_timeout,
                    detector.address,
                )
            )
            if answer is CommandResult.NOT_CONNECTED:
                raise ConnectionError("connection lost during scan")
            if isinstance(answer, tuple):
                detector.name, detector.detection_area, detector.areas = answer
                detector.status = "named"
            else:
                detector.status = str(answer)
            if progress is not None:
                progress((index + 1) / len(detectors))
        return detectors
