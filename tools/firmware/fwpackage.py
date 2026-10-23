#!/usr/bin/env python3
"""Turn an ESP-IDF build directory into release artifacts.

This module is deliberately free of ESP-IDF, network and git calls. It only
reads the files `idf.py` already wrote, so the host test suite can exercise it
without a toolchain:

  * `project_description.json` - the target chip and application binary name
  * `flasher_args.json`        - the offset -> file map and flash settings that
                                 `idf.py flash` itself uses

The output is the set of files the browser flasher and a release need: the
three separate images, one merged factory image at offset 0, an ESP Web Tools
manifest and a build-info record.

Standard library only.
"""

import json
import os
import re
from pathlib import Path

MANIFEST_NAME = "manifest.json"
BUILD_INFO_NAME = "build-info.json"
FACTORY_NAME = "wmr-bridge-factory.bin"
APP_NAME = "wmr-bridge-app.bin"
BOOTLOADER_NAME = "wmr-bridge-bootloader.bin"
PARTITION_TABLE_NAME = "wmr-bridge-partition-table.bin"

DEFAULT_CHIP = "esp32"
# ESP Web Tools spells the chip family this way.
CHIP_FAMILIES = {
    "esp32": "ESP32",
    "esp32s2": "ESP32-S2",
    "esp32s3": "ESP32-S3",
    "esp32c3": "ESP32-C3",
    "esp32c6": "ESP32-C6",
    "esp32h2": "ESP32-H2",
}


class PackagingError(RuntimeError):
    """The build directory does not contain a usable set of images."""


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise PackagingError("cannot read %s: %s" % (path, exc))


def _resolve(build_dir, value):
    path = Path(value)
    if not path.is_absolute():
        path = Path(build_dir) / path
    return path.resolve()


def collect_artifacts(build_dir):
    """Describe the images in *build_dir*.

    Returns a dict with:
      chip              target chip, e.g. "esp32"
      app_bin           path to the application image
      app_offset        flash offset of the application image, e.g. "0x10000"
      flash_files       {"0x1000": Path, ...} in ascending offset order
      flash_settings    {"flash_mode": "dio", "flash_freq": "40m", ...}
      bootloader_bin    path to the bootloader image
      partition_bin     path to the partition table
    """
    build_dir = Path(build_dir).resolve()
    description_path = build_dir / "project_description.json"
    if not description_path.is_file():
        raise PackagingError(
            "no project_description.json in %s; run `idf.py build` first" % build_dir
        )

    description = _read_json(description_path)
    chip = (description.get("target") or DEFAULT_CHIP).lower()
    app_name = description.get("app_bin")
    if not app_name:
        raise PackagingError("project_description.json has no app_bin entry")
    app_bin = _resolve(build_dir, app_name)
    if not app_bin.is_file():
        raise PackagingError("application image is missing: %s" % app_bin)

    flasher_path = build_dir / "flasher_args.json"
    if flasher_path.is_file():
        flasher = _read_json(flasher_path)
        flash_files = {
            str(offset): _resolve(build_dir, name)
            for offset, name in (flasher.get("flash_files") or {}).items()
        }
        flash_settings = dict(flasher.get("flash_settings") or {})
    else:
        # ESP-IDF has written flasher_args.json for a long time. Keep a
        # conservative fallback for the single-app-large layout this project
        # uses, so a future IDF change produces a clear error, not silence.
        flash_files = {
            "0x1000": _resolve(build_dir, "bootloader/bootloader.bin"),
            "0x8000": _resolve(build_dir, "partition_table/partition-table.bin"),
            "0x10000": app_bin,
        }
        flash_settings = {}

    if not flash_files:
        raise PackagingError("flasher_args.json lists no flash files")

    missing = [str(path) for path in flash_files.values() if not path.is_file()]
    if missing:
        raise PackagingError("missing flash image(s): %s" % ", ".join(missing))

    def offset_key(offset):
        try:
            return int(offset, 0)
        except ValueError:
            raise PackagingError("not a flash offset: %r" % (offset,))

    ordered = sorted(flash_files.items(), key=lambda item: offset_key(item[0]))
    flash_files = dict(ordered)

    app_offset = None
    for offset, path in flash_files.items():
        if path == app_bin:
            app_offset = offset
            break
    if app_offset is None:
        raise PackagingError("application image %s is not in flasher_args.json" % app_bin)

    bootloader_bin = _image_named(flash_files, "bootloader") or flash_files.get("0x1000")
    partition_bin = _image_named(flash_files, "partition-table") or flash_files.get("0x8000")
    if bootloader_bin is None or partition_bin is None:
        raise PackagingError("could not identify bootloader and partition table images")

    return {
        "chip": chip,
        "app_bin": app_bin,
        "app_offset": app_offset,
        "flash_files": flash_files,
        "flash_settings": flash_settings,
        "bootloader_bin": bootloader_bin,
        "partition_bin": partition_bin,
    }


def _image_named(flash_files, needle):
    for path in flash_files.values():
        if needle in path.name:
            return path
    return None


def merge_bin_argv(idf_py, build_dir, output):
    """Arguments for `idf.py merge-bin`.

    IDF reads the build's own `flash_args` file, so the merge uses exactly the
    flash mode, size and offsets that `idf.py flash` would write. This avoids
    depending on the esptool CLI spelling, which changed between v4 and v5.
    """
    return [
        str(idf_py),
        "-B",
        str(build_dir),
        "merge-bin",
        "-o",
        str(output),
    ]


def chip_family(chip):
    return CHIP_FAMILIES.get(chip.lower(), chip.upper())


def make_manifest(version, chip=DEFAULT_CHIP, factory_name=FACTORY_NAME):
    """ESP Web Tools manifest for a single merged image at offset 0."""
    return {
        "name": "WMR Bridge",
        "version": str(version),
        "new_install_prompt_erase": True,
        "builds": [
            {
                "chipFamily": chip_family(chip),
                "parts": [{"path": factory_name, "offset": 0}],
            }
        ],
    }


def make_build_info(
    version,
    artifacts,
    repo_commit,
    repo_dirty,
    btstack_commit,
    idf_version,
    built_at,
):
    return {
        "name": "WMR Bridge",
        "version": str(version),
        "chip": artifacts["chip"],
        "commit": repo_commit,
        "dirty": bool(repo_dirty),
        "btstackCommit": btstack_commit,
        "idfVersion": idf_version,
        "builtAt": built_at,
        "appOffset": artifacts["app_offset"],
        "flashSettings": artifacts["flash_settings"],
    }


VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)+)")


def normalise_version(raw):
    """Make a git description safe to show in the manifest.

    `v0.2.0` becomes `0.2.0`; anything that is not a dotted version (a bare
    commit hash, for instance) becomes `0.0.0+<value>`.
    """
    value = (raw or "").strip()
    if not value:
        return "0.0.0+unknown"
    if VERSION_RE.match(value):
        return value[1:] if value.startswith("v") else value
    return "0.0.0+" + value


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path
