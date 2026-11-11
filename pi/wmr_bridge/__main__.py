"""Command line entry point: ``python3 -m wmr_bridge``."""

from __future__ import annotations

import argparse
import asyncio
import errno
import ipaddress
import logging
import math
import os
import shutil
import signal
import socket
import stat
import sys
from collections.abc import Callable
from typing import Any

from . import __version__
from .bluez import (
    HIDRAW_ROOT,
    BluezAutoPairer,
    BluezStatus,
    Runner,
    _run_command,
    collect_status,
    discover_hidraw,
    set_device_blocked,
)
from .bridge import Bridge
from .discovery import DISCOVERY_PORT, DiscoveryResponder
from .inputs import (
    IDLE_POWEROFF_TIMEOUT,
    IDLE_RECONNECT_COOLDOWN,
    IDLE_SLEEP_TIMEOUT,
    HidrawManager,
    ReplaySource,
    read_capture,
)
from .mdns import MdnsAdvertiser
from .server import BridgeServer
from .wifi import inspect_uplink, inspect_wifi, interface_ipv4, prefer_5ghz

LOG = logging.getLogger("wmr_bridge")
DEFAULT_PAIR_TOKEN_FILE = "/etc/wmr-bridge/token"


def _idle_seconds(value: str) -> float:
    """Parse a non-negative timeout; ``0`` disables the stage."""

    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid number of seconds: {value!r}") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("must be zero or a positive number of seconds")
    return seconds


def _validate_idle_timeouts(args: argparse.Namespace) -> None:
    """Power-off must not fire before the controller has had a chance to sleep."""

    sleep = args.idle_sleep_timeout
    poweroff = args.idle_poweroff_timeout
    if sleep > 0 and poweroff > 0 and poweroff < sleep:
        raise SystemExit(
            "--idle-poweroff-timeout must be 0 (disabled) or at least "
            "--idle-sleep-timeout"
        )


async def _pair_window(args: argparse.Namespace) -> int:
    """Ask the running daemon to open or close its manual BLE window."""

    from . import ws as wsmod

    token = args.token or os.environ.get("WMR_BRIDGE_TOKEN")
    token_file = args.token_file
    if token is None and token_file is None and os.path.exists(DEFAULT_PAIR_TOKEN_FILE):
        token_file = DEFAULT_PAIR_TOKEN_FILE
    if token is None and token_file:
        token = _read_token(
            argparse.Namespace(token=None, token_file=token_file)
        )
    if not args.close and args.seconds <= 0:
        print("--seconds must be greater than zero when opening a pairing window")
        return 1
    try:
        client = await wsmod.connect(args.host, args.port, "/wmr", timeout=5)
    except (OSError, asyncio.TimeoutError, wsmod.WebSocketError) as exc:
        print(
            f"wmr-bridge is not reachable at {args.host}:{args.port} ({exc}); "
            "start the service or pass --host/--port"
        )
        return 1
    try:
        hello = await asyncio.wait_for(client.recv_json(), 5)
        if hello.get("authRequired"):
            if token is None:
                print(
                    "the running service requires a token; pass --token-file "
                    f"(default {DEFAULT_PAIR_TOKEN_FILE})"
                )
                return 1
            await client.send_json({"type": "auth", "token": token})
            reply = await asyncio.wait_for(client.recv_json(), 5)
            if reply.get("type") != "auth_ok":
                print("authentication failed; check --token-file")
                return 1
        message: dict[str, Any] = {
            "type": "pairing",
            "action": "close" if args.close else "open",
        }
        if not args.close:
            message["seconds"] = args.seconds
        await client.send_json(message)
        reply = await asyncio.wait_for(client.recv_json(), 5)
        if reply.get("type") == "error":
            print(f"could not change the pairing window: {reply.get('code')}")
            return 1
        if reply.get("type") != "pairing":
            print(f"unexpected reply from wmr-bridge: {reply.get('type')!r}")
            return 1
        opened = reply.get("open") is True
        manual = reply.get("manual") is True
        if args.close:
            if manual:
                print(
                    "the daemon still reports a manual pairing window; check "
                    "'journalctl -u wmr-bridge'"
                )
                return 1
            if opened:
                reason = reply.get("reason")
                detail = f" ({reason})" if isinstance(reason, str) and reason else ""
                print(
                    "manual BLE pairing window closed; the adapter is still "
                    f"pairable{detail}."
                )
            else:
                print(
                    "manual BLE pairing window closed; the stored-bond rule "
                    "now applies."
                )
        else:
            if not manual:
                print(
                    "the daemon did not open a manual pairing window; check "
                    "'journalctl -u wmr-bridge'"
                )
                return 1
            if not opened:
                print(
                    "the daemon could not make the adapter pairable; check "
                    "'journalctl -u wmr-bridge'"
                )
                return 1
            print(
                f"BLE pairing window open for {args.seconds:g} s; "
                "pair your phone or controller now."
            )
        return 0
    finally:
        await client.close()


