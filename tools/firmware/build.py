#!/usr/bin/env python3
"""Build, package and (optionally) flash WMR_Bridge with one command.

The firmware is not a standalone ESP-IDF project: it builds inside the BTstack
ESP32 port, with one BTstack file replaced. Doing that by hand is the main
reason flashing is awkward. This script does the same steps reproducibly:

  1. clone BTstack at the pinned commit into a managed work directory
     (default: <repo>/.fw-build/btstack),
  2. copy this repository to <btstack>/wmr_bridge,
  3. copy integration/main-CMakeLists.txt over port/esp32/main/CMakeLists.txt,
  4. run `idf.py build` with EXAMPLE=wmr_bridge (and the variant's
     EXTRA_CFLAGS for --variant left|right),
  5. copy the images and a merged factory image to <repo>/dist, together with
     the ESP Web Tools manifest and the browser flasher page,
  6. optionally run `idf.py flash` against a serial port.

`--variant both|left|right` builds the default both-hands image or a
single-hand image for two bridges (or single-hand testing on one board). The
flags are compile-time EXTRA_CFLAGS; src/wmr_config.h is never edited. `both`
keeps today's names and build directory.

ESP-IDF must be on PATH. Load it first, or pass `--idf-export` on Linux/macOS:

  Windows PowerShell:
    . C:\\Espressif\\tools\\Microsoft.v6.1.PowerShell_profile.ps1
    python tools\\firmware\\build.py --flash COM5

  Linux/macOS:
    python3 tools/firmware/build.py --idf-export ~/esp/esp-idf/export.sh \\
        --flash /dev/ttyUSB0

See docs/FLASHING.md for the framework, the release procedure and limits.
Standard library only.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import fwpackage  # noqa: E402

BTSTACK_URL = "https://github.com/bluekitchen/btstack.git"
BTSTACK_COMMIT = "e38553977a25fb0b55b383c72c289be0975f422c"
BTSTACK_DIR = "btstack"
APP_DIR = "wmr_bridge"
BUILD_DIR = "build-wmr-bridge"
DEFAULT_WORK = REPO_ROOT / ".fw-build"
DEFAULT_OUT = REPO_ROOT / "dist"

# One build directory per variant. EXTRA_CFLAGS is read at configure time, so
# sharing a directory between variants could silently reuse the previous
# defines. `both` keeps the original directory name.
VARIANT_BUILD_DIRS = {
    "both": BUILD_DIR,
    "left": BUILD_DIR + "-left",
    "right": BUILD_DIR + "-right",
}

COPY_IGNORE_NAMES = {
    ".git",
    ".fw-build",
    ".paperclip",
    ".paperclip-inbound",
    ".paperclip-repositories",
    ".claude",
    ".tmp",
    ".vscode",
    "__pycache__",
    "captures",
    "dist",
    "node_modules",
}


def log(message):
    print("[fw] " + message, flush=True)


def fail(message):
    print("[fw] ERROR: " + message, file=sys.stderr, flush=True)
    raise SystemExit(1)


def run(argv, cwd=None, env=None, capture=False):
    shown = " ".join(str(part) for part in argv)
    log("$ " + shown if cwd is None else "$ (cd %s && %s)" % (cwd, shown))
    try:
        result = subprocess.run(
            [str(part) for part in argv],
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        fail("command not found: %s" % argv[0])
    if result.returncode != 0:
        if capture and result.stdout:
            print(result.stdout, file=sys.stderr)
        fail("command failed with exit code %d: %s" % (result.returncode, shown))
    return result.stdout.strip() if capture else ""


def git(repo, *args, capture=True):
    return run(["git", "-C", str(repo)] + list(args), capture=capture)


def ensure_btstack(work):
    btstack = work / BTSTACK_DIR
    if not (btstack / ".git").is_dir():
        if btstack.exists():
            log("removing incomplete BTstack directory %s" % btstack)
            shutil.rmtree(btstack)
        work.mkdir(parents=True, exist_ok=True)
        log("cloning BTstack (one time) into %s" % btstack)
        run(["git", "clone", "--filter=blob:none", BTSTACK_URL, str(btstack)])

    def has_commit():
        result = subprocess.run(
            ["git", "-C", str(btstack), "cat-file", "-e",
             BTSTACK_COMMIT + "^{commit}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    if not has_commit():
        log("fetching pinned BTstack commit %s" % BTSTACK_COMMIT)
        run(["git", "-C", str(btstack), "fetch", "--depth", "1", "origin", BTSTACK_COMMIT])
    run(["git", "-C", str(btstack), "checkout", "-f", "--detach", BTSTACK_COMMIT])
    head = git(btstack, "rev-parse", "HEAD")
    if head != BTSTACK_COMMIT:
        fail("BTstack is at %s, expected %s" % (head, BTSTACK_COMMIT))
    return btstack


def _copy_ignore(repo_root, work, out):
    skip = {Path(work).resolve(), Path(out).resolve()}

    def ignore(directory, names):
        ignored = set()
        directory = Path(directory).resolve()
        for name in names:
            path = (directory / name).resolve()
            if name in COPY_IGNORE_NAMES or name.startswith("build"):
                ignored.add(name)
            elif path in skip:
                ignored.add(name)
        return ignored

    return ignore


def sync_source(btstack, work, out):
    """Copy the current working tree into <btstack>/wmr_bridge.

    Copying (rather than symlinking) works on Windows without developer mode
    and avoids stale files from an earlier checkout: the destination is
    removed first.
    """
    destination = btstack / APP_DIR
    if destination.exists():
        shutil.rmtree(destination)
    log("copying the working tree to %s" % destination)
    shutil.copytree(
        REPO_ROOT,
        destination,
        ignore=_copy_ignore(REPO_ROOT, work, out),
        copy_function=shutil.copy2,
    )

    source_cmake = REPO_ROOT / "integration" / "main-CMakeLists.txt"
    target_cmake = btstack / "port" / "esp32" / "main" / "CMakeLists.txt"
    if not source_cmake.is_file():
        fail("missing %s" % source_cmake)
    shutil.copyfile(source_cmake, target_cmake)
    log("replaced port/esp32/main/CMakeLists.txt with integration/main-CMakeLists.txt")
    return destination


def idf_version(port_dir, env):
    try:
        output = run(["idf.py", "--version"], cwd=port_dir, env=env, capture=True)
    except SystemExit:
        return "unknown"
    for line in output.splitlines():
        if "ESP-IDF" in line:
            return line.strip()
    return output.splitlines()[-1].strip() if output else "unknown"


def repo_state(repo):
    commit = git(repo, "rev-parse", "--short", "HEAD")
    dirty = bool(git(repo, "status", "--porcelain"))
    return commit, dirty


def pick_version(repo, override):
    if override:
        return fwpackage.normalise_version(override)
    tag = os.environ.get("GITHUB_REF_NAME", "")
    if tag.startswith("v"):
        return fwpackage.normalise_version(tag)
    try:
        described = git(repo, "describe", "--tags", "--always", "--dirty")
    except SystemExit:
        described = ""
    return fwpackage.normalise_version(described)


# esp_app_desc_t.version is 32 bytes including the terminator; FF16 and the boot line
# read it as a C string.
MAX_PROJECT_VERSION_BYTES = 31


def version_fits_app_desc(version):
    return len(version.encode("utf-8")) <= MAX_PROJECT_VERSION_BYTES


def build_argv(build_dir, version):
    """`idf.py build` arguments, including the version the firmware reports.

    PROJECT_VER becomes esp_app_get_description()->version, so the value the
    bridge serves on FF16 and prints on the console is the same value in
    manifest.json and build-info.json (see docs/FLASHING.md).
    """
    return [
        "idf.py",
        "-B",
        str(build_dir),
        "-DPROJECT_VER=%s" % version,
        "build",
    ]


def build_dir_name(variant="both"):
    """Build-directory name for *variant*; `both` keeps the original name."""
    if variant not in fwpackage.VARIANTS:
        raise SystemExit("unknown variant %r; expected one of: %s"
                         % (variant, ", ".join(fwpackage.VARIANTS)))
    return VARIANT_BUILD_DIRS[variant]


def build_dir_for(work, variant="both"):
    return Path(work) / build_dir_name(variant)


def build_env(variant="both", base_env=None):
    """Environment for the build, with the variant's compile-time defines.

    The defines go through EXTRA_CFLAGS, the ESP-IDF mechanism the run sheets
    already use; src/wmr_config.h is never edited. `both` adds nothing and
    leaves any existing EXTRA_CFLAGS alone, so the default build is unchanged.
    The value is appended so it wins over a conflicting define already in the
    environment.
    """
    env = dict(os.environ if base_env is None else base_env)
    defines = fwpackage.variant_defines(variant)
    if defines:
        existing = env.get("EXTRA_CFLAGS", "").strip()
        env["EXTRA_CFLAGS"] = " ".join([existing] + defines).strip()
    return env


def package_outputs(artifacts, out, version, repo_commit, repo_dirty,
                    btstack_commit, idf_ver, port_dir, build_dir, env,
                    variant="both"):
    names = fwpackage.artifact_names(variant)
    out.mkdir(parents=True, exist_ok=True)
    copies = (
        (artifacts["app_bin"], names["app"]),
        (artifacts["bootloader_bin"], names["bootloader"]),
        (artifacts["partition_bin"], names["partition_table"]),
    )
    for source, name in copies:
        shutil.copyfile(source, out / name)
        log("packaged %s" % (out / name))

    factory = out / names["factory"]
    argv = fwpackage.merge_bin_argv("idf.py", build_dir, factory)
    run(argv, cwd=port_dir, env=env)
    if not factory.is_file():
        fail("idf.py merge-bin did not produce %s" % factory)

    manifest = fwpackage.make_manifest(version, chip=artifacts["chip"],
                                       variant=variant)
    fwpackage.write_json(out / names["manifest"], manifest)
    log("wrote %s (version %s, variant %s)"
        % (out / names["manifest"], version, variant))

    info = fwpackage.make_build_info(
        version=version,
        artifacts=artifacts,
        repo_commit=repo_commit,
        repo_dirty=repo_dirty,
        btstack_commit=btstack_commit,
        idf_version=idf_ver,
        built_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        variant=variant,
    )
    fwpackage.write_json(out / names["build_info"], info)

    flasher = REPO_ROOT / "docs" / "flasher" / "index.html"
    if flasher.is_file():
        shutil.copyfile(flasher, out / "index.html")
        log("copied the browser flasher page to %s" % (out / "index.html"))
    else:
        log("warning: %s is missing; no browser flasher page in the output" % flasher)


def maybe_reexec_with_idf(export_path):
    """Source an ESP-IDF export script, then run this script under its Python."""
    if not export_path or os.environ.get("WMR_FW_IDF_EXPORTED") == "1":
        return
    if os.name == "nt":
        fail("--idf-export is POSIX-only. In PowerShell, dot-source the ESP-IDF "
             "profile first (see docs/FLASHING.md).")
    export = Path(export_path).expanduser()
    if not export.is_file():
        fail("IDF export script not found: %s" % export)

    forwarded = []
    skip_next = False
    for arg in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg == "--idf-export":
            skip_next = True
            continue
        if arg.startswith("--idf-export="):
            continue
        forwarded.append(arg)

    log("sourcing %s" % export)
    child_env = dict(os.environ)
    child_env["WMR_FW_IDF_EXPORTED"] = "1"
    argv = [
        "bash",
        "-c",
        'set -e; . "$1" >/dev/null; shift; exec "$@"',
        "wmr-fw",
        str(export),
        "python3",
        str(Path(__file__).resolve()),
    ] + forwarded
    os.execvpe("bash", argv, child_env)


def main():
    parser = argparse.ArgumentParser(
        description="Build, package and optionally flash WMR_Bridge.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", help="version string for the manifest "
                        "(default: git describe, or GITHUB_REF_NAME on a tag)")
    parser.add_argument("--variant", choices=fwpackage.VARIANTS, default="both",
                        help="firmware variant: both hands (default), or a "
                             "single-hand image for two bridges / one-board "
                             "single-hand testing")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="artifact directory (default: %(default)s)")
    parser.add_argument("--work", default=str(DEFAULT_WORK),
                        help="managed BTstack/build directory (default: %(default)s)")
    parser.add_argument("--flash", metavar="PORT",
                        help="after building, flash this serial port, e.g. COM5 "
                             "or /dev/ttyUSB0")
    parser.add_argument("--idf-export", metavar="SCRIPT",
                        help="POSIX path to ESP-IDF export.sh to source first "
                             "(Windows: dot-source the PowerShell profile instead)")
    parser.add_argument("--clean", action="store_true",
                        help="delete the managed work directory first "
                             "(forces a fresh BTstack clone and build)")
    args = parser.parse_args()

    maybe_reexec_with_idf(args.idf_export)

    if not shutil.which("idf.py"):
        fail("idf.py is not on PATH. Load ESP-IDF first:\n"
             "  Windows:  . C:\\Espressif\\tools\\Microsoft.v6.1.PowerShell_profile.ps1\n"
             "  POSIX:    python3 tools/firmware/build.py --idf-export "
             "<esp-idf>/export.sh")

    work = Path(args.work).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    if args.clean and work.exists():
        log("removing %s" % work)
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    version = pick_version(REPO_ROOT, args.version)
    if not version_fits_app_desc(version):
        fail("version %r is longer than %d bytes; ESP-IDF's application descriptor "
             "(and FF16) holds at most that many bytes" %
             (version, MAX_PROJECT_VERSION_BYTES))
    repo_commit, repo_dirty = repo_state(REPO_ROOT)
    log("building WMR_Bridge %s (%s variant; source %s%s)" % (
        version, args.variant, repo_commit, "+dirty" if repo_dirty else ""))

    btstack = ensure_btstack(work)
    sync_source(btstack, work, out)

    port_dir = btstack / "port" / "esp32"
    build_dir = build_dir_for(work, args.variant)
    env = build_env(args.variant, os.environ)
    env["EXAMPLE"] = "wmr_bridge"
    defines = fwpackage.variant_defines(args.variant)
    if defines:
        log("variant flags: %s" % " ".join(defines))
    else:
        log("variant flags: none (source defaults: hands=0, name=%s)"
            % fwpackage.BLE_NAMES["both"])
    idf_ver = idf_version(port_dir, env)
    log("using %s" % idf_ver)
    run(build_argv(build_dir, version), cwd=port_dir, env=env)

    artifacts = fwpackage.collect_artifacts(build_dir)
    package_outputs(
        artifacts=artifacts,
        out=out,
        version=version,
        repo_commit=repo_commit,
        repo_dirty=repo_dirty,
        btstack_commit=BTSTACK_COMMIT,
        idf_ver=idf_ver,
        port_dir=port_dir,
        build_dir=build_dir,
        env=env,
        variant=args.variant,
    )

    if args.flash:
        log("flashing %s (%s variant)" % (args.flash, args.variant))
        run(["idf.py", "-B", str(build_dir), "-p", args.flash, "flash"],
            cwd=port_dir, env=env)

    names = fwpackage.artifact_names(args.variant)
    log("done. Artifacts in %s" % out)
    for name in (names["factory"], names["manifest"], names["build_info"],
                 "index.html"):
        path = out / name
        if path.is_file():
            log("  %-36s %8.1f KiB" % (name, path.stat().st_size / 1024.0))
    log("browser flasher: serve %s over HTTP (python3 -m http.server -d %s)" % (out, out))


if __name__ == "__main__":
    main()
