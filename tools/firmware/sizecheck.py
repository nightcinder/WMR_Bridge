#!/usr/bin/env python3
"""Static DRAM and flash budget for a WMR_Bridge firmware build.

The ESP32 image has little spare DRAM: the PSVR2 frame size and queue capacity
were chosen to fit, and a quiet change to either can overflow `dram0_0_seg` only
at link time on a full build. This module reads the `idf.py size --format json2`
report plus the packaged application image size and compares both with the
budgets in `size-budget.json`.

Deliberately free of ESP-IDF, network and git calls: the host test suite feeds
it a synthetic report, including a simulated 1 KiB overrun, without a toolchain.
Standard library only.
"""

import json
from pathlib import Path

BUDGET_NAME = "size-budget.json"
# idf.py size --format json2 names the static DRAM entry exactly this.
DRAM_MEMORY_TYPE = "DRAM"


class SizeError(RuntimeError):
    """A size report or budget is missing, malformed or out of date."""


def _positive_int(value, what):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SizeError("%s must be a positive integer, got %r" % (what, value))
    return value


def load_budget(path):
    """Read *path* into {variant: {"dramUsedMax", "appImageMax"}}.

    The application partition is the same for every variant (stock
    `CONFIG_PARTITION_TABLE_SINGLE_APP_LARGE`, 1500 KiB), so its ceiling is
    shared; static DRAM depends on which hands are compiled in, so it is per
    variant.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SizeError("cannot read size budget %s: %s" % (path, exc))

    if not isinstance(data, dict):
        raise SizeError("%s must contain a JSON object" % path)
    app_max = _positive_int(data.get("appImageMax"), "appImageMax")
    variants = data.get("variants")
    if not isinstance(variants, dict) or not variants:
        raise SizeError("%s has no variants object" % path)

    budgets = {}
    for variant, entry in variants.items():
        if not isinstance(entry, dict):
            raise SizeError("budget for variant %r must be an object" % variant)
        budgets[variant] = {
            "dramUsedMax": _positive_int(
                entry.get("dramUsedMax"), "%s.dramUsedMax" % variant),
            "appImageMax": app_max,
        }
    return budgets


def parse_size_report(payload):
    """Extract the numbers the budget needs from an `idf.py size` json2 report.

    json2 is a documented, stable output format: `total_size` is the sum of the
    image's loadable sections and each `layout` entry is a memory type with
    `used`/`total` bytes. Only the DRAM entry is required here; the image size
    is checked from the packaged .bin instead, because that is what has to fit
    the flash partition after padding.
    """
    if not isinstance(payload, dict):
        raise SizeError("size report must be a JSON object")
    layout = payload.get("layout")
    if not isinstance(layout, list):
        raise SizeError("size report has no layout list")

    dram = None
    for entry in layout:
        if isinstance(entry, dict) and entry.get("name") == DRAM_MEMORY_TYPE:
            dram = entry
            break
    if dram is None:
        raise SizeError("size report has no %s layout entry" % DRAM_MEMORY_TYPE)

    used = dram.get("used")
    total = dram.get("total")
    if isinstance(used, bool) or not isinstance(used, int) or used < 0:
        raise SizeError("size report has a bad DRAM used value: %r" % (used,))
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise SizeError("size report has a bad DRAM total value: %r" % (total,))
    return {"dramUsed": used, "dramTotal": total}


def load_report(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SizeError("cannot read size report %s: %s" % (path, exc))
    return parse_size_report(payload)


def check(report, limits):
    """Return a list of human-readable budget violations (empty when it fits).

    *report* needs `dramUsed` (static DRAM bytes) and `appImage` (packaged .bin
    bytes); *limits* is one entry from `load_budget`.
    """
    problems = []
    used = report["dramUsed"]
    if used > limits["dramUsedMax"]:
        problems.append(
            "static DRAM %d bytes is %d bytes over the %d byte budget"
            % (used, used - limits["dramUsedMax"], limits["dramUsedMax"]))
    image = report["appImage"]
    if image > limits["appImageMax"]:
        problems.append(
            "app image %d bytes is %d bytes over the %d byte budget"
            % (image, image - limits["appImageMax"], limits["appImageMax"]))
    return problems


def format_line(variant, report, limits):
    """One CI log line: the numbers measured and how much budget is left."""
    return (
        "%s: DRAM %6.1f KiB used (%5.1f KiB budget, %5.1f free), "
        "app %7.1f KiB (%7.1f KiB budget, %6.1f free)"
        % (
            variant,
            report["dramUsed"] / 1024.0,
            limits["dramUsedMax"] / 1024.0,
            (limits["dramUsedMax"] - report["dramUsed"]) / 1024.0,
            report["appImage"] / 1024.0,
            limits["appImageMax"] / 1024.0,
            (limits["appImageMax"] - report["appImage"]) / 1024.0,
        )
    )


def require_variant(budgets, variant):
    if variant not in budgets:
        raise SizeError(
            "no size budget for variant %r; add it to %s"
            % (variant, BUDGET_NAME))
    return budgets[variant]
