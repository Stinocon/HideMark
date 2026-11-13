#!/usr/bin/env python3
"""tests for docxfill.py — run with the docx interpreter (python-docx installed):
    ~/.pi/agent/venvs/docx-venv/bin/python tests/test_docxfill.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from docx import Document  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402

import docxfill  # noqa: E402

FAILURES = []


def check(name, ok, detail=""):
    if not ok:
        FAILURES.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'' if ok else f' — {detail}'}")


def build_reference(path: Path) -> None:
    doc = Document()
    doc.add_heading("Old report", level=1)
    doc.add_heading("Old chapter", level=2)
    toc = doc.add_paragraph()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    toc.add_run()._r.append(begin)
    instruction = OxmlElement("w:instrText")
    instruction.text = ' TOC \\o "1-3" \\h \\z \\u '
    toc.add_run()._r.append(instruction)
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    toc.add_run()._r.append(separate)
    toc_result = doc.add_paragraph()
    toc_result.add_run("Old TOC entry")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    doc.add_paragraph().add_run()._r.append(end)
    old_update_fields = OxmlElement("w:updateFields")
    old_update_fields.set(qn("w:val"), "false")
    compat = doc.settings.element.find(qn("w:compat"))
    if compat is None:
        doc.settings.element.append(old_update_fields)
    else:
        compat.addprevious(old_update_fields)
    doc.add_paragraph("Old body text that must be replaced.")
    table = doc.add_table(rows=1, cols=2)
    table.style = doc.styles["Table Grid"]
    table.rows[0].cells[0].text = "old"
    table.rows[0].cells[1].text = "table"
    section = doc.sections[0]
    section.footer.paragraphs[0].text = "Confidential — page footer"
    section.header.paragraphs[0].text = "ACME — header"
    doc.save(str(path))


MD = """# New report

Intro paragraph with **bold**, *italic* and `code` plus an apostrophe: l'acqua.

## Chapter 1

- first bullet
- second bullet

1. numbered one
2. numbered two

| Host | CVE | Severity |
| --- | --- | --- |
| 10.0.0.1 | CVE-2024-1234 | High |
| 10.0.0.2 | CVE-2024-5678 | Critical |

```text
literal block
second line
```

