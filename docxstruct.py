#!/usr/bin/env python3
"""docxstruct.py — dump the STRUCTURE of a .docx as Markdown (no body text beyond headings).

Purpose: give an LLM the layout of a reference document (chapter hierarchy, styles, tables,
headers/footers, sections) without dumping the full sensitive body into the context.

Output: `#`/`##`/… lines mirroring Heading 1..6 (style name in parens when it differs),
then a summary block: paragraph/table/image counts, table dimensions, header/footer text
per section, section count, list of styles actually used. JSON reports only the input basename,
not its absolute local path.

python-docx only, read-only (never modifies the input). Run with the docx interpreter:
    ~/.pi/agent/venvs/docx-venv/bin/python docxstruct.py reference.docx
    ~/.pi/agent/venvs/docx-venv/bin/python docxstruct.py reference.docx -o structure.md

Exit codes: 0 ok, 2 usage/IO error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from docx import Document
    from docx.opc.exceptions import PackageNotFoundError
except ImportError:  # pragma: no cover
    sys.stderr.write("docxstruct.py: python-docx is required (use the docx-venv interpreter)\n")
    raise SystemExit(2)

MAX_HEADING_CHARS = 120


def structure(path: Path) -> dict:
    doc = Document(str(path))
    headings = []
    for p in doc.paragraphs:
        style = p.style.name
        if style.startswith("Heading") and p.text.strip():
            try:
                level = int(style.split()[-1])
            except ValueError:
                level = 1
            text = p.text.strip()[:MAX_HEADING_CHARS]
            headings.append((min(level, 6), text, style))

    tables = [(len(t.rows), len(t.columns)) for t in doc.tables]
    sections = []
    for i, s in enumerate(doc.sections):
        header = " | ".join(p.text.strip() for p in s.header.paragraphs if p.text.strip())
        footer = " | ".join(p.text.strip() for p in s.footer.paragraphs if p.text.strip())
        sections.append({"index": i, "header": header, "footer": footer})

    used_styles = sorted({p.style.name for p in doc.paragraphs if p.text.strip()})
    inline_shapes = len(doc.inline_shapes)
    return {
        "file": path.name,
        "headings": headings,
        "paragraphs": len(doc.paragraphs),
        "tables": tables,
        "inline_images": inline_shapes,
        "sections": sections,
        "styles_used": used_styles,
    }


def to_markdown(s: dict) -> str:
    lines = [f"# Structure of `{Path(s['file']).name}`", ""]
    lines.append("## Chapter hierarchy")
    if s["headings"]:
        for level, text, style in s["headings"]:
            suffix = f" ({style})" if style != f"Heading {level}" else ""
            lines.append(f"{'#' * level} {text}{suffix}")
    else:
        lines.append("(no headings)")
    lines += [
        "",
        "## Summary",
        f"- paragraphs: {s['paragraphs']}",
        f"- tables: {len(s['tables'])}" + (f" (dims: {', '.join(f'{r}x{c}' for r, c in s['tables'])})" if s["tables"] else ""),
        f"- inline images: {s['inline_images']}",
        f"- sections: {len(s['sections'])}",
        "- styles used: " + ", ".join(s["styles_used"]),
        "",
        "## Headers / footers",
    ]
    for sec in s["sections"]:
        lines.append(f"- section {sec['index']}: header={sec['header']!r} footer={sec['footer']!r}")
    return "\n".join(lines) + "\n"


def resolve_input(raw: str) -> Path:
    """Accept shell-escaped paths: a non-existent path containing backslashes is retried with the
    backslashes removed (models often paste `My\\ File.docx` when the real name is `My File.docx`)."""
    p = Path(raw)
    if not p.exists() and "\\" in raw:
        return Path(raw.replace("\\", ""))
    return p


def compare(a: dict, b: dict) -> dict:
    """Compare two structure dumps: heading-level sequence (the layout skeleton), table count/dims,
    per-section header/footer. Returns {match, diffs[]} — text of headings is compared too, but a
    mismatch is reported as 'changed' (expected: new content) not as a structural break; only the
    LEVEL sequence and the table/section skeleton must match."""
    diffs = []
    # Layout skeleton = reference heading LEVELS appearing IN ORDER as a subsequence of the output
    # levels. Extra headings (deeper subsections, new chapters) are legitimate new content and do
    # NOT break the match; a flattened chapter (ref level 2 missing from out) does.
    la = [h[0] for h in a["headings"]]
    lb = [h[0] for h in b["headings"]]
    it = iter(lb)
    missing = [x for x in la if not any(y == x for y in it)]
    if missing:
        diffs.append(f"reference heading levels missing from output (in order): {missing} — ref={la} out={lb}")
    ta, tb = a["tables"], b["tables"]
    if len(ta) != len(tb):
        diffs.append(f"table count differs: ref={len(ta)} out={len(tb)}")
    for i, (ra, rb) in enumerate(zip(ta, tb)):
        if ra != rb:
            diffs.append(f"table {i} dims differ: ref={ra} out={rb}")
    # docx_fill keeps only the LAST section setup, so compare the last sections' header/footer;
    # a different section COUNT is a declared limitation, not a structural break.
    if a["sections"] and b["sections"]:
        sa, sb = a["sections"][-1], b["sections"][-1]
        if sa["header"] != sb["header"] or sa["footer"] != sb["footer"]:
            diffs.append(f"last section header/footer differs: ref={sa['header']!r}/{sa['footer']!r} out={sb['header']!r}/{sb['footer']!r}")
    changed = [f"'{t}' -> '{u}'" for (_, t, _), (_, u, _) in zip(a["headings"], b["headings"]) if t != u]
    return {"match": not diffs, "structural_diffs": diffs, "changed_headings": changed}


def main() -> int:
    ap = argparse.ArgumentParser(description="Dump the structure of a .docx as Markdown (headings, tables, headers/footers).")
    ap.add_argument("path", help=".docx file to inspect (read-only)")
    ap.add_argument("--compare", metavar="OTHER.docx", help="compare the structure of two .docx files (e.g. reference vs produced)")
    ap.add_argument("-o", "--out", help="write the Markdown to this file instead of stdout (parent dirs are created)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    args = ap.parse_args()

    p = resolve_input(args.path)
    if not p.is_file():
        sys.stderr.write(f"docxstruct.py: not found: {p}\n")
        return 2
    input_paths = [p]
    try:
        s = structure(p)
    except (OSError, ValueError, PackageNotFoundError) as exc:
        sys.stderr.write(f"docxstruct.py: invalid DOCX or I/O error ({type(exc).__name__})\n")
        return 2
    if args.compare:
        other = resolve_input(args.compare)
        if not other.is_file():
            sys.stderr.write(f"docxstruct.py: compare target not found: {other}\n")
            return 2
        input_paths.append(other)
        try:
            other_structure = structure(other)
        except (OSError, ValueError, PackageNotFoundError) as exc:
            sys.stderr.write(f"docxstruct.py: invalid DOCX or I/O error ({type(exc).__name__})\n")
            return 2
        c = compare(s, other_structure)
        if args.json:
            text = json.dumps(c, indent=2, ensure_ascii=False)
        else:
            lines = [f"# Structure compare: `{p.name}` vs `{other.name}`", ""]
            lines.append("MATCH — same layout skeleton (heading levels, tables, sections, headers/footers)" if c["match"] else "STRUCTURAL DIFFS:")
            lines += [f"- {d}" for d in c["structural_diffs"]]
            if c["changed_headings"]:
                lines += ["", "Changed heading texts (expected — new content):"] + [f"- {x}" for x in c["changed_headings"]]
            text = "\n".join(lines) + "\n"
    else:
        text = json.dumps(s, indent=2, ensure_ascii=False) if args.json else to_markdown(s)
    if args.out:
        out_p = Path(args.out)
        same_path = out_p.resolve() in {source.resolve() for source in input_paths}
        same_file = out_p.exists() and any(os.path.samefile(out_p, source) for source in input_paths)
        if same_path or same_file:
            sys.stderr.write("docxstruct.py: output must differ from every input file (inputs are read-only)\n")
            return 2
        out_p.parent.mkdir(parents=True, exist_ok=True)  # /tmp/work & co. may not exist yet
        try:
            out_p.write_text(text, encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"docxstruct.py: cannot write output ({type(exc).__name__})\n")
            return 2
        print(f"docxstruct.py: wrote {args.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
