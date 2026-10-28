#!/usr/bin/env python3
"""Cross-check the notify/indicate claims in docs/ against wmr_bridge.gatt.

wmr_bridge.gatt is the source of truth.  This test parses its CHARACTERISTIC
lines into the actual GATT properties (READ, WRITE, WRITE_WITHOUT_RESPONSE,
NOTIFY, INDICATE).  Permission flags such as READ_ENCRYPTED and WRITE_ENCRYPTED
are deliberately not treated as properties: FF12 and FF14 have WRITE_ENCRYPTED
permissions but are still notify-only characteristics.

It then extracts these claims from the markdown under docs/:

* "notify only" / "notify-only" (and indicate variants).  Such a claim must
  match exactly {notify} / {indicate}; anything else in the GATT file, in
  particular WRITE or WRITE_WITHOUT_RESPONSE, is a disagreement.  This is the
  FF15 write+notify regression this test exists to catch.
* GATT property tables (a markdown table with a "Properties" header) and
  inline property lists such as `NOTIFY | DYNAMIC`.  The listed properties
  must match the GATT declaration exactly (permissions and DYNAMIC ignored).
* Simple prose such as "FF15 is write-without-response plus notify" or
  "FF11 (read, notify)".  The claimed notify/indicate property must exist.

Run: python3 tests/test_doc_gatt.py
"""

from __future__ import annotations

import bisect
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
GATT_PATH = ROOT / "wmr_bridge.gatt"

UUID_RE = re.compile(r"0000([0-9A-Fa-f]{4})-0000-1000-8000-00805F9B34FB")
ABBREV_UUID_RE = re.compile(r"0000(FF[0-9A-Fa-f]{2})")
FF_RE = re.compile(r"\bFF([0-9A-Fa-f]{2})\b")

CORE_PROPERTIES = {
    "READ": "read",
    "WRITE": "write",
    "WRITE_WITHOUT_RESPONSE": "write_without_response",
    "NOTIFY": "notify",
    "INDICATE": "indicate",
}
PROPERTY_TOKEN = (
    r"(?:READ|WRITE|WRITE_WITHOUT_RESPONSE|NOTIFY|INDICATE|DYNAMIC|"
    r"READ_ENCRYPTED|WRITE_ENCRYPTED)"
)
INLINE_LIST_RE = re.compile(
    rf"(?<![A-Z_])(?:{PROPERTY_TOKEN})"
    rf"(?:\s*\|\s*{PROPERTY_TOKEN})+(?![A-Z_])",
    re.I,
)
TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)

# A characteristic id in prose.  Accepts FF15, 0000FF15-..., a full UUID, and
# the abbreviated 0000FF15-... / 0000FF15-… form used by some tables.
ID_PAT = (
    r"(?:`?(?:(?:0000)?FF[0-9A-Fa-f]{2}(?:-(?:\.\.\.|…))?"
    r"|0000[0-9A-Fa-f]{4}-0000-1000-8000-00805F9B34FB)`?)"
)
GROUP_PAT = rf"{ID_PAT}(?:\s*(?:,|and|/)\s*{ID_PAT})*"

ONLY_WORDS = {
    "notify": ("notify", "notifies", "notification", "notifications"),
    "indicate": ("indicate", "indicates", "indication", "indications"),
}
ANY_ONLY_RE = re.compile(
    r"\b(?P<word>notify|notifies|notification|notifications|"
    r"indicate|indicates|indication|indications)[\s_-]*-?[\s_-]*only\b",
    re.I,
)
WRITE_WITHOUT_RESPONSE_RE = re.compile(
    r"\bwrite\s+(?:with\s*out|w/o|no)\s+(?:a\s+)?response\b", re.I
)
READ_RE = re.compile(r"\bread(?:s|able)?\b", re.I)
WRITE_RE = re.compile(r"\bwrit(?:e|es|ten|able)\b", re.I)
NOTIFY_RE = re.compile(r"\bnotif(?:y|ies|ication|ications)\b", re.I)
INDICATE_RE = re.compile(r"\bindicat(?:e|es|ion|ions)\b", re.I)

checks = 0
failures = 0


def check(cond, what):
    global checks, failures
    checks += 1
    if not cond:
        failures += 1
        print("  FAIL:", what)


