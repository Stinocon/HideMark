# Changelog

## Unreleased

- `docxstruct.py`: read-only structure dump of a `.docx` (chapter hierarchy with styles, table
  dimensions, per-section headers/footers, styles used — body prose excluded by design) as Markdown
  or JSON. Companion to `docxfill.py` for the local-model document pipeline (Pi tool `docx_structure`).
  Tests: `tests/test_docxstruct.py`.

- `docxfill.py`: Markdown → DOCX writer that clones the structure (styles, headers, footers, TOC
  field, section setup) of a reference `.docx` and sets `w:updateFields` so Word refreshes the TOC
  on open. python-docx only, local. Tests: `tests/test_docxfill.py` (wired into `make test` when the
  docx interpreter is present). Used by Pi's `docx_fill` tool for the local-model report workflow.

## Initial HideMark release

This release packages the current application as HideMark. It includes the command-line engine, browser UI, local mapping and restoration workflow, text composition, optional document conversion, MCP interface, optional loopback model suggestions, tests, and container setup.

The release keeps the existing `anon.py` and `deanon.py` commands and the `$HOME/.anon` data directory for compatibility. It does not include private project notes, machine-specific measurements, or local integration scripts.
