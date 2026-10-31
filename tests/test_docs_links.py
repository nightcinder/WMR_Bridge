#!/usr/bin/env python3
"""Validate relative links and anchors across docs/ and both READMEs.

Run: python3 tests/test_docs_links.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SCOPE = [ROOT / "README.md", ROOT / "pi" / "README.md"] + sorted(
    (ROOT / "docs").rglob("*.md")
)

LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
REF_RE = re.compile(r"^\s*\[([^\]]+)\]:\s*(\S+)", re.MULTILINE)

EXTERNAL_SCHEMES = ("http://", "https://", "mailto:", "tel:", "ftp://", "data:")


def slugify(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("`", "")
    s = text.lower().strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^\w\-]", "", s, flags=re.UNICODE)
    s = re.sub(r"-{3,}", "--", s)
    s = s.strip("-")
    return s


_anchor_cache: dict[Path, set[str]] = {}


def anchors_for(path: Path) -> set[str]:
    if path in _anchor_cache:
        return _anchor_cache[path]
    if path.suffix.lower() not in {".md", ".markdown", ".mdx"} or not path.exists():
        _anchor_cache[path] = set()
        return _anchor_cache[path]
    text = path.read_text(encoding="utf-8")
    seen: dict[str, int] = {}
    out: set[str] = set()
    lines = text.splitlines()
    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.*?)(?:\s+#+\s*)?$", line)
        if m:
            base = slugify(m.group(2))
            n = seen.get(base, 0)
            seen[base] = n + 1
            out.add(base if n == 0 else f"{base}-{n}")
    i = 0
    while i < len(lines) - 1:
        if re.match(r"^[=-]{3,}\s*$", lines[i + 1]):
            out.add(slugify(lines[i].strip()))
            i += 2
            continue
        i += 1
    _anchor_cache[path] = out
    return out


def resolve(src: Path, raw: str):
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    parts = target.split()
    if not parts:
        return None
    url = parts[0]
    if any(url.startswith(s) for s in EXTERNAL_SCHEMES):
        return None
    if "://" in url:
        return None
    frag = ""
    pathpart = url
    if "#" in url:
        pathpart, frag = url.split("#", 1)
    frag = unquote(frag.strip())
    if pathpart == "":
        path = src
    elif pathpart.startswith("/"):
        path = (ROOT / pathpart.lstrip("/")).resolve()
    else:
        path = (src.parent / pathpart).resolve()
    return path, frag


def collect_targets(text: str):
    for m in LINK_RE.finditer(text):
        yield m.group(1)
    for m in REF_RE.finditer(text):
        yield m.group(2)


class TestDocsLinks(unittest.TestCase):
    def test_relative_links_and_anchors(self):
        errors: list[str] = []
        for src in SCOPE:
            text = src.read_text(encoding="utf-8")
            for raw in collect_targets(text):
                res = resolve(src, raw)
                if res is None:
                    continue
                path, frag = res
                rel_src = src.relative_to(ROOT)
                if not path.exists():
                    errors.append(f"{rel_src}: missing target {raw!r}")
                    continue
                if path.is_dir():
                    continue
                if frag and path.suffix.lower() in {".md", ".markdown", ".mdx"}:
                    if frag not in anchors_for(path):
                        errors.append(
                            f"{rel_src}: broken anchor {raw!r} "
                            f"(target {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path})"
                        )
        if errors:
            self.fail("\n".join(errors))


if __name__ == "__main__":
    unittest.main()