def _pair_command(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_pair_window(args))
    except KeyboardInterrupt:
        return 130


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
    parser.add_argument(
        "--no-ble",
        dest="ble",
        action="store_false",
        help=(
            "do not serve the FF10 GATT service over BlueZ BLE or advertise it "
            "(the peripheral is on by default; needs python3-dbus-next and a free "
            "hci adapter)"
        ),
    )
    parser.add_argument(
        "--ble-fallback",
        action="store_true",
        help=(
            "deprecated no-op alias; the BLE GATT peripheral is enabled by "
            "default, use --no-ble to disable it"
        ),
    )
    parser.add_argument(
        "--ble-adapter",
        default="hci0",
        metavar="NAME",
        help="BlueZ adapter for the BLE GATT peripheral (default hci0)",
    )
    parser.add_argument(
        "--ble-name",
        default="WMR Bridge",
        help="BLE advertising name for the GATT peripheral (default 'WMR Bridge')",
    )
    parser.add_argument(
        "--ble-pairing-window",
        type=float,
        default=180.0,
        metavar="SECONDS",
        help=(
            "seconds after boot, and the duration of a manually opened window, "
            "that a new BLE bond is accepted (default 180). After that the "
            "adapter stays pairable only while no LE bond is stored; 0 disables "
            "the boot window"
        ),
    )
    parser.add_argument(
        "--ble-continuous-pairing",
        action="store_true",
        help=(
            "keep the BLE adapter pairable whenever BLE is active, like the "
            "ESP32 (default off; any nearby LE central can then bond)"
        ),
    )

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
    hid.add_argument(
        "--idle-sleep-timeout",
        type=_idle_seconds,
        default=IDLE_SLEEP_TIMEOUT,
        metavar="SECONDS",
        help=(
            "pause the 0x06 streaming keep-alive after this much inactivity so "
            f"the controller can sleep (default {IDLE_SLEEP_TIMEOUT:g}, 0 disables)"
        ),
    )
    hid.add_argument(
        "--idle-poweroff-timeout",
        type=_idle_seconds,
        default=IDLE_POWEROFF_TIMEOUT,
        metavar="SECONDS",
        help=(
            "disconnect an idle controller so it powers off (default "
            f"{IDLE_POWEROFF_TIMEOUT:g}, 0 disables; must be 0 or >= "
            "--idle-sleep-timeout)"
        ),
    )
    hid.add_argument(
        "--idle-reconnect-cooldown",
        type=_idle_seconds,
        default=IDLE_RECONNECT_COOLDOWN,
        metavar="SECONDS",
        help=(
            "refuse the controller's page-in for this long after an idle power-off "
            f"so it can finish shutting down (default {IDLE_RECONNECT_COOLDOWN:g}, "
            "0 disables the guard)"
        ),
    )
    hid.add_argument(
        "--no-auto-pair",
        dest="auto_pair",
        action="store_false",
        help=(
            "do not run bluetoothctl to pair, trust and connect WMR controllers "
            "while a hand is missing"
        ),
    )

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
        "--status",
        action="store_true",
        help=(
            "show Bluetooth adapter, pairing and hidraw state, then exit "
            "(same as the 'status' argument)"
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("status", "pair"),
        metavar="{status,pair}",
        help=(
            "'status' is the same as --status; 'pair' opens the BLE pairing "
            "window on the running service"
        ),
    )
    parser.add_argument(
        "--seconds",
        type=_idle_seconds,
        default=180.0,
        metavar="SECONDS",
        help="how long 'pair' keeps the BLE pairing window open (default 180)",
    )
    parser.add_argument(
        "--close",
        action="store_true",
        help="with 'pair', close the window instead of opening it",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="control endpoint host for 'pair' (default 127.0.0.1)",
    )
    parser.add_argument(
        "--advertise-mdns",
        action="store_true",
        help=(
            "publish _wmr-bridge._tcp.local. with avahi-publish-service or "
            "systemd-resolved when available (optional; never fatal)"
        ),
    )
    wifi_group = parser.add_argument_group("Wi-Fi")
    wifi_group.add_argument(
        "--prefer-5ghz",
        action="store_true",
        help=(
            "clone the active NetworkManager connection into a 5 GHz-preferred "
            "profile and use it when a 5 GHz BSSID is visible (non-lockout)"
        ),
    )
    wifi_group.add_argument(
        "--no-reconnect",
        action="store_true",
        help="with --prefer-5ghz, save the profile without switching to it",
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
                    f"'sudo chown wmr:wmr {args.token_file}' (mode 0640)"
                )
            else:
                hint = f"check that {args.token_file} exists and is readable"
            raise SystemExit(
                f"cannot read --token-file {args.token_file}: {reason}. "
                f"This daemon is running as uid {os.getuid()}; {hint}."
            ) from exc
        try:
            mode = os.stat(args.token_file).st_mode
            if mode & stat.S_IROTH:
                LOG.warning(
                    "%s is world-readable; chmod 0640 is recommended",
                    args.token_file,
                )
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
    """IPv4 addresses this host can be reached on, wired address first.

    ``getaddrinfo(hostname)`` alone is not enough: a stock Pi OS image maps the
    hostname to ``127.0.1.1`` in /etc/hosts, so the startup line listed only
    loopback even though the service bound ``0.0.0.0``. The connected UDP
    socket asks the kernel for the source address of the default route without
    sending a packet. ``eth0`` is placed ahead of Wi-Fi so the FF16 endpoint
    document and the startup line present the preferred uplink first.
    """

    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not ipaddress.ip_address(address).is_loopback:
                addresses.add(address)
    except (OSError, ValueError):
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
            if not ipaddress.ip_address(address).is_loopback:
                addresses.add(address)
    except (OSError, ValueError):
        pass
    addresses.add("127.0.0.1")

    ordered: list[str] = []
    for name in ("eth0", "wlan0"):
        address = interface_ipv4(name)
        if address is None:
            continue
        try:
            if ipaddress.ip_address(address).is_loopback:
                continue
        except ValueError:
            continue
        addresses.add(address)
        ordered.append(address)
    ordered.extend(sorted(addresses - set(ordered)))
    return ordered


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


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _format_status(
    status: BluezStatus,
    wifi: dict[str, Any] | None = None,
    uplink: dict[str, Any] | None = None,
) -> list[str]:
    """Render the pairing state a user needs when a controller will not pair."""

    lines: list[str] = []
    if status.adapter is None:
        lines.append(
            "Bluetooth adapter: none (check 'rfkill list bluetooth' and "
            "'systemctl status bluetooth')"
        )
    else:
        adapter = status.adapter
        name = f" ({adapter.name})" if adapter.name else ""
        lines.append(
            f"Bluetooth adapter {adapter.address}{name}: powered={_yes(adapter.powered)} "
            f"pairable={_yes(adapter.pairable)} discovering={_yes(adapter.discovering)}"
        )
        if not adapter.powered:
            # btmon reports HCI Reset -> Hardware Error 0x00 for a crashed
            # Broadcom/Cypress controller. A reboot reinitialises the chip; a
            # daemon restart does not reload its firmware. Field evidence on
            # the Pi 3B+ shows `sudo reboot` clearing this state.
            lines.append(
                "Adapter is not powered. If btmon or dmesg shows an HCI Reset "
                "answered by 'Hardware Error' (0x10), the controller firmware "
                "has crashed: reboot the Pi. If the error survives the reboot, "
                "power-cycle the board. Restarting bluetooth.service does not "
                "reload the controller firmware."
            )
        elif not adapter.pairable and any(
            device.paired and device.hand is None for device in status.devices
        ):
            lines.append(
                "A stored LE bond keeps the adapter pairable=no; run "
                "'wmr-bridge pair' to open a 180 s window, or remove the "
                "phone's stale bond with 'bluetoothctl remove <MAC>'."
            )

    missing = False
    for hand, label in ((0, "LEFT"), (1, "RIGHT")):
        node = next((item for item in status.hidraw if item.hand == hand), None)
        device = next((item for item in status.devices if item.hand == hand), None)
        if device is None and node is not None:
            device = next(
                (item for item in status.devices if item.address == node.uniq.upper()),
                None,
            )
        if device is None:
            bond = "no bond known"
        else:
            bond = (
                f"{device.address} paired={_yes(device.paired)} "
                f"trusted={_yes(device.trusted)} connected={_yes(device.connected)}"
            )
        if node is None:
            missing = True
            lines.append(f"{label}: no hidraw node ({bond})")
        else:
            lines.append(f"{label}: {node.path} ({bond})")

    if missing:
        lines.append(
            "Pair a missing hand: put it in pairing mode and follow the service with "
            "'journalctl -u wmr-bridge -n 200 --no-pager'"
        )
    lines.extend(_format_uplink(uplink))
    lines.extend(_format_wifi(wifi))
    return lines