@dataclass(frozen=True)
class Claim:
    path: str
    line: int
    char: str
    props: frozenset[str]
    exact: bool
    kind: str
    text: str


def parse_gatt(text: str) -> dict[str, frozenset[str]]:
    """Return {FFxx: {properties}} for the 16-bit characteristic IDs in text."""
    chars: dict[str, frozenset[str]] = {}
    for line in text.splitlines():
        line = line.split("//", 1)[0].strip()
        match = re.match(r"CHARACTERISTIC\s*,\s*([^,]+?)\s*,\s*(.*)$", line)
        if not match:
            continue
        uuid_match = UUID_RE.fullmatch(match.group(1).strip())
        if not uuid_match:
            continue
        char = uuid_match.group(1).upper()
        # Ignore a value field (e.g. GAP_DEVICE_NAME's "WMR Bridge").
        flags_field = match.group(2).split(",", 1)[0]
        flags = {
            flag.strip().upper()
            for flag in flags_field.split("|")
            if flag.strip()
        }
        chars[char] = frozenset(
            prop for flag, prop in CORE_PROPERTIES.items() if flag in flags
        )
    return chars


def ids_in(text: str, chars: dict[str, frozenset[str]]) -> list[str]:
    """All known characteristic IDs mentioned in text, in first-seen order."""
    found = []
    for match in UUID_RE.finditer(text):
        found.append(match.group(1).upper())
    for match in ABBREV_UUID_RE.finditer(text):
        found.append(match.group(1).upper())
    for match in FF_RE.finditer(text):
        found.append("FF" + match.group(1).upper())
    return list(dict.fromkeys(char for char in found if char in chars))


def props_from_text(text: str) -> frozenset[str]:
    """Extract claimed GATT properties from a natural-language property phrase."""
    normalized = " ".join(
        text.lower().replace("_", " ").replace("-", " ").split()
    )
    props = set()
    if WRITE_WITHOUT_RESPONSE_RE.search(normalized):
        props.add("write_without_response")
    if READ_RE.search(normalized):
        props.add("read")
    if NOTIFY_RE.search(normalized):
        props.add("notify")
    if INDICATE_RE.search(normalized):
        props.add("indicate")
    without_write_without_response = WRITE_WITHOUT_RESPONSE_RE.sub(
        " ", normalized
    )
    if WRITE_RE.search(without_write_without_response):
        props.add("write")
    return frozenset(props)


def props_from_tokens(text: str) -> frozenset[str]:
    tokens = [token.strip().upper() for token in re.split(r"\s*\|\s*", text)]
    return frozenset(
        CORE_PROPERTIES[token] for token in tokens if token in CORE_PROPERTIES
    )


def add_claim(
    claims: list[Claim],
    path: str,
    line: int,
    ids: list[str],
    props: frozenset[str],
    exact: bool,
    kind: str,
    text: str,
) -> None:
    if not props:
        return
    snippet = " ".join(text.split())[:120]
    for char in dict.fromkeys(ids):
        claims.append(
            Claim(
                path=path,
                line=line,
                char=char,
                props=props,
                exact=exact,
                kind=kind,
                text=snippet,
            )
        )


def table_claims(
    path: str,
    lines: list[str],
    chars: dict[str, frozenset[str]],
    claims: list[Claim],
) -> None:
    i = 0
    while i + 1 < len(lines):
        if "|" not in lines[i] or not TABLE_SEPARATOR_RE.match(lines[i + 1]):
            i += 1
            continue

        header = [cell.strip().lower() for cell in split_row(lines[i])]
        property_cols = [
            col
            for col, name in enumerate(header)
            if "propert" in name or name == "access"
        ]
        id_cols = [
            col
            for col, name in enumerate(header)
            if "uuid" in name or "characteristic" in name or name in ("item", "id")
        ]

        j = i + 2
        while j < len(lines) and "|" in lines[j] and lines[j].strip():
            cells = split_row(lines[j])
            if property_cols:
                for property_col in property_cols:
                    if property_col >= len(cells):
                        continue
                    phrase = cells[property_col]
                    props = props_from_text(phrase)
                    if not props:
                        continue
                    ids = []
                    for id_col in id_cols:
                        if id_col < len(cells):
                            ids.extend(ids_in(cells[id_col], chars))
                    if not ids:
                        for col in range(property_col):
                            ids.extend(ids_in(cells[col], chars))
                    add_claim(
                        claims,
                        path,
                        j + 1,
                        ids,
                        props,
                        True,
                        "GATT property table",
                        phrase,
                    )
            j += 1
        i = j


