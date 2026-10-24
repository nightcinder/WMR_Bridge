"""Optional mDNS/DNS-SD advertisement for the bridge.

Two host mechanisms are supported, both without third-party Python packages:

* ``avahi-publish-service`` (Avahi) publishes ``_wmr-bridge._tcp.local.``
  directly for as long as the daemon runs;
* ``resolvectl`` means systemd-resolved is available, so a ``.dnssd`` service
  file is registered under ``/run/systemd/dnssd`` (systemd-resolved publishes
  it and ``resolvectl service`` can browse it).

If neither tool is installed the daemon logs one informational line and keeps
serving; advertisement is a discovery convenience, never a startup requirement.
"""

from __future__ import annotations

import logging
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import __version__

LOG = logging.getLogger("wmr_bridge.mdns")

SERVICE_TYPE = "_wmr-bridge._tcp"
SERVICE_FILE = "wmr-bridge.dnssd"
DNSSD_DIR = Path("/run/systemd/dnssd")


class MdnsAdvertiser:
    """Best-effort DNS-SD advertisement; ``start()`` never raises."""

    def __init__(
        self,
        *,
        port: int,
        hands: list[str],
        hostname: str | None = None,
        version: str = __version__,
        path: str = "/wmr",
        dnssd_dir: Path = DNSSD_DIR,
        which: Callable[[str], str | None] = shutil.which,
        popen: Callable[..., Any] = subprocess.Popen,
        log: logging.Logger = LOG,
    ) -> None:
        self.port = port
        self.hands = list(hands)
        self.name = hostname or socket.gethostname()
        self.version = version
        self.path = path
        self.dnssd_dir = Path(dnssd_dir)
        self._which = which
        self._popen = popen
        self._log = log
        self._process: Any = None
        self._service_file: Path | None = None

    @property
    def service_file(self) -> Path:
        return self.dnssd_dir / SERVICE_FILE

    def _txt(self) -> list[str]:
        return [
            f"version={self.version}",
            f"hands={','.join(self.hands)}",
            f"path={self.path}",
        ]

    def start(self) -> bool:
        """Publish the service; return True when a mechanism was started."""

        if self._process is not None or self._service_file is not None:
            return True
        avahi = self._which("avahi-publish-service")
        if avahi:
            command = [
                avahi,
                "--no-reverse",
                self.name,
                SERVICE_TYPE,
                str(self.port),
                *self._txt(),
            ]
            try:
                self._process = self._popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                self._log.info("mDNS advertisement skipped: cannot start avahi: %s", exc)
                return False
            self._log.info(
                "advertising %s.local. on port %d via avahi-publish-service",
                SERVICE_TYPE,
                self.port,
            )
            return True

        if self._which("resolvectl"):
            return self._start_resolved()

        self._log.info(
            "mDNS advertisement requested, but avahi-publish-service and "
            "resolvectl are both absent; continuing without advertisement"
        )
        return False

    def _start_resolved(self) -> bool:
        """Register a systemd-resolved ``.dnssd`` service file."""

        if not self.dnssd_dir.is_dir():
            self._log.info(
                "mDNS advertisement requested via resolvectl, but %s does not "
                "exist (is systemd-resolved running as root?); continuing "
                "without advertisement",
                self.dnssd_dir,
            )
            return False
        body = "\n".join(
            [
                "[Service]",
                f"Name={self.name}",
                f"Type={SERVICE_TYPE}",
                f"Port={self.port}",
                "TxtText=" + " ".join(self._txt()),
                "",
            ]
        )
        try:
            self.service_file.write_text(body, encoding="utf-8")
        except OSError as exc:
            self._log.info(
                "mDNS advertisement requested via resolvectl, but %s is not "
                "writable (%s); continuing without advertisement",
                self.service_file,
                exc,
            )
            return False
        self._service_file = self.service_file
        self._log.info(
            "advertising %s.local. on port %d via systemd-resolved (%s)",
            SERVICE_TYPE,
            self.port,
            self._service_file,
        )
        return True

    def close(self) -> None:
        """Stop advertising; safe to call more than once."""

        process, self._process = self._process, None
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
            except OSError:
                pass
        service_file, self._service_file = self._service_file, None
        if service_file is not None:
            try:
                service_file.unlink()
            except OSError:
                pass
