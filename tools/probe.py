"""Probe the panel with the integration's own client – without Home Assistant.

Read-only by default: the client only acknowledges the panel's telegrams and prints every
change. Stop any other client of the converter first (one client at a time). Run it with the
Python of the test environment: importing the package needs ``homeassistant`` installed.

    python tools/probe.py HOST [--port 8234] [--duration 600] [--names scan.json]
    python tools/probe.py HOST --gap 120          # disconnect for 120 s, then compare
    python tools/probe.py HOST --scan out.json    # read names and detection areas
    python tools/probe.py HOST --command arm_home --confirm

``--gap`` simulates a Home Assistant restart: nobody acknowledges the panel for that long.
Afterwards the full status is compared with the one before (e.g. a transmission-path fault).
``--command`` sends one command (arm_home, arm_away, disarm, reset) and only with
``--confirm``. ``--names`` takes a JSON list of objects with ``address`` (or ``adresse``) and
``name`` – the scan result stays on your machine, it is not part of the repository.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.telenot.client import (  # noqa: E402
    CommandResult,
    TelenotClient,
    Timing,
)
from custom_components.telenot.inventory import to_storage  # noqa: E402
from custom_components.telenot.state import (  # noqa: E402
    ADDR_ALARM,
    ADDR_ARMED_AWAY,
    ADDR_ARMED_HOME,
    ADDR_DISARMED,
    ADDR_FAULT,
    ADDR_READY_AWAY,
    ADDR_READY_HOME,
)

SYSTEM_NAMES = {
    ADDR_DISARMED: "disarmed",
    ADDR_ARMED_HOME: "armed home",
    ADDR_ARMED_AWAY: "armed away",
    ADDR_ALARM: "alarm",
    ADDR_FAULT: "fault",
    ADDR_READY_HOME: "ready home",
    ADDR_READY_AWAY: "ready away",
}
COMMANDS = ("arm_home", "arm_away", "disarm", "reset")


def load_names(path: str | None) -> dict[int, str]:
    names = dict(SYSTEM_NAMES)
    if path:
        for item in json.loads(Path(path).read_text(encoding="utf-8")):
            address = item.get("address", item.get("adresse"))
            if isinstance(address, str):
                address = int(address, 16)
            if address is not None and item.get("name"):
                names.setdefault(address, " ".join(item["name"].split()))
    return names


class Probe:
    def __init__(
        self,
        client: TelenotClient,
        names: dict[int, str],
        out: Callable[[str], None] = print,
    ) -> None:
        self.client = client
        self.names = names
        self.out = out
        self.started = time.monotonic()
        self.changes = 0
        self.availability: list[tuple[float, bool]] = []
        client.add_listener(self._changed)

    def log(self, text: str) -> None:
        self.out(f"{time.monotonic() - self.started:8.1f}s  {text}")

    def label(self, address: int) -> str:
        name = self.names.get(address)
        return f"0x{address:04X}" + (f" {name}" if name else "")

    def summary(self) -> str:
        s = self.client.state
        return (
            f"state={s.arm_state.value} ready_home={s.ready_home} ready_away={s.ready_away} "
            f"alarm={s.is_active(ADDR_ALARM)} fault={s.is_active(ADDR_FAULT)}"
        )

    def snapshot(self) -> dict[int, bool]:
        return self.client.state.snapshot()

    def _changed(self, changed: set[int]) -> None:
        if not changed:
            available = self.client.available
            if not self.availability or self.availability[-1][1] != available:
                self.availability.append((time.monotonic() - self.started, available))
                self.log(f"available={available}  {self.summary() if available else ''}")
            return
        if not self.client.state.complete:
            return  # first telegram after connecting: every bit is "new"
        for address in sorted(changed):
            self.changes += 1
            self.log(f"{self.label(address)} → {self.client.state.is_active(address)}")

    def stats(self) -> str:
        st = self.client.stats
        return (
            f"connects={st['connects']} frames={st['frames']} acks_sent={st['acks_sent']} "
            f"frame_errors={st['frame_errors']} changes={self.changes}"
        )


async def wait_available(client: TelenotClient, timeout: float) -> bool:
    try:
        async with asyncio.timeout(timeout):
            while not client.available:
                await asyncio.sleep(0.1)
    except TimeoutError:
        return False
    return True


def diff(before: dict[int, bool], after: dict[int, bool]) -> list[tuple[int, bool | None, bool]]:
    return [
        (address, before.get(address), active)
        for address, active in sorted(after.items())
        if before.get(address) != active
    ]


async def run(args: argparse.Namespace, out: Callable[[str], None] = print) -> int:
    timing = (
        Timing()
        if not args.fast
        else Timing(
            connect_timeout=1.0,
            backoff_min=0.05,
            backoff_max=0.2,
            liveness=0.5,
            command_timeout=0.2,
            query_timeout=0.2,
        )
    )
    client = TelenotClient(args.host, args.port, timing)
    probe = Probe(client, load_names(args.names), out)
    client.start()
    try:
        if not await wait_available(client, args.connect_wait):
            probe.log("panel not available – is another client connected?")
            return 2
        probe.log(f"complete status: {probe.summary()}")

        if args.scan:
            probe.log("scan started (read-only queries)")
            detectors = await client.scan(lambda f: None)
            Path(args.scan).write_text(
                json.dumps(to_storage(detectors), ensure_ascii=False, indent=1), encoding="utf-8"
            )
            named = sum(d.status == "named" for d in detectors)
            probe.log(f"scan done: {len(detectors)} occupied, {named} named → {args.scan}")
            return 0

        if args.command:
            if not args.confirm:
                probe.log(f"refusing to send {args.command} without --confirm")
                return 2
            probe.log(f"sending {args.command}")
            result = await getattr(client, args.command)()
            probe.log(f"result {result.value}")
            await asyncio.sleep(args.settle)
            probe.log(f"after: {probe.summary()}")
            return 0 if result is CommandResult.OK else 1

        if args.gap:
            before = probe.snapshot()
            probe.log(f"disconnecting for {args.gap:.0f} s (nobody acknowledges the panel)")
            await client.stop()
            await asyncio.sleep(args.gap)
            client.start()
            if not await wait_available(client, args.connect_wait):
                probe.log("panel not available after the gap")
                return 2
            changed = diff(before, probe.snapshot())
            probe.log(f"back: {probe.summary()}")
            if changed:
                for address, old, new in changed:
                    probe.log(f"changed during the gap: {probe.label(address)} {old} → {new}")
            else:
                probe.log("no bit changed during the gap")
            await asyncio.sleep(args.settle)
            later = diff(before, probe.snapshot())
            if later != changed:
                probe.log(f"{len(later)} bits differ from before after {args.settle:.0f} s more")
            return 0

        await asyncio.sleep(args.duration)
        probe.log(f"end: {probe.summary()}")
        return 0
    finally:
        await client.stop()
        out(probe.stats())


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=8234)
    ap.add_argument("--names", help="JSON list with address/name, kept local")
    ap.add_argument("--duration", type=float, default=600.0, help="watch for this many seconds")
    ap.add_argument("--connect-wait", type=float, default=20.0)
    ap.add_argument("--settle", type=float, default=10.0, help="watch after a command or gap")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--gap", type=float, help="disconnect for this many seconds, then compare")
    mode.add_argument("--scan", metavar="OUT.json", help="read names and detection areas")
    mode.add_argument("--command", choices=COMMANDS)
    ap.add_argument("--confirm", action="store_true", help="required for --command")
    ap.add_argument("--fast", action="store_true", help=argparse.SUPPRESS)  # tests
    return ap.parse_args(argv)


if __name__ == "__main__":
    sys.exit(asyncio.run(run(parse())))
