# Design and data flow

HideMark has one detection engine shared by the CLI, web UI, and MCP interface. `anon.py` detects configured values and replaces them with typed placeholders. `deanon.py` restores those placeholders from the matching private map. The web server calls the same engine rather than implementing a second set of detection rules.

## Detection

The engine combines regular expressions, checksum checks for supported identifiers, and text dictionaries. The default catalogs are editable starting points. Names and contextual clues not represented by those patterns or catalogs can remain in the output, so review is required.

The optional suggester is separate from the engine. It asks a loopback model for candidate strings, checks whether each candidate occurs in the submitted text, and returns proposals. It does not write mappings or decide what to redact.

## Maps and restoration

A map stores each placeholder's type and exact original text. It is written under `$HOME/.anon/maps/`, outside the repository. File permissions reduce access by other local users, but the map is plaintext and readable by processes that can access the store.

Placeholders carry a run tag of 6 hexadecimal digits, widened if needed to avoid a collision among visible maps. The original matched text is restored exactly; this does not make an inadequately redacted document anonymous.

Composition accepts whole UTF-8 text and Markdown files together with their maps. It validates source and map fingerprints and writes a separate manifest under `$HOME/.anon/compositions/`. The manifest records references and bindings but does not contain original values. It does not merge identities across maps or support Office/PDF composition.

## Interfaces

- `anon.py` and `deanon.py` provide command-line anonymization and restoration.
- `web/server.py` serves the browser interface and document endpoints.
- `mcp_anon.py` exposes text checks and anonymization over stdio.
- `convert.py` handles supported document extraction; `pdfout.py` creates plain PDF output.
- `suggest.py` is the optional loopback-only model client.

## Runtime boundaries

The default container binds to `127.0.0.1` and mounts the private store as `/data`. The UI has no authentication. The store is not encrypted at rest. The optional converter and model are separate from the core detection engine. These boundaries are configuration and operational requirements, not a claim that the application prevents every disclosure.