def _format_uplink(uplink: dict[str, Any] | None) -> list[str]:
    """Render the interface carrying the default route."""

    if uplink is None:
        return []
    iface = uplink.get("iface")
    if not iface:
        return ["Uplink: none (no IPv4 default route)"]
    addresses = ", ".join(uplink.get("addresses") or []) or "no address"
    return [
        f"Uplink {iface}: kind={uplink.get('kind') or 'unknown'} "
        f"addresses={addresses}"
    ]


def _format_wifi(wifi: dict[str, Any] | None) -> list[str]:
    """Render the Wi-Fi band and contention warning, if one was inspected."""

    if wifi is None:
        return []
    if wifi.get("iface") is None:
        return [f"Wi-Fi: {wifi.get('warning') or 'not available'}"]
    if wifi.get("connected"):
        band = wifi.get("band") or "unknown"
    else:
        band = "not connected"
    freq = wifi.get("freqMhz")
    frequency = f" {freq} MHz" if freq else ""
    supports = wifi.get("supports5Ghz")
    supports_text = "yes" if supports else "no" if supports is False else "unknown"
    lines = [
        f"Wi-Fi {wifi['iface']}: band={band}{frequency} supports5Ghz={supports_text}"
    ]
    if wifi.get("warning"):
        lines.append(f"Wi-Fi warning: {wifi['warning']}")
    return lines


