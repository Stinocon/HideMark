#!/usr/bin/env python3
"""Check that public numeric claims match the current implementation.

    python3 scripts/check-doc-numbers.py
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import anon  # noqa: E402

MB = 1024 * 1024


def load_server_module():
    """Load module-level limits without starting the web server."""
    spec = importlib.util.spec_from_file_location("hidemark_web_server", ROOT / "web" / "server.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []
    checked = 0

    def claim(label: str, pattern: str, doc: str) -> None:
        nonlocal checked
        checked += 1
        if not re.search(pattern, read(doc)):
            failures.append(f"{label}: {doc} has no match for /{pattern}/")

    server = load_server_module()
    claim("version", rf"HideMark {re.escape(anon.VERSION)}", "README.md")
    claim("map tag width", rf"{anon.TAG_DIGITS} hexadecimal digits", "docs/DESIGN.md")
    claim("scan limit", rf"{anon.SCAN_MAX_BYTES // MB} MB", "SECURITY.md")
    claim("upload cap", rf"{server.MAX_BODY_BYTES // MB} MB", "SECURITY.md")
    claim("converter output cap", rf"{server.CONVERT_MAX_BYTES // MB} MB", "SECURITY.md")
    claim("converter timeout", rf"{server.CONVERT_TIMEOUT_SECONDS} seconds", "SECURITY.md")
    claim("rate limit", rf"{server.DEFAULT_RATE_LIMIT} requests per minute", "SECURITY.md")

    for doc in ("README.md", "SECURITY.md", "docs/DESIGN.md"):
        for match in re.finditer(r"\[[A-Z][A-Z0-9_]*-\d+-([0-9a-f]+)\]", read(doc)):
            checked += 1
            if len(match.group(1)) not in (anon.TAG_DIGITS, 8):
                failures.append(
                    f"placeholder example in {doc}: {match.group(0)} has {len(match.group(1))} hex digits"
                )

    if failures:
        for failure in failures:
            print(f"FAIL  {failure}")
        print(f"check-doc-numbers: {len(failures)} of {checked} claim(s) drifted from the code")
        return 1
    print(f"check-doc-numbers: {checked} claim(s) in the docs match the code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
