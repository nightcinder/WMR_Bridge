#!/usr/bin/env python3
"""Offline tests for tools/firmware (build packaging and flasher manifest).

No ESP-IDF, no serial port, no network. These check our handling of the files
idf.py writes, not that a particular firmware builds. Run:

    python3 tests/test_firmware_package.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "firmware"))

import fwpackage as fp  # noqa: E402
import build  # noqa: E402

checks = 0
failures = 0


def check(cond, what):
    global checks, failures
    checks += 1
    if not cond:
        failures += 1
        print("  FAIL:", what)


def write(path, content=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


def make_build(root, with_flasher_args=True):
    app = os.path.join(root, "esp32-example.bin")
    write(app, b"app")
    write(os.path.join(root, "bootloader", "bootloader.bin"), b"boot")
    write(os.path.join(root, "partition_table", "partition-table.bin"), b"part")
    with open(os.path.join(root, "project_description.json"), "w") as fh:
        json.dump({"target": "esp32", "app_bin": "esp32-example.bin"}, fh)
    if with_flasher_args:
        with open(os.path.join(root, "flasher_args.json"), "w") as fh:
            json.dump(
                {
                    "flash_settings": {
                        "flash_mode": "dio",
                        "flash_freq": "40m",
                        "flash_size": "2MB",
                    },
                    # Deliberately unordered: packaging must sort by offset.
                    "flash_files": {
                        "0x10000": "esp32-example.bin",
                        "0x1000": "bootloader/bootloader.bin",
                        "0x8000": "partition_table/partition-table.bin",
                    },
                },
                fh,
            )
    return app


def test_collect_with_flasher_args():
    with tempfile.TemporaryDirectory() as root:
        app = make_build(root)
        art = fp.collect_artifacts(root)
        check(art["chip"] == "esp32", "chip is esp32")
        check(str(art["app_bin"]) == os.path.realpath(app), "app binary resolved")
        check(art["app_offset"] == "0x10000", "app offset is 0x10000")
        check(
            list(art["flash_files"]) == ["0x1000", "0x8000", "0x10000"],
            "flash files are ordered by numeric offset",
        )
        check(
            art["bootloader_bin"].name == "bootloader.bin",
            "bootloader identified",
        )
        check(
            art["partition_bin"].name == "partition-table.bin",
            "partition table identified",
        )
        check(
            art["flash_settings"]["flash_mode"] == "dio",
            "flash settings preserved",
        )
        check(set(art["flash_files"].values()) == {
            art["bootloader_bin"], art["partition_bin"], art["app_bin"]
        }, "all three images are described")


def test_collect_fallback_without_flasher_args():
    with tempfile.TemporaryDirectory() as root:
        make_build(root, with_flasher_args=False)
        art = fp.collect_artifacts(root)
        check(art["app_offset"] == "0x10000", "fallback app offset")
        check(
            list(art["flash_files"]) == ["0x1000", "0x8000", "0x10000"],
            "fallback offsets",
        )
        check(art["flash_settings"] == {}, "fallback has no flash settings")


def test_collect_missing_app_fails():
    with tempfile.TemporaryDirectory() as root:
        with open(os.path.join(root, "project_description.json"), "w") as fh:
            json.dump({"target": "esp32", "app_bin": "missing.bin"}, fh)
        try:
            fp.collect_artifacts(root)
        except fp.PackagingError as exc:
            check("missing.bin" in str(exc), "error names the missing image")
        else:
            check(False, "missing app must raise PackagingError")


def test_merge_argv():
    argv = fp.merge_bin_argv("idf.py", "/build", "/out/factory.bin")
    check(argv == ["idf.py", "-B", "/build", "merge-bin", "-o", "/out/factory.bin"],
          "idf.py merge-bin command uses the build's own flash args")


def test_manifest():
    manifest = fp.make_manifest("1.2.3")
    check(manifest["name"] == "WMR Bridge", "manifest name")
    check(manifest["version"] == "1.2.3", "manifest version")
    check(manifest["new_install_prompt_erase"] is True, "flasher offers erase")
    build = manifest["builds"][0]
    check(build["chipFamily"] == "ESP32", "ESP Web Tools chip family")
    check(build["parts"] == [{"path": "wmr-bridge-factory.bin", "offset": 0}],
          "one merged image at offset 0")
    check(fp.make_manifest("x", chip="esp32s3")["builds"][0]["chipFamily"] == "ESP32-S3",
          "S3 family mapping")


def test_normalise_version():
    cases = (
        ("v0.2.0", "0.2.0"),
        ("0.2.0", "0.2.0"),
        ("v0.2.0-3-gabc1234", "0.2.0-3-gabc1234"),
        ("8f4a377", "0.0.0+8f4a377"),
        ("abc1234", "0.0.0+abc1234"),
        ("", "0.0.0+unknown"),
        ("  v1.0.0  ", "1.0.0"),
    )
    for raw, expected in cases:
        got = fp.normalise_version(raw)
        check(got == expected, "normalise_version(%r) == %r (got %r)" % (raw, expected, got))


def test_build_info_and_json_roundtrip():
    with tempfile.TemporaryDirectory() as root:
        make_build(root)
        art = fp.collect_artifacts(root)
        info = fp.make_build_info(
            "1.2.3", art, "abc1234", False, "e38553977a25fb0b55b383c72c289be0975f422c",
            "ESP-IDF v6.1", "2026-10-23T00:00:00Z",
        )
        check(info["version"] == "1.2.3", "build-info version")
        check(info["commit"] == "abc1234", "build-info commit")
        check(info["chip"] == "esp32", "build-info chip")
        check(info["appOffset"] == "0x10000", "build-info app offset")
        path = fp.write_json(os.path.join(root, "nested", "build-info.json"), info)
        with open(path) as fh:
            loaded = json.load(fh)
        check(loaded == info, "build-info survives JSON round trip")
        with open(path, "rb") as fh:
            check(fh.read().endswith(b"\n"), "written JSON ends with a newline")


def test_sync_source_into_btstack_layout():
    with tempfile.TemporaryDirectory() as root:
        root = os.path.realpath(root)
        btstack = os.path.join(root, "btstack")
        os.makedirs(os.path.join(btstack, "port", "esp32", "main"))
        os.makedirs(os.path.join(btstack, ".git"))
        cmake = os.path.join(btstack, "port", "esp32", "main", "CMakeLists.txt")
        write(cmake, b"stock BTstack file\n")

        build.sync_source(Path(btstack), Path(root) / ".fw-build", Path(root) / "dist")

        app = os.path.join(btstack, "wmr_bridge")
        check(os.path.isfile(os.path.join(app, "src", "wmr_bridge_main.c")),
              "firmware sources copied into <btstack>/wmr_bridge")
        check(os.path.isfile(os.path.join(app, "integration", "main-CMakeLists.txt")),
              "integration file copied with the tree")
        check(not os.path.exists(os.path.join(app, ".git")),
              "the nested .git directory is not copied")
        check(not os.path.exists(os.path.join(app, ".paperclip-repositories")),
              "the companion repository checkout is not copied")
        with open(cmake, "rb") as fh:
            installed = fh.read()
        with open(os.path.join(app, "integration", "main-CMakeLists.txt"), "rb") as fh:
            expected = fh.read()
        check(installed == expected,
              "port/esp32/main/CMakeLists.txt is replaced with the integration file")


def test_flasher_page_contract():
    path = os.path.join(HERE, "..", "docs", "flasher", "index.html")
    with open(path, encoding="utf-8") as fh:
        page = fh.read()
    check('slot="activate"' in page,
          "install button uses the ESP Web Tools 10 activate slot")
    check('slot="install-button"' not in page,
          "the non-existent install-button slot is not used")
    check('manifest="./manifest.json"' in page,
          "flasher loads the manifest next to it")
    check("esp-web-tools@10.4.0" in page, "ESP Web Tools is version-pinned")
    check('integrity="sha384-' in page, "the CDN module has an SRI hash")
    check('crossorigin="anonymous"' in page, "SRI fetch is CORS-enabled")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    print("\n%d checks, %d failures" % (checks, failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