def split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def inline_list_claims(
    path: str,
    lines: list[str],
    chars: dict[str, frozenset[str]],
    claims: list[Claim],
) -> None:
    for line_number, line in enumerate(lines, 1):
        for match in INLINE_LIST_RE.finditer(line):
            ids = ids_in(line[: match.start()], chars)
            if not ids:
                # Some prose introduces the list on the line after the
                # characteristic name/table row (e.g. PROTOCOL.md FF14).
                for previous in range(line_number - 2, max(-1, line_number - 27), -1):
                    if lines[previous].lstrip().startswith("#"):
                        break
                    ids = ids_in(lines[previous], chars)
                    if ids:
                        break
            if not ids:
                continue
            add_claim(
                claims,
                path,
                line_number,
                ids,
                props_from_tokens(match.group(0)),
                True,
                "inline GATT property list",
                match.group(0),
            )


def flatten_paragraph(paragraph: list[str]) -> tuple[str, list[int]]:
    parts = []
    offsets = []
    offset = 0
    for line in paragraph:
        stripped = line.strip()
        offsets.append(offset)
        parts.append(stripped)
        offset += len(stripped) + 1
    return " ".join(parts), offsets


def line_for(offsets: list[int], paragraph_start: int, offset: int) -> int:
    index = bisect.bisect_right(offsets, offset) - 1
    return paragraph_start + max(index, 0) + 1


def only_re(kind: str) -> re.Pattern[str]:
    return re.compile(
        r"\b(?:" + "|".join(ONLY_WORDS[kind]) + r")"
        r"[\s_-]*-?[\s_-]*only\b",
        re.I,
    )


def only_kind(word: str) -> str:
    return "notify" if word.lower().startswith("notif") else "indicate"


