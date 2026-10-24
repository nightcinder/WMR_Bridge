"""Command line entry point: ``python3 -m wmr_bridge``."""

from __future__ import annotations

import argparse
import asyncio
import errno
import ipaddress
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
from .mdns import MdnsAdvertiser
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
    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="address to listen on (default 127.0.0.1, loopback only)",
    )
    parser.add_argument("--port", type=int, default=8765, help="WebSocket port (default 8765)")
    parser.add_argument("--token", help="shared token required from clients (prefer --token-file)")
    parser.add_argument("--token-file", help="file holding the shared token on its first line")
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="allow this browser Origin header (repeatable); other Origin values are rejected",
    )
    parser.add_argument(
        "--insecure-no-token",
        action="store_true",
        help="permit a non-loopback bind without a token (UNSAFE; do not use on a shared network)",
    )
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
    hid.add_argument(
        "--hand",
        choices=("both", "left", "right"),
        default="both",
        help="only open the selected controller hand (default both; mirrors WMR_CFG_BRIDGE_HANDS)",
    )
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
    parser.add_argument(
        "--advertise-mdns",
        action="store_true",
        help=(
            "publish _wmr-bridge._tcp.local. with avahi-publish-service or "
            "systemd-resolved when available (optional; never fatal)"
        ),
    )
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
            reason = exc.strerror or str(exc)
            if exc.errno in (errno.EACCES, errno.EPERM):
                hint = (
                    "the provided systemd unit runs as user 'wmr', so a "
                    "root-owned token file is not readable by it. Check "
                    f"'ls -l {args.token_file}' and fix ownership with "
                    f"'sudo chown wmr:wmr {args.token_file}' (mode 0600)"
                )
            else:
                hint = f"check that {args.token_file} exists and is readable"
            raise SystemExit(
                f"cannot read --token-file {args.token_file}: {reason}. "
                f"This daemon is running as uid {os.getuid()}; {hint}."
            ) from exc
        try:
            mode = os.stat(args.token_file).st_mode
            if mode & (stat.S_IRGRP | stat.S_IROTH):
                LOG.warning("%s is readable by group/other; chmod 600 is recommended", args.token_file)
        except OSError:
            pass
    if token == "":
        raise SystemExit("token is empty")
    return token


def _is_loopback_bind(host: str) -> bool:
    """True only when every address ``host`` resolves to is a loopback address."""

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addresses = {info[4][0] for info in infos}
    if not addresses:
        return False
    try:
        return all(ipaddress.ip_address(address).is_loopback for address in addresses)
    except ValueError:
        return False


def _require_auth_for_bind(args: argparse.Namespace, token: str | None) -> None:
    """Refuse to expose an unauthenticated listener beyond loopback."""

    if token is not None or args.insecure_no_token or _is_loopback_bind(args.bind):
        return
    raise SystemExit(
        f"--bind {args.bind} is not a loopback address but no token was given. "
        "Pass --token-file PATH (preferred) or --token VALUE, or pass "
        "--insecure-no-token to serve without authentication anyway "
        "(unsafe: every host that can reach this port can read controller data)."
    )


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
    _require_auth_for_bind(args, token)
    allowed_hands = (
        (0,) if args.hand == "left" else (1,) if args.hand == "right" else (0, 1)
    )
    bridge = Bridge(
        token=token,
        queue_size=args.queue_size,
        batch_max=args.batch_max,
        batch_hold_ms=args.batch_hold_ms,
        fragment_capacity=args.fragment_capacity,
        auth_timeout_s=args.auth_timeout,
        allowed_hands=allowed_hands,
    )
    server = BridgeServer(
        bridge,
        host=args.bind,
        port=args.port,
        allowed_origins=tuple(args.allow_origin),
    )
    await server.start()

    addresses = _local_addresses() if args.bind in ("0.0.0.0", "::") else [args.bind]
    LOG.info("listening on %s", ", ".join(f"ws://{address}:{server.port}/wmr" for address in addresses))
    if token:
        LOG.info("authentication: shared token required")
    elif _is_loopback_bind(args.bind):
        LOG.info("authentication: none (loopback only)")
    else:
        LOG.warning(
            "authentication: NONE (--insecure-no-token) on non-loopback %s",
            args.bind,
        )
    if args.allow_origin:
        LOG.info("allowed browser origins: %s", ", ".join(args.allow_origin))
    LOG.info("payloads: FF11 raw, FF12 decoded, FF14 batched; protocol 1")
    LOG.info("hand filter: %s", ", ".join(bridge.hand_filter))

    advertiser: MdnsAdvertiser | None = None
    if args.advertise_mdns:
        advertiser = MdnsAdvertiser(port=server.port, hands=bridge.hand_filter)
        advertiser.start()

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
            allowed_hands=allowed_hands,
            explicit_paths=args.hidraw,
            include_all=args.all_hidraw,
            poll_interval=args.poll_interval,
        )
        manager.start()
        LOG.info("watching hidraw for WMR controllers (hand=%s)", args.hand)

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
    replay_task: asyncio.Task[None] | None = None
    if replay is not None and args.replay_once:
        replay_task = asyncio.create_task(
            _stop_after_replay(replay, stop), name="replay-stop"
        )

    try:
        await stop.wait()
    finally:
        stop.set()
        status_task.cancel()
        if duration_task is not None:
            duration_task.cancel()
        if replay_task is not None:
            replay_task.cancel()
        if replay is not None:
            await replay.close()
        if manager is not None:
            await manager.close()
        if advertiser is not None:
            advertiser.close()
        await server.close()
        LOG.info("stopped: %s", bridge.status())
    return 0


async def _stop_after(stop: asyncio.Event, seconds: float) -> None:
    await asyncio.sleep(max(0.0, seconds))
    stop.set()


async def _stop_after_replay(replay: ReplaySource, stop: asyncio.Event) -> None:
    try:
        await replay.wait()
    finally:
        # --replay-once promises a clean exit after one pass, even if the
        # replay failed part-way through.
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