def _print_status(
    command: str = "bluetoothctl",
    runner: Runner | None = None,
    which: Callable[[str], str | None] = shutil.which,
    sysfs_root: str = HIDRAW_ROOT,
    wifi: dict[str, Any] | None = None,
    uplink: dict[str, Any] | None = None,
) -> int:
    if which(command) is None:
        print(f"{command} not found; install bluez and try again")
        return 1
    status = collect_status(command, runner or _run_command, sysfs_root)
    print("\n".join(_format_status(status, wifi, uplink)))
    return 0 if status.adapter is not None else 1


async def _run(args: argparse.Namespace) -> int:
    _validate_idle_timeouts(args)
    token = _read_token(args)
    _require_auth_for_bind(args, token)
    wifi_info = inspect_wifi()
    uplink_info = inspect_uplink()
    if wifi_info.get("contentionWarning"):
        LOG.warning("%s", wifi_info.get("warning"))
    if uplink_info.get("iface"):
        LOG.info(
            "uplink: %s (%s) %s",
            uplink_info["iface"],
            uplink_info.get("kind"),
            ", ".join(uplink_info.get("addresses") or []) or "no address",
        )

    def network_snapshot() -> tuple[dict[str, Any], dict[str, Any]]:
        return inspect_wifi(), inspect_uplink()

    # --prefer-5ghz is a one-shot action in main(); a running daemon only
    # reports the band it observes.
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
        wifi=wifi_info,
        uplink=uplink_info,
        network=network_snapshot,
        # systemd sets STATE_DIRECTORY for StateDirectory=; use its first entry
        # so a non-default unit still finds the trigger directory.
        upgrade_dir=(os.environ.get("STATE_DIRECTORY", "").split(":")[0] or None),
    )
    server = BridgeServer(
        bridge,
        host=args.bind,
        port=args.port,
        allowed_origins=tuple(args.allow_origin),
    )
    await server.start()

    wildcard_bind = args.bind in ("0.0.0.0", "::")
    addresses = _local_addresses() if wildcard_bind else [args.bind]
    LOG.info("wmr_bridge %s listening on %s", __version__,
             ", ".join(f"ws://{address}:{server.port}/wmr" for address in addresses))
    if wildcard_bind and addresses == ["127.0.0.1"]:
        LOG.warning(
            "no non-loopback IPv4 address found yet; the service is bound to %s "
            "but LAN clients need the Pi's Wi-Fi address",
            args.bind,
        )
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

    ble = None
    if args.ble_fallback:
        LOG.warning(
            "--ble-fallback is deprecated and now a no-op: the BLE GATT "
            "peripheral is enabled by default (use --no-ble to disable it)"
        )
    if args.ble:
        from .ble_gatt import BluezGattPeripheral

        hosts = lambda: [address for address in _local_addresses() if address != "127.0.0.1"]
        ble = BluezGattPeripheral(
            bridge,
            adapter=args.ble_adapter,
            hosts=hosts,
            port=server.port,
            name=args.ble_name,
            pairing_window_s=args.ble_pairing_window,
            continuous_pairing=args.ble_continuous_pairing,
        )
        try:
            await ble.start()
        except Exception as exc:
            LOG.warning("BLE GATT disabled: %s", exc)
            ble = None
        else:
            LOG.info(
                "BLE GATT: FF10 on %s, endpoint %s",
                args.ble_adapter,
                ", ".join(hosts()) or "no LAN address yet",
            )

            async def _pairing_control(
                action: str, seconds: float | None
            ) -> dict[str, Any]:
                if action == "open":
                    await ble.open_pairing_window(seconds)
                else:
                    await ble.close_pairing_window()
                return {
                    "open": ble.pairing_window_open(),
                    "manual": ble.manual_pairing_window_open(),
                    "reason": ble.pairable_reason(),
                    "seconds": None if seconds is None else seconds,
                }

            bridge.pairing_handler = _pairing_control

    advertiser: MdnsAdvertiser | None = None
    discovery: DiscoveryResponder | None = None
    replay: ReplaySource | None = None
    manager: HidrawManager | None = None
    pairer: BluezAutoPairer | None = None
    status_task: asyncio.Task[None] | None = None
    duration_task: asyncio.Task[None] | None = None
    replay_task: asyncio.Task[None] | None = None
    stop = asyncio.Event()
    try:
        discovery = DiscoveryResponder(
            bridge_id=bridge.bridge_id,
            name=socket.gethostname(),
            port=server.port,
            hands=bridge.hand_filter,
            auth_required=token is not None,
            listen_host=args.bind,
            listen_port=DISCOVERY_PORT,
        )
        try:
            await discovery.start()
        except OSError as exc:
            LOG.info(
                "UDP discovery skipped: cannot bind %s:%d: %s",
                args.bind,
                DISCOVERY_PORT,
                exc,
            )
            discovery = None

        if args.advertise_mdns:
            advertiser = MdnsAdvertiser(
                port=server.port, hands=bridge.hand_filter, bridge_id=bridge.bridge_id
            )
            advertiser.start()

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
                idle_sleep_timeout=args.idle_sleep_timeout,
                idle_poweroff_timeout=args.idle_poweroff_timeout,
                idle_reconnect_cooldown=args.idle_reconnect_cooldown,
                reconnect_guard=set_device_blocked,
            )
            manager.start()
            LOG.info("watching hidraw for WMR controllers (hand=%s)", args.hand)
            LOG.info(
                "controller idle policy: sleep after %g s, power off after %g s, "
                "reconnect cooldown %g s (0 disables a stage)",
                args.idle_sleep_timeout,
                args.idle_poweroff_timeout,
                args.idle_reconnect_cooldown,
            )
            if args.auto_pair:
                pairer = BluezAutoPairer(
                    bridge,
                    allowed_hands=allowed_hands,
                    pairing_window_open=(ble.pairing_window_open if ble else None),
                    on_scan_end=(ble.request_agent_reclaim if ble else None),
                )
                pairer.start()
                LOG.info(
                    "automatic pairing: scanning for WMR controllers in pairing mode "
                    "(disable with --no-auto-pair)"
                )
            else:
                LOG.info("automatic pairing: disabled (--no-auto-pair)")

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
                    f"{item['hand'].lower()}={'up' if item['connected'] else 'down'}"
                    f"/{item['reports']}({item['delivered']})"
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
        if args.duration:
            duration_task = asyncio.create_task(
                _stop_after(stop, args.duration), name="duration"
            )
        if replay is not None and args.replay_once:
            replay_task = asyncio.create_task(
                _stop_after_replay(replay, stop), name="replay-stop"
            )

        await stop.wait()
    finally:
        stop.set()
        if status_task is not None:
            status_task.cancel()
        if duration_task is not None:
            duration_task.cancel()
        if replay_task is not None:
            replay_task.cancel()
        if replay is not None:
            await replay.close()
        if pairer is not None:
            await pairer.close()
        if manager is not None:
            await manager.close()
        if advertiser is not None:
            advertiser.close()
        if discovery is not None:
            await discovery.close()
        if ble is not None:
            await ble.close()
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


