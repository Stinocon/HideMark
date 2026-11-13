# HideMark — Sensitive data out. Meaning intact.

HideMark 2.2.1 replaces selected identifying values in text and supported documents with typed placeholders. It keeps a mapping on the local machine so a reviewed result can be restored later.

The detection engine uses patterns, curated dictionaries, and validators. It does not send input to a model. An optional suggester can ask a separately configured model on loopback for candidate values; it only proposes matches and never writes mappings.

## What it handles

- A Python command-line engine and a browser UI that share the same detection code.
- Text files, Markdown, CSV, JSON, YAML, and supported Office documents. PDF and other input formats can be converted to Markdown with the optional converter.
- Restoration from the matching mapping file, with placeholders tagged by run so another map cannot silently substitute values.
- Multi-document composition for UTF-8 text and Markdown, with a private manifest that records mapping references without copying original values.
- A stdio MCP interface for checking and anonymizing text.
- Optional loopback model suggestions for values that patterns and dictionaries cannot identify.

Content checks stop at 12 MB per document and fail closed for larger inputs. The web upload limit defaults to 160 MB.

HideMark is not a semantic anonymizer. It cannot infer that “the site near the river” identifies a customer, and a proper name absent from the configured dictionaries may remain. Review every redacted result before sharing it.

## Requirements

- Python 3.12 or later.
- Docker Compose for the container UI. The command-line engine uses the Python standard library.
- Node.js for the UI checks. Pandoc is needed for some document conversion tests.
- The optional converter is pinned with hashes in `requirements-anydoc.txt`.

## Quick start

Get a local copy and enter its directory:

```sh
git clone https://github.com/Stinocon/HideMark.git
cd HideMark
```

The CLI needs Python 3.12 or later and no package installation. Docker Compose is required for
the container UI. For native document conversion, check the optional converter with
`python3 convert.py --doctor`; install its hash-pinned environment with `python3 convert.py --install`.
That installation requires network access; conversion itself runs locally.

Redact a text file and write a tagged mapping under `$HOME/.anon/maps/`:

```sh
python3 anon.py report.txt
```

Build a new `.docx` from Markdown by cloning the structure (styles, headers, footers, TOC field,
section setup) of an existing report — the local-model workflow for producing reports in the
house format. Requires python-docx (e.g. `~/.pi/agent/venvs/docx-venv/bin/python`):

```sh
~/.pi/agent/venvs/docx-venv/bin/python docxfill.py old_report.docx content.md -o new_report.docx
```

Check a file without writing output, then inspect what would change:

```sh
python3 anon.py report.txt --check --json
python3 anon.py report.txt --dry-run
```

Restore a reviewed result with the map created for that run:

```sh
python3 deanon.py final.txt "$HOME/.anon/maps/<map-id>.map.json"
```

Run the web UI in Docker. It publishes only on loopback by default:

```sh
docker compose up -d
# Open http://127.0.0.1:1407
```

For a native UI, run `python3 web/server.py`. The UI has no authentication; keep it bound to loopback unless you have added an appropriate access-control layer.

To compose Markdown documents, provide each redacted file with its map:

```sh
python3 composition.py \
  --source "first.redacted.md=$HOME/.anon/maps/<first-id>.map.json" \
  --source "second.redacted.md=$HOME/.anon/maps/<second-id>.map.json" \
  --output report.md
```

The command creates a manifest under `$HOME/.anon/compositions/`. Keep the manifest with the maps. Restore the composition with:

```sh
python3 deanon.py report.md --manifest "$HOME/.anon/compositions/<id>.composition.json"
```

Composition supports whole UTF-8 text and Markdown files. It does not compose PDF or Office documents, select fragments, or infer that equal values in different maps belong to the same person.

## Mappings and local data

Mappings contain the original matched values and are required for restoration. They are written outside the checkout under `$HOME/.anon/maps/` with restrictive file permissions. The local store is **not encrypted at rest**; any process with access to it can read the values. The optional backup command encrypts and verifies an archive, but it does not schedule backups or manage the passphrase.

Create and verify an encrypted backup before moving or replacing the private store:

```sh
make backup BACKUP="$HOME/hidemark-backup.tar.age"
```

Restore it with `make restore FILE=...`; the command asks for the backup passphrase and refuses to
overwrite existing files unless `FORCE=1` is explicitly supplied. Keep backups and passphrases
separately from the repository.

Do not commit mappings, manifests, real input files, or redacted output that may still contain identifying context. `.gitignore` excludes common local data patterns, but it is not a security boundary.

## Configuration

- `ANON_HOME` selects the private data directory; the default is `$HOME/.anon`.
- `--entities PATH` supplies custom dictionaries. The default catalogs live in `catalogs/`.
- `ANON_MAX_UPLOAD_BYTES` sets the web upload limit.
- `ANON_CONVERTER_PYTHON` selects a converter interpreter. Without it, the pinned environment is created under `$XDG_CACHE_HOME/hidemark/venvs/` or `~/.cache/hidemark/venvs/`.
- The optional suggester accepts only loopback endpoints. Configure its URL and model explicitly; it sends submitted text to that local endpoint.
- The optional Compose model image is pinned by immutable OCI digest. Its model cache defaults to `$HOME/.hidemark/models`; set `ANON_MODEL_DIR` to use another directory.
- `docker-compose.yml` binds the UI to `127.0.0.1:1407`. Do not expose the port directly to a network: the UI has no authentication.

See [SECURITY.md](SECURITY.md) for the threat model and [docs/DESIGN.md](docs/DESIGN.md) for the data flow and limits.

## Tests and development

```sh
make test
make coverage
make mutate
make smoke
```

`make test` runs the unit and integration checks. `make coverage` measures exercised engine code, `make mutate` checks that selected defects are caught by named tests, and `make smoke` builds and exercises the container. The smoke test requires Docker; some document conversion checks require Pandoc and the pinned converter runtime.

The UI supports Italian and English. Repository documentation and code comments are maintained in English.

## Companion tools

- `convert.py` — documents (PDF/DOCX/…) → Markdown, hash-pinned local runtime.
- `docxfill.py` — Markdown → DOCX cloning a reference `.docx` structure (python-docx; tests in `tests/test_docxfill.py`, run with the docx interpreter).
- `docxstruct.py` — dump the STRUCTURE of a `.docx` (headings/styles/table dims/headers/footers, no body prose) as Markdown or JSON; read-only (tests in `tests/test_docxstruct.py`).

## License and contributions

HideMark is distributed under the MIT License; see [LICENSE](LICENSE). Third-party dependency details are in [NOTICE.md](NOTICE.md). Bug reports and focused pull requests are welcome. Please do not include real documents, mappings, credentials, or private customer data in reports.