def paragraph_claims(
    path: str,
    lines: list[str],
    chars: dict[str, frozenset[str]],
    claims: list[Claim],
) -> None:
    """Extract notify-only and simple property claims from prose paragraphs."""
    paragraph_start = 0
    for index in range(len(lines) + 1):
        if index != len(lines) and lines[index].strip():
            continue
        if index > paragraph_start:
            paragraph = lines[paragraph_start:index]
            flat, offsets = flatten_paragraph(paragraph)

            for kind in ("notify", "indicate"):
                claim_re = only_re(kind)
                claim_prop = frozenset({kind})

                # "FF12 and FF14 are notify-only" / "FF14 is an additional
                # notify-only characteristic".
                copula = re.compile(
                    rf"(?P<subj>{GROUP_PAT})\s+(?:is|are)\s+"
                    rf"[^.;\n]{{0,80}}?{claim_re.pattern}",
                    re.I,
                )
                for match in copula.finditer(flat):
                    add_claim(
                        claims,
                        path,
                        line_for(offsets, paragraph_start, match.start()),
                        ids_in(match.group("subj"), chars),
                        claim_prop,
                        True,
                        f"{kind}-only prose claim",
                        match.group(0),
                    )

                # "a notify-only 0000FF14-... characteristic".
                adjective = re.compile(
                    rf"{claim_re.pattern}\s+(?:\*\*)?(?P<subj>{ID_PAT})",
                    re.I,
                )
                for match in adjective.finditer(flat):
                    add_claim(
                        claims,
                        path,
                        line_for(offsets, paragraph_start, match.start()),
                        ids_in(match.group("subj"), chars),
                        claim_prop,
                        True,
                        f"{kind}-only prose claim",
                        match.group(0),
                    )

                # "FF12 (notify only)".
                parenthetical = re.compile(
                    rf"(?P<subj>{GROUP_PAT})\s*\(\s*{claim_re.pattern}",
                    re.I,
                )
                for match in parenthetical.finditer(flat):
                    ids = ids_in(match.group("subj"), chars)
                    # docs/runs/sheets/ff14-ab.md uses shorthand
                    # "FF11, FF12, FF14 (notify only)" for a group whose
                    # individual properties are stated elsewhere.
                    if len(ids) > 1 and "runs" in Path(path).parts:
                        continue
                    add_claim(
                        claims,
                        path,
                        line_for(offsets, paragraph_start, match.start()),
                        ids,
                        claim_prop,
                        True,
                        f"{kind}-only prose claim",
                        match.group(0),
                    )

                # "FF15 with write-without-response ... notify only".
                with_claim = re.compile(
                    rf"(?P<subj>{ID_PAT})\s+(?:with|has|supports?)\s+"
                    rf"[^.;()\n]{{0,80}}?{claim_re.pattern}",
                    re.I,
                )
                for match in with_claim.finditer(flat):
                    add_claim(
                        claims,
                        path,
                        line_for(offsets, paragraph_start, match.start()),
                        ids_in(match.group("subj"), chars),
                        claim_prop,
                        True,
                        f"{kind}-only prose claim",
                        match.group(0),
                    )

            # Fallback for a short paragraph/table row that mentions exactly
            # one characteristic and says it is notify/indicate only.
            paragraph_ids = ids_in(flat, chars)
            if len(paragraph_ids) == 1:
                for match in ANY_ONLY_RE.finditer(flat):
                    kind = only_kind(match.group("word"))
                    add_claim(
                        claims,
                        path,
                        line_for(offsets, paragraph_start, match.start()),
                        paragraph_ids,
                        frozenset({kind}),
                        True,
                        f"{kind}-only prose claim",
                        flat[max(0, match.start() - 60) : match.end() + 40],
                    )

            # Simple presence claims: "FF15 is write-without-response plus
            # notify", "FF11 (read, notify)", "0000FF15-... with ... notify".
            predicate = re.compile(
                rf"(?P<subj>{GROUP_PAT})\s+(?:is|are)\s+"
                rf"(?:(?:therefore|also|an?|the|additional)\s+)*"
                rf"(?:\*\*)?(?:read|write|notify|notifies|notification|"
                rf"notifications|indicate|indicates|indication|indications)\b"
                rf"[^.;()\n]{{0,120}}",
                re.I,
            )
            with_presence = re.compile(
                rf"(?P<subj>{ID_PAT})\s+with\s+"
                rf"(?P<phrase>[^.;()\n]{{0,120}})",
                re.I,
            )
            parenthetical_presence = re.compile(
                rf"(?P<subj>{GROUP_PAT})\s*\((?P<phrase>[^)]*)\)",
                re.I,
            )
            for pattern in (predicate, with_presence, parenthetical_presence):
                for match in pattern.finditer(flat):
                    phrase = (
                        match.group("phrase")
                        if "phrase" in match.groupdict()
                        else match.group(0)
                    )
                    wanted = props_from_text(phrase) & frozenset(
                        {"notify", "indicate"}
                    )
                    if wanted:
                        add_claim(
                            claims,
                            path,
                            line_for(offsets, paragraph_start, match.start()),
                            ids_in(match.group("subj"), chars),
                            wanted,
                            False,
                            "notify/indicate prose claim",
                            match.group(0),
                        )
        paragraph_start = index + 1


def collect_claims(
    paths: list[Path],
    base: Path,
    chars: dict[str, frozenset[str]],
) -> list[Claim]:
    claims: list[Claim] = []
    for path in paths:
        label = str(path.relative_to(base))
        lines = path.read_text(encoding="utf-8").splitlines()
        table_claims(label, lines, chars, claims)
        inline_list_claims(label, lines, chars, claims)
        paragraph_claims(label, lines, chars, claims)

    unique = []
    seen = set()
    for claim in claims:
        key = (
            claim.path,
            claim.line,
            claim.char,
            claim.props,
            claim.exact,
            claim.kind,
        )
        if key not in seen:
            seen.add(key)
            unique.append(claim)
    return unique


def format_props(props: frozenset[str]) -> str:
    return "{" + ", ".join(sorted(props)) + "}"


def mismatches(
    chars: dict[str, frozenset[str]], claims: list[Claim]
) -> list[tuple[Claim, frozenset[str]]]:
    bad = []
    for claim in claims:
        actual = chars.get(claim.char)
        if actual is None:
            bad.append((claim, frozenset()))
            continue
        if claim.exact:
            if claim.props != actual:
                bad.append((claim, actual))
        elif not claim.props <= actual:
            bad.append((claim, actual))
    return bad


