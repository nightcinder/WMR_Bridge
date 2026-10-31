#!/usr/bin/env python3
"""Offline tests for tools/firmware/sizecheck.py.

No ESP-IDF, no build directory, no network: the size report is synthetic and
the budget is written to a temporary file. The simulated 1 KiB overrun is the
point of the suite -- it proves the CI assertion actually fails a build that
eats the DRAM headroom. Run:

    python3 tests/test_firmware_size.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "firmware"))

import sizecheck as sc  # noqa: E402

checks = 0
failures = 0


def check(cond, what):
    global checks, failures
    checks += 1
    if not cond:
        failures += 1
        print("  FAIL:", what)


def report(dram_used=250000, image=800000, dram_total=270336):
    return {"dramUsed": dram_used, "dramTotal": dram_total, "appImage": image}


def limits(dram=260000, image=900000):
    return {"dramUsedMax": dram, "appImageMax": image}


def test_within_budget():
    check(sc.check(report(), limits()) == [], "a build inside both budgets passes")


def test_dram_overrun_by_one_kib_fails():
    problems = sc.check(report(dram_used=261024), limits())
    check(len(problems) == 1, "a 1 KiB DRAM overrun is a violation")
    check("1024 bytes over" in problems[0],
          "the violation states how far over budget the build is")
    check("DRAM" in problems[0], "the violation names static DRAM")


def test_image_overrun_by_one_kib_fails():
    problems = sc.check(report(image=901024), limits())
    check(len(problems) == 1, "a 1 KiB image overrun is a violation")
    check("app image" in problems[0], "the violation names the app image")


def test_both_overruns_are_reported():
    problems = sc.check(report(dram_used=270000, image=1000000), limits())
    check(len(problems) == 2, "both violations are reported, not just the first")


def test_exact_budget_passes():
    check(sc.check(report(dram_used=260000, image=900000), limits()) == [],
          "using exactly the budget is allowed")


def test_parse_json2_report():
    payload = {
        "version": "1.2",
        "total_size": 812345,
        "layout": [
            {"name": "IRAM", "used": 100, "total": 200, "parts": {}},
            {"name": "DRAM", "used": 266808, "total": 335872, "parts": {}},
            {"name": "Flash Data", "used": 400000, "total": 0, "parts": {}},
        ],
    }
    parsed = sc.parse_size_report(payload)
    check(parsed["dramUsed"] == 266808, "DRAM used is read from the layout")
    check(parsed["dramTotal"] == 335872, "DRAM total is kept for the report")


def test_parse_rejects_missing_dram():
    try:
        sc.parse_size_report({"layout": [{"name": "IRAM", "used": 1}]})
    except sc.SizeError as exc:
        check("DRAM" in str(exc), "a report without DRAM names the missing entry")
    else:
        check(False, "a report without DRAM must be rejected")


def test_parse_rejects_bad_used():
    try:
        sc.parse_size_report(
            {"layout": [{"name": "DRAM", "used": "lots", "total": 1}]})
    except sc.SizeError as exc:
        check("used" in str(exc), "a non-numeric DRAM size is rejected")
    else:
        check(False, "a non-numeric DRAM size must be rejected")


def write_budget(payload):
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8")
    with handle:
        json.dump(payload, handle)
    return handle.name


def test_load_budget():
    path = write_budget({
        "appImageMax": 1519616,
        "variants": {"both": {"dramUsedMax": 260000},
                     "left": {"dramUsedMax": 250000}},
    })
    try:
        budgets = sc.load_budget(path)
    finally:
        os.unlink(path)
    check(set(budgets) == {"both", "left"}, "both variants are loaded")
    check(budgets["both"]["dramUsedMax"] == 260000,
          "the per-variant DRAM budget is kept")
    check(budgets["left"]["appImageMax"] == 1519616,
          "the shared app image budget applies to every variant")


def test_load_budget_rejects_missing_variants():
    path = write_budget({"appImageMax": 1519616})
    try:
        sc.load_budget(path)
    except sc.SizeError as exc:
        check("variants" in str(exc), "a budget without variants is rejected")
    else:
        check(False, "a budget without variants must be rejected")
    finally:
        os.unlink(path)


def test_load_budget_rejects_bad_limit():
    path = write_budget({
        "appImageMax": 0,
        "variants": {"both": {"dramUsedMax": 260000}},
    })
    try:
        sc.load_budget(path)
    except sc.SizeError as exc:
        check("appImageMax" in str(exc), "a non-positive limit is rejected")
    else:
        check(False, "a non-positive limit must be rejected")
    finally:
        os.unlink(path)


def test_require_variant():
    budgets = {"both": limits()}
    check(sc.require_variant(budgets, "both") == limits(),
          "a known variant returns its limits")
    try:
        sc.require_variant(budgets, "right")
    except sc.SizeError as exc:
        check("right" in str(exc), "an unknown variant is named in the error")
    else:
        check(False, "a variant without a budget must be rejected")


def test_checked_in_budget_covers_every_variant():
    path = os.path.join(HERE, "..", "tools", "firmware", sc.BUDGET_NAME)
    budgets = sc.load_budget(path)
    for variant in ("both", "left", "right"):
        check(variant in budgets,
              "the checked-in budget covers the %s variant" % variant)


def test_format_line():
    line = sc.format_line("both", report(dram_used=260000, image=900000),
                          limits())
    check(line.startswith("both: DRAM"), "the line starts with the variant")
    check("0.0 free" in line, "a build exactly on budget shows no headroom")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    print("\n%d checks, %d failures" % (checks, failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
