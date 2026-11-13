#!/usr/bin/env python3
"""tests for docxstruct.py — run with the docx interpreter:
    ~/.pi/agent/venvs/docx-venv/bin/python tests/test_docxstruct.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from docx import Document  # noqa: E402

import docxstruct  # noqa: E402

FAILURES = []


def check(name, ok, detail=""):
    if not ok:
        FAILURES.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'' if ok else f' — {detail}'}")


def build(path: Path) -> None:
    doc = Document()
    doc.add_heading("Report A", level=1)
    doc.add_paragraph("Body text that must NOT appear in the structure dump beyond counts.")
    doc.add_heading("Chapter A.1", level=2)
    t = doc.add_table(rows=3, cols=4)
    t.style = doc.styles["Table Grid"]
    doc.add_paragraph("More text.")
    doc.sections[0].header.paragraphs[0].text = "HDR"
    doc.sections[0].footer.paragraphs[0].text = "FTR"
    doc.save(str(path))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        ref = tmp / "ref.docx"
        build(ref)

        # unit level
        s = docxstruct.structure(ref)
        check("headings captured with levels", [h[0] for h in s["headings"]] == [1, 2], str(s["headings"]))
        check("table dims captured", s["tables"] == [(3, 4)], str(s["tables"]))
        check("header/footer captured", s["sections"][0]["header"] == "HDR" and s["sections"][0]["footer"] == "FTR")
        check("styles used listed", "Heading 1" in s["styles_used"] and "Table Grid" not in s["styles_used"])

        md = docxstruct.to_markdown(s)
        check("markdown mirrors heading levels", "# Report A" in md and "## Chapter A.1" in md)
        check("body prose does NOT leak into structure dump", "Body text that must NOT appear" not in md)

        # CLI level
        proc = subprocess.run([sys.executable, str(REPO / "docxstruct.py"), str(ref), "--json"],
                              capture_output=True, text=True)
        check("CLI --json exits 0 and parses", proc.returncode == 0)
        try:
            parsed = json.loads(proc.stdout)
            check("json has headings/tables/sections", parsed["headings"] and parsed["tables"] and parsed["sections"])
            check("json exposes basename only", parsed["file"] == "ref.docx")
        except Exception as e:  # noqa: BLE001
            check("json parses", False, str(e))

        out = tmp / "struct.md"
        proc2 = subprocess.run([sys.executable, str(REPO / "docxstruct.py"), str(ref), "-o", str(out)],
                               capture_output=True, text=True)
        check("CLI -o writes file", proc2.returncode == 0 and out.is_file() and "# Report A" in out.read_text())
        overwrite = subprocess.run([sys.executable, str(REPO / "docxstruct.py"), str(ref), "-o", str(ref)],
                                   capture_output=True, text=True)
        check("refusing output==input preserves source", overwrite.returncode == 2 and Document(str(ref)).paragraphs[0].text == "Report A")

        missing = subprocess.run([sys.executable, str(REPO / "docxstruct.py"), str(tmp / "nope.docx")],
                                 capture_output=True, text=True)
        check("missing file exits 2", missing.returncode == 2)
        invalid_docx = tmp / "invalid.docx"
        invalid_docx.write_text("not a DOCX package", encoding="utf-8")
        invalid = subprocess.run([sys.executable, str(REPO / "docxstruct.py"), str(invalid_docx)],
                                 capture_output=True, text=True)
        check("invalid DOCX exits 2 without traceback", invalid.returncode == 2 and "Traceback" not in invalid.stderr)

        # regression: shell-escaped path with spaces (`My\\ File.docx` for `My File.docx`)
        spaced = tmp / "My Report.docx"
        build(spaced)
        escaped = subprocess.run([sys.executable, str(REPO / "docxstruct.py"), str(spaced).replace(" ", "\\ ")],
                                 capture_output=True, text=True)
        check("shell-escaped path resolves", escaped.returncode == 0 and "# Report A" in escaped.stdout, escaped.stderr[-200:])

        # regression: output into a not-yet-existing directory
        deep = tmp / "a" / "b" / "struct.md"
        proc3 = subprocess.run([sys.executable, str(REPO / "docxstruct.py"), str(ref), "-o", str(deep)],
                               capture_output=True, text=True)
        check("output parent dirs are created", proc3.returncode == 0 and deep.is_file(), proc3.stderr[-200:])

        # --compare: identical structure => MATCH; different heading level => structural diff
        same = subprocess.run([sys.executable, str(REPO / "docxstruct.py"), str(ref), "--compare", str(ref), "--json"],
                              capture_output=True, text=True)
        c = json.loads(same.stdout)
        check("compare with itself: MATCH", same.returncode == 0 and c["match"] is True and not c["structural_diffs"])

        other = tmp / "other.docx"
        d2 = Document()
        d2.add_heading("Report A", level=1)
        d2.add_heading("Flat chapter", level=1)  # ref has level 2 here: flattened -> must fail
        d2.save(str(other))
        diff = subprocess.run([sys.executable, str(REPO / "docxstruct.py"), str(ref), "--compare", str(other), "--json"],
                              capture_output=True, text=True)
        c2 = json.loads(diff.stdout)
        check("compare detects flattened level (missing ref level)", c2["match"] is False and any("missing" in d for d in c2["structural_diffs"]), diff.stdout[:300])
        check("compare lists changed heading text", c2["changed_headings"], diff.stdout[:300])
        overwrite_compare = subprocess.run([sys.executable, str(REPO / "docxstruct.py"), str(ref), "--compare", str(other), "-o", str(other)],
                                           capture_output=True, text=True)
        check("compare output cannot overwrite either input", overwrite_compare.returncode == 2 and Document(str(other)).paragraphs[0].text == "Report A")

        # extra deeper subsection / extra chapter are LEGITIMATE: subsequence semantics => still MATCH
        extra = tmp / "extra.docx"
        d3 = Document()
        d3.add_heading("Report A", level=1)
        d3.add_heading("Chapter A.1", level=2)
        d3.add_heading("Deeper note", level=3)  # extra deeper level: allowed
        d3.add_heading("New chapter", level=1)   # extra top-level chapter: allowed
        d3.sections[0].header.paragraphs[0].text = "HDR"
        d3.sections[0].footer.paragraphs[0].text = "FTR"
        t3 = d3.add_table(rows=3, cols=4); t3.style = d3.styles["Table Grid"]
        d3.save(str(extra))
        okc = subprocess.run([sys.executable, str(REPO / "docxstruct.py"), str(ref), "--compare", str(extra), "--json"],
                             capture_output=True, text=True)
        c3 = json.loads(okc.stdout)
        check("extra subsections/chapters still MATCH (subsequence)", c3["match"] is True, okc.stdout[:400])

    print("all checks passed" if not FAILURES else f"{len(FAILURES)} FAILED")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
