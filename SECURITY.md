# Security policy

## Reporting

Report suspected vulnerabilities through a private GitHub security advisory for this repository. Do not include real documents, mapping files, credentials, or personal data in a public issue.

## Security model

HideMark is a local tool for reducing identifying details before text is shared. It is not a guarantee of anonymity or a substitute for human review.

- The core detection and restoration commands use local files. The deterministic engine does not make network requests.
- The optional suggestion client can send submitted text to a configured model endpoint. It accepts loopback addresses only; use it only with a model running on the same machine.
- The web UI has no authentication. The Compose configuration binds its port to loopback. Changing that binding exposes uploaded documents and dictionary data to the reachable network.
- Mapping files contain the original values and are stored outside the repository under `$HOME/.anon/`. They are permission-restricted but are not encrypted at rest.
- Composition manifests contain fingerprints and references to mappings, not the original values. Both manifests and mappings must remain private because they are needed for restoration.
- The optional backup script encrypts an archive and verifies its contents. It does not schedule backups or manage the passphrase.
- Detection relies on patterns and configured dictionaries. Contextual references, screenshots, and names missing from the dictionaries may not be caught. Inspect the output before sharing it.

The engine refuses to scan documents larger than 12 MB. The web server limits uploads to 160 MB by default. Converter output is capped at 160 MB and conversion runs for at most 300 seconds. The API rate limit defaults to 120 requests per minute; these limits can be configured.

Placeholder tags use 6 hexadecimal digits and widen if a collision is found among visible maps. Tags reduce accidental cross-map restoration; they are not cryptographic authentication.

## Supported versions

The current `main` branch is supported. No older release receives security updates unless a release note says otherwise.
