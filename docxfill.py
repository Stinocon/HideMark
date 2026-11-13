#!/usr/bin/env python3
"""docxfill.py — build a .docx from Markdown by cloning the structure of a reference .docx.

Workflow (VA reports and similar): the data comes from new source documents, the LAYOUT from a
past report. This tool opens the reference with python-docx, removes every body block, and
rebuilds the body from the Markdown — while styles, headers, footers, the TOC field, numbering
and the final section setup stay untouched (declared limitation: a multi-section reference keeps
only the LAST section's setup — per-paragraph section breaks live inside the removed paragraphs).
Chapter numbering (when the reference uses automatic
multilevel numbering on the heading styles) and the TOC therefore behave as in the original.
`word/settings.xml` gets `<w:updateFields w:val="true"/>` so Word refreshes TOC and fields on
open; if it does not, select-all + F9 in Word does the same.

Markdown support: `#`..`######` headings (mapped to Heading 1..6 styles), paragraphs,
**bold**, *italic*, `code`, `-` bullets (List Paragraph style when present, "•" prefix),
`1.` numbered items (List Paragraph style, literal number kept), `| pipe | tables |` (real
tables, Table Grid style when present, header row bold), ``` fenced blocks (one paragraph per
line, Consolas). Straight apostrophes in normal text are normalized to U+2019, matching the
typographic convention of Italian Word templates. Deliberately NOT supported (declared, not
faked): images, nested lists, multi-level numbering control, arbitrary layout.

python-docx only, no network. Run with the docx interpreter, e.g.:
    ~/.pi/agent/venvs/docx-venv/bin/python docxfill.py reference.docx content.md -o report.docx

Exit codes: 0 ok, 2 usage/IO error.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
import re
import sys
from pathlib import Path

try:
    from docx import Document
    from docx.opc.exceptions import PackageNotFoundError
    from docx.oxml.ns import qn
except ImportError:  # pragma: no cover
    sys.stderr.write("docxfill.py: python-docx is required (use the docx-venv interpreter)\n")
    raise SystemExit(2)

# Elements of w:settings that FOLLOW w:updateFields in the ECMA-376 CT_Settings sequence
# (…alwaysMergeEmptyNamespace, w:updateFields, hdrShapeDefaults, footnotePr, endnotePr, compat,
# docVars, rsids, …). The flag is inserted before the first of these, or appended if none present.
_SETTINGS_AFTER_UPDATE = {
    "w:hdrShapeDefaults", "w:footnotePr", "w:endnotePr", "w:compat", "w:docVars", "w:rsids",
    "m:mathPr", "w:themeFontLang", "w:clrSchemeMapping", "w:shapeDefaults",
    "w:decimalSymbol", "w:listSeparator",
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_INLINE_RE = re.compile(r"(\*\*.+?\*\*|\*[^*\n]+?\*|`[^`\n]+?`)")
_TOC_INSTRUCTION_RE = re.compile(r"^\s*TOC(?:\s|$)", re.IGNORECASE)


def parse_inline(text: str):
    """Split inline Markdown into (text, bold, italic, code) runs."""
    runs = []
    for part in _INLINE_RE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            runs.append((part[2:-2], True, False, False))
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            runs.append((part[1:-1], False, False, True))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            runs.append((part[1:-1], False, True, False))
        else:
            runs.append((part, False, False, False))
    return runs


def add_runs(paragraph, text: str) -> None:
    for raw, bold, italic, code in parse_inline(text):
        # Typographic apostrophe in prose (not in code): matches Italian Word templates, where a
        # straight ' breaks anchor matching and looks wrong next to U+2019 elsewhere.
        run = paragraph.add_run(raw if code else raw.replace("'", "\u2019"))
        if bold:
            run.bold = True
        if italic:
            run.italic = True
        if code:
            run.font.name = "Consolas"


def style_exists(doc, name: str) -> bool:
    try:
        doc.styles[name]
        return True
    except KeyError:
        return False


def add_heading(doc, level: int, text: str) -> None:
    style = f"Heading {level}" if style_exists(doc, f"Heading {level}") else None
    p = doc.add_paragraph(style=style)
    add_runs(p, text)
    if style is None:  # no heading style in the reference: make it visibly a heading anyway
        for run in p.runs:
            run.bold = True


def add_bullet(doc, text: str, number: str | None) -> None:
    style = "List Paragraph" if style_exists(doc, "List Paragraph") else None
    p = doc.add_paragraph(style=style)
    prefix = f"{number}. " if number is not None else "\u2022 "
    add_runs(p, prefix + text)


def split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def add_table(doc, header: list[str], rows: list[list[str]]) -> None:
    table = doc.add_table(rows=len(rows) + 1, cols=len(header))
    if style_exists(doc, "Table Grid"):
        table.style = doc.styles["Table Grid"]
    for j, cell_text in enumerate(header):
        cell = table.rows[0].cells[j]
        cell.paragraphs[0].text = ""
        add_runs(cell.paragraphs[0], cell_text)
        for run in cell.paragraphs[0].runs:
            run.bold = True
    for i, row in enumerate(rows, start=1):
        if len(row) > len(header):
            sys.stderr.write(f"docxfill.py: warning: table row {i} has {len(row)} cells, header has {len(header)} — extra cells dropped\n")
        for j in range(len(header)):
            cell_text = row[j] if j < len(row) else ""
            cell = table.rows[i].cells[j]
            cell.paragraphs[0].text = ""
            add_runs(cell.paragraphs[0], cell_text)


def fill(doc, markdown: str) -> dict:
    """Replace the body of `doc` with content generated from `markdown`. Returns counts."""
    body = doc.element.body
    sect_pr = body.find(qn("w:sectPr"))
    toc_blocks = extract_toc_blocks(body)
    for child in list(body):
        if child is not sect_pr:
            body.remove(child)

    counts = {"headings": 0, "paragraphs": 0, "bullets": 0, "tables": 0, "code_lines": 0}
    lines = markdown.splitlines()
    i = 0
    in_code = False
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            in_code = not in_code
            i += 1
            continue
        if in_code:
            if line.strip():
                p = doc.add_paragraph()
                add_runs(p, line)
                counts["code_lines"] += 1
            i += 1
            continue
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        m = _HEADING_RE.match(stripped)
        if m:
            add_heading(doc, len(m.group(1)), m.group(2).strip())
            counts["headings"] += 1
            i += 1
            continue
        m = _BULLET_RE.match(line)
        if m:
            add_bullet(doc, m.group(1).strip(), None)
            counts["bullets"] += 1
            i += 1
            continue
        m = _NUMBERED_RE.match(line)
        if m:
            add_bullet(doc, m.group(2).strip(), m.group(1))
            counts["bullets"] += 1
            i += 1
            continue
        if stripped.startswith("|") and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]) and "-" in lines[i + 1]:
            header = split_row(stripped)
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i].strip()))
                i += 1
            add_table(doc, header, rows)
            counts["tables"] += 1
            continue
        p = doc.add_paragraph()
        add_runs(p, stripped)
        counts["paragraphs"] += 1
        i += 1

    # A reference TOC belongs after the report title. Its result text is retained until Word
    # refreshes the field on open; with no level-one title, place it before generated content.
    if toc_blocks:
        title = next((p for p in doc.paragraphs if p.style.name == "Heading 1"), None)
        insert_at = body.index(title._p) + 1 if title is not None else 0
        for offset, block in enumerate(toc_blocks):
            body.insert(insert_at + offset, block)
    return counts


def set_update_fields(doc) -> None:
    settings = doc.settings.element
    existing = settings.find(qn("w:updateFields"))
    if existing is not None:
        existing.set(qn("w:val"), "true")
        return
    el = settings.makeelement(qn("w:updateFields"), {qn("w:val"): "true"})
    for child in settings:
        if child.tag in {qn(t) for t in _SETTINGS_AFTER_UPDATE}:
            child.addprevious(el)
            return
    settings.append(el)


def extract_toc_blocks(body) -> list:
    """Copy complete table-of-contents field blocks from a reference document.

    Word stores a TOC as either a simple field or a complex field spanning runs and sometimes
    paragraphs. The complex-field stack preserves the entire result between its begin and end
    markers, while leaving unrelated body content out of the generated document.
    """
    children = list(body)
    fields: list[list[object]] = []
    ranges: list[tuple[int, int]] = []

    for index, child in enumerate(children):
        for simple in child.iter(qn("w:fldSimple")):
            instruction = simple.get(qn("w:instr"), "")
            if _TOC_INSTRUCTION_RE.match(instruction):
                ranges.append((index, index))

        for element in child.iter():
            if element.tag == qn("w:fldChar"):
                kind = element.get(qn("w:fldCharType"))
                if kind == "begin":
                    fields.append([index, False, index])
                elif kind == "end" and fields:
                    start, is_toc, _instruction_end = fields.pop()
                    if is_toc:
                        ranges.append((int(start), index))
            elif element.tag == qn("w:instrText") and fields:
                if _TOC_INSTRUCTION_RE.match(element.text or ""):
                    fields[-1][1] = True
                    fields[-1][2] = index

    # Preserve malformed but recognizable fields at least through the instruction block so the
    # document does not silently lose the TOC field code. Word can rebuild its result on open.
    for start, is_toc, instruction_end in fields:
        if is_toc:
            ranges.append((int(start), int(instruction_end)))

    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return [deepcopy(block) for start, end in merged for block in children[start : end + 1]]


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a .docx from Markdown, cloning the structure of a reference .docx.")
    ap.add_argument("reference", help="existing .docx whose styles/headers/footers/TOC are cloned")
    ap.add_argument("content", help="Markdown file with the new content")
    ap.add_argument("-o", "--out", required=True, help="output .docx path")
    ap.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    args = ap.parse_args()

    reference = Path(args.reference)
    content = Path(args.content)
    out = Path(args.out)
    if not reference.is_file():
        sys.stderr.write(f"docxfill.py: reference not found: {reference}\n")
        return 2
    if not content.is_file():
        sys.stderr.write(f"docxfill.py: content not found: {content}\n")
        return 2
    same_path = out.resolve() in {reference.resolve(), content.resolve()}
    same_file = out.exists() and any(os.path.samefile(out, source) for source in (reference, content))
    if same_path or same_file:
        sys.stderr.write("docxfill.py: output must differ from both input files (inputs are never overwritten)\n")
        return 2

    try:
        doc = Document(str(reference))
        markdown = content.read_text(encoding="utf-8")
        counts = fill(doc, markdown)
        set_update_fields(doc)
        out.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(out))
    except (OSError, UnicodeError, ValueError, PackageNotFoundError) as exc:
        sys.stderr.write(f"docxfill.py: invalid DOCX or I/O error ({type(exc).__name__})\n")
        return 2

    report = {"output": str(out), "reference": str(reference), "content": str(content), **counts}
    if args.json:
        print(json.dumps(report))
    else:
        print(
            f"docxfill.py: wrote {out} — {counts['headings']} headings, {counts['paragraphs']} paragraphs, "
            f"{counts['bullets']} list items, {counts['tables']} tables, {counts['code_lines']} code lines. "
            "TOC/fields refresh on open (updateFields set); otherwise select-all + F9 in Word."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