def _parse_cli_args(argv: list[str] | None = None, env_args: str | None = None) -> argparse.Namespace:
    if argv is None:
        argv = sys.argv[1:]
    if env_args is None:
        env_args = os.environ.get("WMR_BRIDGE_ARGS", "")
    extra = env_args.strip()
    if extra:
        import shlex

        # Append WMR_BRIDGE_ARGS unless already present in argv (e.g. via systemd $WMR_BRIDGE_ARGS).
        extra_tokens = shlex.split(extra)
        if not (len(argv) >= len(extra_tokens) and argv[-len(extra_tokens) :] == extra_tokens):
            argv = [*argv, *extra_tokens]
    return _build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_cli_args(argv, env_args=None if argv is None else "")
    if args.status or args.command == "status":
        return _print_status(wifi=inspect_wifi(), uplink=inspect_uplink())
    if args.command == "pair":
        return _pair_command(args)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        filename=args.log_file,
    )
    if args.list:
        return _print_devices()
    if args.prefer_5ghz:
        # One-shot action so systemd (ExecStartPre) and the installer can
        # stage or apply the profile without starting a second daemon.
        preference = prefer_5ghz(reconnect=not args.no_reconnect)
        if preference.get("profile"):
            LOG.info(
                "5 GHz preference: profile %s%s",
                preference["profile"],
                " (active)" if preference.get("reconnected") else "",
            )
        if preference.get("warning"):
            LOG.warning("5 GHz preference: %s", preference.get("warning"))
        return 0
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