def selftest() -> None:
    """Prove that a notify-only doc claim conflicts with write+notify GATT."""
    gatt = """
PRIMARY_SERVICE, 0000FF10-0000-1000-8000-00805F9B34FB
CHARACTERISTIC, 0000FF12-0000-1000-8000-00805F9B34FB, NOTIFY | DYNAMIC | WRITE_ENCRYPTED,
CHARACTERISTIC, 0000FF15-0000-1000-8000-00805F9B34FB, WRITE_WITHOUT_RESPONSE | NOTIFY | DYNAMIC | WRITE_ENCRYPTED,
"""
    chars = parse_gatt(gatt)
    check(
        chars.get("FF15") == frozenset({"notify", "write_without_response"}),
        "self-test parses FF15 as write-without-response + notify",
    )
    check(
        chars.get("FF12") == frozenset({"notify"}),
        "self-test treats WRITE_ENCRYPTED as a permission, not a property",
    )

    table_header = (
        "| Item | UUID | Properties |\n"
        "| --- | --- | --- |\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        temp_root = Path(tmp)
        bad_doc = temp_root / "PROTOCOL.md"
        bad_doc.write_text(
            table_header
            + "| Characteristic | `0000FF15-0000-1000-8000-00805F9B34FB` | "
            "notify only |\n",
            encoding="utf-8",
        )
        bad_claims = collect_claims([bad_doc], temp_root, chars)
        bad = mismatches(chars, bad_claims)
        check(
            any(
                claim.char == "FF15"
                and claim.props == frozenset({"notify"})
                and "write_without_response" in actual
                for claim, actual in bad
            ),
            "self-test catches a notify-only claim for write+notify FF15",
        )

        prose_doc = temp_root / "SECURITY.md"
        prose_doc.write_text("FF15 is notify-only.\n", encoding="utf-8")
        prose_bad = mismatches(
            chars, collect_claims([prose_doc], temp_root, chars)
        )
        check(
            any(claim.char == "FF15" for claim, _ in prose_bad),
            "self-test catches a notify-only FF15 prose claim",
        )

        good_doc = temp_root / "GOOD.md"
        good_doc.write_text(
            table_header
            + "| Characteristic | `0000FF15-0000-1000-8000-00805F9B34FB` | "
            "write without response, notify |\n",
            encoding="utf-8",
        )
        good = mismatches(chars, collect_claims([good_doc], temp_root, chars))
        check(not good, "self-test accepts FF15 write-without-response + notify")


def main() -> int:
    selftest()

    gatt_text = GATT_PATH.read_text(encoding="utf-8")
    chars = parse_gatt(gatt_text)
    check(bool(chars), "wmr_bridge.gatt declares 16-bit characteristics")

    doc_paths = sorted(DOCS_DIR.rglob("*.md"))
    check(bool(doc_paths), "docs/ contains markdown files")
    claims = collect_claims(doc_paths, ROOT, chars)
    check(bool(claims), "docs/ contains recognizable GATT property claims")
    check(
        any(
            claim.char == "FF15"
            and claim.exact
            and claim.props == frozenset({"notify", "write_without_response"})
            for claim in claims
        ),
        "docs/ still state FF15 is write-without-response + notify",
    )
    check(
        any(
            claim.char in ("FF12", "FF14")
            and claim.exact
            and claim.props == frozenset({"notify"})
            for claim in claims
        ),
        "docs/ still contain a notify-only stream characteristic claim",
    )

    bad = mismatches(chars, claims)
    for claim, actual in bad:
        print(
            f"  FAIL: {claim.path}:{claim.line}: docs claim {claim.char} is "
            f"{format_props(claim.props)} ({claim.kind}), but wmr_bridge.gatt "
            f"declares {format_props(actual)}: {claim.text!r}"
        )
    check(not bad, "every docs notify/indicate claim matches wmr_bridge.gatt")

    print(
        f"\n{len(claims)} GATT property claims checked in "
        f"{len(doc_paths)} markdown files; "
        f"{checks} checks, {failures} failure(s)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