Final paragraph.
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        ref = tmp / "ref.docx"
        md = tmp / "content.md"
        out = tmp / "out.docx"
        build_reference(ref)
        md.write_text(MD, encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, str(REPO / "docxfill.py"), str(ref), str(md), "-o", str(out), "--json"],
            capture_output=True, text=True,
        )
        check("CLI exits 0", proc.returncode == 0, proc.stderr)
        check("output docx exists", out.is_file())

        doc = Document(str(out))
        texts = [p.text for p in doc.paragraphs]
        styles = [p.style.name for p in doc.paragraphs]

        check("old body text is gone", not any("Old body text" in t for t in texts))
        check("TOC field code preserved", any((ins.text or "").lstrip().startswith("TOC ") for ins in doc.element.body.iter(qn("w:instrText"))))
        check("TOC result preserved for field refresh", any("Old TOC entry" in t for t in texts))
        check("heading 1 present with Heading 1 style", any(t == "New report" and s == "Heading 1" for t, s in zip(texts, styles)),
              str(list(zip(texts, styles))[:3]))
        check("heading 2 present with Heading 2 style", any(t == "Chapter 1" and s == "Heading 2" for t, s in zip(texts, styles)))
        check("bullets get the bullet prefix", any(t.startswith("\u2022 first bullet") for t in texts))
        check("numbered items keep the literal number", any(t.startswith("1. numbered one") for t in texts))
        check("apostrophe normalized to U+2019", any("l\u2019acqua" in t for t in texts), str(texts[:3]))

        intro = next(p for p in doc.paragraphs if p.text.startswith("Intro paragraph"))
        title_index = next(i for i, p in enumerate(doc.paragraphs) if p.text == "New report")
        toc_index = next(i for i, p in enumerate(doc.paragraphs) if "Old TOC entry" in p.text)
        intro_index = next(i for i, p in enumerate(doc.paragraphs) if p.text.startswith("Intro paragraph"))
        check("TOC follows report title and precedes body", title_index < toc_index < intro_index)
        runs = {r.text: (r.bold, r.italic, r.font.name) for r in intro.runs}
        check("bold run is bold", runs.get("bold") == (True, None, None), str(runs))
        check("italic run is italic", runs.get("italic") == (None, True, None), str(runs))
        check("code run is Consolas", runs.get("code") == (None, None, "Consolas"), str(runs))

        check("exactly one table, with header and rows", len(doc.tables) == 1 and len(doc.tables[0].rows) == 3,
              f"tables={len(doc.tables)}")
        if doc.tables:
            t = doc.tables[0]
            check("table header cells", [c.text for c in t.rows[0].cells] == ["Host", "CVE", "Severity"])
            check("table data row", [c.text for c in t.rows[2].cells] == ["10.0.0.2", "CVE-2024-5678", "Critical"])
            check("header row is bold", all(r.bold for c in t.rows[0].cells for r in c.paragraphs[0].runs))

        check("code fence became paragraphs", any(t == "literal block" for t in texts) and any(t == "second line" for t in texts))

        # Structure cloned from the reference, not regenerated:
        section = doc.sections[0]
        check("footer preserved", "Confidential" in section.footer.paragraphs[0].text)
        check("header preserved", "ACME" in section.header.paragraphs[0].text)
        check("sectPr still last body child (page setup kept)", doc.element.body[-1].tag == qn("w:sectPr"))
        # updateFields must sit at a schema-valid position in w:settings: after every predecessor
        # (zoom, proofState, defaultTabStop, trackChanges, …) and before every successor
        # (hdrShapeDefaults, footnotePr, compat, docVars, rsids, …).
        settings_tags = [child.tag for child in doc.settings.element]
        uf = qn("w:updateFields")
        check("updateFields set for TOC refresh", uf in settings_tags)
        if uf in settings_tags:
            update_fields = doc.settings.element.find(uf)
            check("updateFields enabled even when reference disabled it", update_fields.get(qn("w:val")) == "true")
            before = {qn(t) for t in ("w:zoom", "w:proofState", "w:defaultTabStop", "w:trackChanges", "w:documentProtection")}
            after = {qn(t) for t in ("w:hdrShapeDefaults", "w:footnotePr", "w:compat", "w:docVars", "w:rsids")}
            i = settings_tags.index(uf)
            check("updateFields after all predecessors", all(settings_tags.index(t) < i for t in settings_tags if t in before))
            check("updateFields before all successors", all(settings_tags.index(t) > i for t in settings_tags if t in after))

        # The reference itself must be untouched:
        ref_doc = Document(str(ref))
        check("reference file not modified", any("Old body text" in p.text for p in ref_doc.paragraphs))

        # CLI guards:
        same = subprocess.run([sys.executable, str(REPO / "docxfill.py"), str(ref), str(md), "-o", str(ref)],
                              capture_output=True, text=True)
        check("refusing reference==output", same.returncode == 2)
        same_content = subprocess.run([sys.executable, str(REPO / "docxfill.py"), str(ref), str(md), "-o", str(md)],
                                      capture_output=True, text=True)
        check("refusing content==output", same_content.returncode == 2 and md.read_text(encoding="utf-8") == MD)
        invalid_ref = tmp / "not-a-docx.docx"
        invalid_ref.write_text("not a DOCX package", encoding="utf-8")
        invalid = subprocess.run([sys.executable, str(REPO / "docxfill.py"), str(invalid_ref), str(md), "-o", str(out)],
                                 capture_output=True, text=True)
        check("invalid reference exits 2 without traceback", invalid.returncode == 2 and "Traceback" not in invalid.stderr)

        # Unit-level: inline parser edge cases
        check("inline: empty and plain", docxfill.parse_inline("plain") == [("plain", False, False, False)])
        check("inline: bold+code", docxfill.parse_inline("a **b** `c`") == [("a ", False, False, False), ("b", True, False, False), (" ", False, False, False), ("c", False, False, True)])

        simple_doc = Document()
        simple_field = OxmlElement("w:fldSimple")
        simple_field.set(qn("w:instr"), 'TOC \\o "1-3" \\h')
        simple_run = OxmlElement("w:r")
        simple_text = OxmlElement("w:t")
        simple_text.text = "Simple TOC entry"
        simple_run.append(simple_text)
        simple_field.append(simple_run)
        simple_paragraph = simple_doc.add_paragraph()
        simple_paragraph._p.append(simple_field)
        docxfill.fill(simple_doc, "# Simple report\n\nBody")
        check("simple TOC field preserved", any(el.get(qn("w:instr"), "").startswith("TOC") for el in simple_doc.element.body.iter(qn("w:fldSimple"))))
        check("simple TOC follows title", simple_doc.paragraphs[0].text == "Simple report" and simple_doc.paragraphs[1]._p.find(qn("w:fldSimple")) is not None)

    print("all checks passed" if not FAILURES else f"{len(FAILURES)} FAILED")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
