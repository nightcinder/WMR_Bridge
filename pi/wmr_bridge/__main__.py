"""Command line entry point: ``python3 -m wmr_bridge``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket
import stat
import sys

from . import __version__
from .bluez import discover_hidraw
from .bridge import Bridge
from .inputs import HidrawManager, ReplaySource, read_capture
from .server import BridgeServer

LOG = logging.getLogger("wmr_bridge")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wmr_bridge",
        description=(
            "Linux/Raspberry Pi host bridge for WMR motion controllers. "
            "Connects over Classic Bluetooth (BlueZ hidraw) and serves the "
            "FF11/FF12/FF14 stream over a local WebSocket on Wi-Fi."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--bind", default="0.0.0.0", help="address to listen on (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="WebSocket port (default 8765)")
    parser.add_argument("--token", help="shared token required from clients (prefer --token-file)")
    parser.add_argument("--token-file", help="file holding the shared token on its first line")
    parser.add_argument("--queue-size", type=int, default=512, help="per-client outbound queue (frames)")
    parser.add_argument("--batch-max", type=int, default=9, help="records per FF14 batch (1..9)")
    parser.add_argument("--batch-hold-ms", type=float, default=5.0, help="maximum batch wait in ms")
    parser.add_argument(
        "--fragment-capacity",
        type=int,
        default=244,
        help="raw fragment size ceiling including the 12-byte header (default 244)",
    )
    parser.add_argument("--auth-timeout", type=float, default=5.0, help="seconds a client has to authenticate")

    hid = parser.add_argument_group("controllers (BlueZ)")
    hid.add_argument("--hidraw", action="append", default=[], metavar="PATH", help="open this /dev/hidrawN")
    hid.add_argument("--all-hidraw", action="store_true", help="watch every HID device, not only WMR names")
    hid.add_argument("--left", metavar="MATCH", help="assign a device to LEFT by address, path or name part")
    hid.add_argument("--right", metavar="MATCH", help="assign a device to RIGHT by address, path or name part")
    hid.add_argument("--poll-interval", type=float, default=1.0, help="hidraw hotplug poll interval in seconds")

    replay = parser.add_argument_group("replay (no hardware)")
    replay.add_argument("--replay", metavar="FILE", help="replay a '<label> <hex>' capture")
    replay.add_argument("--replay-rate", type=float, default=160.0, help="replayed frames per second")
    replay.add_argument(
        "--replay-hand",
        choices=("auto", "left", "right", "alternate"),
        default="auto",
        help="hand assignment for replay (default auto)",
    )
    replay.add_argument("--replay-once", action="store_true", help="stop after one pass of the capture")

    parser.add_argument("--list", action="store_true", help="list hidraw devices and exit")
    parser.add_argument("--duration", type=float, help="stop after this many seconds (bench/testing)")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    parser.add_argument("--log-file", help="append logs to this file instead of stderr")
    return parser


def _read_token(args: argparse.Namespace) -> str | None:
    token = args.token or os.environ.get("WMR_BRIDGE_TOKEN")
    if args.token_file:
        try:
            with open(args.token_file, "r", encoding="utf-8") as handle:
                token = handle.readline().rstrip("\r\n")
        except OSError as exc:
            raise SystemExit(f"cannot read --token-file: {exc}") from exc
        try:
            mode = os.stat(args.token_file).st_mode
            if mode & (stat.S_IRGRP | stat.S_IROTH):
                LOG.warning("%s is readable by group/other; chmod 600 is recommended", args.token_file)
        except OSError:
            pass
    if token == "":
        raise SystemExit("token is empty")
    return token


def _local_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    addresses.discard("0.0.0.0")
    addresses.add("127.0.0.1")
    return sorted(addresses)


def _print_devices() -> int:
    devices = discover_hidraw(include_all=True)
    if not devices:
        print("no /dev/hidraw nodes found (is bluetoothd running, hidp loaded and a controller connected?)")
        return 1
    print(f"{'PATH':<20} {'HAND':<6} {'HID NAME':<32} ADDRESS")
    for device in devices:
        hand = ("LEFT", "RIGHT")[device.hand] if device.hand is not None else "-"
        print(f"{device.path:<20} {hand:<6} {device.name[:32]:<32} {device.uniq}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    token = _read_token(args)
    bridge = Bridge(
        token=token,
        queue_size=args.queue_size,
        batch_max=args.batch_max,
        batch_hold_ms=args.batch_hold_ms,
        fragment_capacity=args.fragment_capacity,
        auth_timeout_s=args.auth_timeout,
    )
    server = BridgeServer(bridge, host=args.bind, port=args.port)
    await server.start()

    addresses = _local_addresses() if args.bind in ("0.0.0.0", "::") else [args.bind]
    LOG.info("listening on %s", ", ".join(f"ws://{address}:{server.port}/wmr" for address in addresses))
    LOG.info("authentication: %s", "shared token required" if token else "none (trusted LAN only)")
    LOG.info("payloads: FF11 raw, FF12 decoded, FF14 batched; protocol 1")

    replay: ReplaySource | None = None
    manager: HidrawManager | None = None
    if args.replay:
        entries = read_capture(args.replay)
        replay = ReplaySource(
            entries,
            rate=args.replay_rate,
            hand=args.replay_hand,
            loop=not args.replay_once,
        )
        replay.start(bridge)
        LOG.info(
            "replaying %d frames at %.0f/s from %s (%s)",
            len(entries),
            args.replay_rate,
            args.replay,
            args.replay_hand,
        )
    else:
        manager = HidrawManager(
            bridge,
            left_spec=args.left,
            right_spec=args.right,
            explicit_paths=args.hidraw,
            include_all=args.all_hidraw,
            poll_interval=args.poll_interval,
        )
        manager.start()
        LOG.info("watching hidraw for WMR controllers")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signame, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    async def log_status() -> None:
        while not stop.is_set():
            await asyncio.sleep(5.0)
            status = bridge.status()
            hands = " ".join(
                f"{item['hand'].lower()}={item['reports']}({item['delivered']})"
                for item in status["hands"]
            )
            LOG.info(
                "clients=%d subscribed=%d %s dropped=%d",
                status["clients"],
                status["subscribed"],
                hands,
                status["dropped"],
            )

    status_task = asyncio.create_task(log_status(), name="status")
    duration_task: asyncio.Task[None] | None = None
    if args.duration:
        duration_task = asyncio.create_task(
            _stop_after(stop, args.duration), name="duration"
        )

    try:
        await stop.wait()
    finally:
        stop.set()
        status_task.cancel()
        if duration_task is not None:
            duration_task.cancel()
        if replay is not None:
            await replay.close()
        if manager is not None:
            await manager.close()
        await server.close()
        LOG.info("stopped: %s", bridge.status())
    return 0


async def _stop_after(stop: asyncio.Event, seconds: float) -> None:
    await asyncio.sleep(max(0.0, seconds))
    stop.set()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        filename=args.log_file,
    )
    if args.list:
        return _print_devices()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
