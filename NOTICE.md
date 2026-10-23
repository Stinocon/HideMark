# Third-party notices

HideMark is licensed under the MIT License; see [LICENSE](LICENSE).

## Runtime dependency

- **firecrawl-anydoc 0.2.3** — [source](https://github.com/firecrawl/anydoc), MIT License. The optional document converter is installed from `requirements-anydoc.txt` with hashes. It is used to extract text locally; do not provide it with credentials.

## Test dependency

- **Pandoc** — [project site](https://pandoc.org), GPL-2.0-or-later. Some tests use it to create and inspect Office document fixtures. It is not included in the runtime image.

The engine itself uses the Python standard library. No third-party source code is vendored in this repository.
