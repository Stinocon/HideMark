#!/usr/bin/env python3
"""Compose text documents with explicit, verifiable links to their source maps.

Composition manifests contain mapping references and fingerprints, never original values. They
live in the private HideMark store and do not grant access to the maps they name.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import anon
import deanon

SCHEMA = "anon-composition/1"
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".log"}
MAX_DOCUMENT_BYTES = 160 * 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024 * 1024
MAX_MAPPING_TOTAL_BYTES = 64 * 1024 * 1024
MAX_SOURCES = 32
MAX_MANIFEST_RECORDS = 4096
MAX_MANIFEST_BINDINGS = 100_000
MAP_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,255}\Z")
SOURCE_ID_RE = re.compile(r"(?:src|derived)_[0-9a-f]{16}\Z")
COMPOSITION_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SUSPICIOUS_TOKEN_RE = re.compile(r"\[[A-Z][A-Z0-9_]*-\d+[^\]\r\n]*\]")
MANIFEST_DIR = anon.ANON_HOME / "compositions"


class DuplicateJSONKey(ValueError):
    """Duplicate object keys are rejected without echoing an untrusted key."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_path(path: Path, label: str) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"cannot resolve {label}") from exc


def _write_temporary(path: Path, data: bytes, private_parent: bool = False) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if private_parent:
        os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.tmp-{os.urandom(8).hex()}")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _publish_noreplace(temporary: Path, destination: Path) -> None:
    """Atomically publish a same-directory file without replacing a concurrent winner."""
    try:
        # A hard link is an atomic create-if-absent operation on the same filesystem. Unlike
        # exists()+replace(), it cannot clobber a file another process published in the meantime.
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise ValueError("refusing to overwrite an existing composition artifact") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _publish_bytes(path: Path, data: bytes, private_parent: bool = False) -> None:
    temporary = _write_temporary(path, data, private_parent)
    _publish_noreplace(temporary, path)


def _read_limited(path: Path, limit: int, label: str, limit_message: str | None = None) -> bytes:
    """Read at most limit+1 bytes from one opened file, detecting growth during the read."""
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise ValueError(f"cannot read {label}") from exc
    if len(raw) > limit:
        raise ValueError(limit_message or f"{label} exceeds the permitted size limit")
    return raw


def _parse_json(raw: bytes, path: Path) -> dict:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise DuplicateJSONKey
            result[key] = value
        return result
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError(f"composition metadata exceeds the size limit: {path.name}")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except DuplicateJSONKey as exc:
        raise ValueError(f"duplicate JSON keys in metadata file: {path.name}") from exc
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError(f"cannot read valid composition metadata: {path.name}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"composition metadata is not an object: {path.name}")
    return data


def _read_json(path: Path) -> dict:
    return _parse_json(_read_limited(path, MAX_METADATA_BYTES, "composition metadata"), path)


def _map_record(path: Path, max_bytes: int = MAX_METADATA_BYTES) -> dict:
    path = _resolve_path(path, "source mapping")
    try:
        path.relative_to(_resolve_path(anon.DEFAULT_MAPS, "private map directory"))
    except ValueError as exc:
        raise ValueError("source maps must be stored in the private HideMark map directory") from exc
    limit = min(MAX_METADATA_BYTES, max_bytes)
    limit_message = (
        "required mapping files exceed the 64 MB composition limit"
        if max_bytes < MAX_METADATA_BYTES else None
    )
    raw_bytes = _read_limited(path, limit, "source mapping", limit_message)
    raw = _parse_json(raw_bytes, path)
    try:
        entries = deanon._validate_entries(path, raw.get("entries"))
    except ValueError as exc:
        # Existing map validation messages may include the rejected key. A malformed map key can
        # itself contain a value, so keep the composition boundary value-free.
        raise ValueError("source map entries are malformed") from exc
    if "version" in raw and not isinstance(raw["version"], str):
        raise ValueError(f"mapping version is malformed: {path.name}")
    if "schema" in raw and not isinstance(raw["schema"], str):
        raise ValueError(f"mapping schema is malformed: {path.name}")
    map_id = raw.get("id")
    if not isinstance(map_id, str) or not map_id:
        map_id = path.name.removesuffix(".map.json")
    if map_id != path.name.removesuffix(".map.json"):
        raise ValueError(f"mapping id does not match its filename: {path.name}")
    tag = raw.get("tag")
    derived_tag = anon.tag_of(entries)
    if tag is not None and (not isinstance(tag, str) or (derived_tag and tag != derived_tag)):
        raise ValueError(f"mapping tag is inconsistent: {path.name}")
    tag = tag or derived_tag
    for token in entries:
        match = anon.PLACEHOLDER_RE.fullmatch(token)
        token_tag = token[:-1].rsplit("-", 1)[-1] if match and token[:-1].count("-") >= 2 else None
        if not match or (token_tag and tag and token_tag != tag):
            raise ValueError(f"mapping contains an inconsistent placeholder: {path.name}")
    return {
        "id": map_id,
        "version": raw.get("version") if isinstance(raw.get("version"), str) else "legacy",
        "schema": raw.get("schema") if isinstance(raw.get("schema"), str) else None,
        "tag": tag,
        "sha256": sha256_bytes(raw_bytes),
        "size": len(raw_bytes),
        "entries": entries,
        "path": path,
    }


def _validate_manifest(manifest: dict) -> None:
    """Validate every value before it participates in sets, lookups, or path selection."""
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA \
            or not isinstance(manifest.get("composition_id"), str) \
            or not COMPOSITION_ID_RE.fullmatch(manifest["composition_id"]) \
            or not isinstance(manifest.get("engine_version"), str) \
            or not manifest["engine_version"] \
            or not isinstance(manifest.get("created"), str):
        raise ValueError("unsupported or malformed composition manifest")
    output = manifest.get("output")
    sources, maps, bindings = manifest.get("sources"), manifest.get("maps"), manifest.get("bindings")
    if not isinstance(output, dict) or not isinstance(output.get("sha256"), str) \
            or not SHA256_RE.fullmatch(output["sha256"]) \
            or not isinstance(output.get("format"), str) \
            or output["format"] not in {"md", "markdown", "txt"}:
        raise ValueError("composition manifest has no output fingerprint")
    if not isinstance(sources, list) or not sources or not isinstance(maps, list) or not maps \
            or not isinstance(bindings, dict) or not bindings \
            or len(sources) > MAX_MANIFEST_RECORDS or len(maps) > MAX_MANIFEST_RECORDS \
            or len(bindings) > MAX_MANIFEST_BINDINGS:
        raise ValueError("composition manifest has malformed dependencies")
    verification = manifest.get("verification")
    if not isinstance(verification, dict) or verification.get("complete") is not True \
            or type(verification.get("unresolved")) is not int or verification["unresolved"] != 0 \
            or type(verification.get("unknown")) is not int or verification["unknown"] != 0:
        raise ValueError("composition manifest is not marked complete")
    source_ids: set[str] = set()
    for item in sources:
        if not isinstance(item, dict) or not isinstance(item.get("source_id"), str) \
                or not SOURCE_ID_RE.fullmatch(item["source_id"]) \
                or not isinstance(item.get("sha256"), str) \
                or not SHA256_RE.fullmatch(item["sha256"]) \
                or not isinstance(item.get("role"), str) \
                or item["role"] not in {"base", "source"} \
                or not isinstance(item.get("format"), str) \
                or not isinstance(item.get("origin_role", item.get("role")), str) \
                or item.get("origin_role", item.get("role")) not in {"base", "source"} \
                or not isinstance(item.get("map_ids"), list) \
                or not item["map_ids"] \
                or any(not isinstance(map_id, str) or not MAP_ID_RE.fullmatch(map_id)
                       for map_id in item["map_ids"]) \
                or len(set(item["map_ids"])) != len(item["map_ids"]):
            raise ValueError("composition manifest has malformed source provenance")
        parent_id = item.get("parent_composition_id")
        if parent_id is not None and (not isinstance(parent_id, str) or not COMPOSITION_ID_RE.fullmatch(parent_id)):
            raise ValueError("composition manifest has malformed source provenance")
        if item["source_id"] in source_ids:
            raise ValueError("composition manifest has duplicate source identifiers")
        source_ids.add(item["source_id"])
    if sum(item["role"] == "base" for item in sources) != 1:
        raise ValueError("composition manifest has malformed source provenance")
    map_ids: set[str] = set()
    for item in maps:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("id", "version", "sha256")) \
                or not MAP_ID_RE.fullmatch(item["id"]) or not item["version"] \
                or not SHA256_RE.fullmatch(item["sha256"]) \
                or (item.get("schema") is not None and not isinstance(item.get("schema"), str)) \
                or (item.get("tag") is not None and not isinstance(item.get("tag"), str)):
            raise ValueError("composition manifest has a malformed mapping reference")
        if item["id"] in map_ids:
            raise ValueError("composition manifest has duplicate mapping references")
        map_ids.add(item["id"])
    for item in sources:
        if any(map_id not in map_ids for map_id in item["map_ids"]):
            raise ValueError("composition manifest source refers to an unknown mapping")
    audit = manifest.get("audit")
    if not isinstance(audit, dict) or audit.get("clean") is not True \
            or not isinstance(audit.get("dictionary_sha256"), list) \
            or len(audit["dictionary_sha256"]) > MAX_MANIFEST_RECORDS \
            or any(not isinstance(value, str) or not SHA256_RE.fullmatch(value)
                   for value in audit["dictionary_sha256"]) \
            or audit.get("built_in_patterns") is not True:
        raise ValueError("composition manifest has no clean source-audit record")
    output_tag = manifest.get("output_tag")
    if not isinstance(output_tag, str) or not re.fullmatch(r"[0-9a-f]{4,8}", output_tag):
        raise ValueError("composition manifest has an invalid output tag")
    for token, binding in bindings.items():
        if not isinstance(token, str) or not anon.PLACEHOLDER_RE.fullmatch(token) or not isinstance(binding, dict):
            raise ValueError("composition manifest has a malformed placeholder binding")
        token_tag = token[:-1].rsplit("-", 1)[-1] if token[:-1].count("-") >= 2 else None
        if token_tag != output_tag:
            raise ValueError("composition manifest placeholder is outside its output namespace")
        binding_map_id, entry = binding.get("map_id"), binding.get("entry")
        if not isinstance(binding_map_id, str) or binding_map_id not in map_ids \
                or not isinstance(entry, str) or not anon.PLACEHOLDER_RE.fullmatch(entry):
            raise ValueError("composition manifest has an unresolved mapping dependency")
        binding_sources = binding.get("source_ids")
        if not isinstance(binding_sources, list) or not binding_sources \
                or any(not isinstance(source_id, str) for source_id in binding_sources):
            raise ValueError("composition manifest has no source provenance for a placeholder")
        if any(source_id not in source_ids for source_id in binding_sources):
            raise ValueError("composition manifest placeholder refers to an unknown source")
        if len(set(binding_sources)) != len(binding_sources):
            raise ValueError("composition manifest placeholder repeats a source reference")
    parents = manifest.get("parents")
    if not isinstance(parents, list) or len(parents) > MAX_SOURCES or any(
        not isinstance(parent, dict)
        or not isinstance(parent.get("composition_id"), str)
        or not COMPOSITION_ID_RE.fullmatch(parent["composition_id"])
        or not isinstance(parent.get("manifest_sha256"), str)
        or not SHA256_RE.fullmatch(parent["manifest_sha256"])
        for parent in parents
    ):
        raise ValueError("composition manifest has malformed parent references")
    fragments = manifest.get("fragments")
    if not isinstance(fragments, list) or len(fragments) > MAX_SOURCES * 2:
        raise ValueError("composition manifest has malformed fragment provenance")
    for fragment in fragments:
        if not isinstance(fragment, dict) or not isinstance(fragment.get("source_id"), str) \
                or fragment["source_id"] not in source_ids \
                or type(fragment.get("start")) is not int or fragment["start"] < 0 \
                or type(fragment.get("length")) is not int or fragment["length"] < 0:
            raise ValueError("composition manifest has malformed fragment provenance")


def _find_map(map_ref: dict, maps_dir: Path, max_bytes: int = MAX_METADATA_BYTES) -> dict:
    matches = []
    for path in maps_dir.glob("*.map.json"):
        if path.name.removesuffix(".map.json") == map_ref["id"]:
            matches.append(path)
    if len(matches) != 1:
        raise ValueError(f"required mapping is missing or ambiguous: {map_ref['id']}")
    record = _map_record(matches[0], max_bytes)
    for key in ("id", "version", "tag", "sha256"):
        if record.get(key) != map_ref.get(key):
            raise ValueError(f"required mapping does not match its recorded {key}: {map_ref['id']}")
    return record


def _read_text_source(path: Path) -> tuple[str, str, int]:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError(f"unsupported composition format: {path.suffix or 'no extension'}")
    try:
        raw = _read_limited(path, MAX_DOCUMENT_BYTES, "source document")
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"source is not readable UTF-8 text: {path.name}") from exc
    if not raw:
        raise ValueError(f"source is empty: {path.name}")
    if "\x00" in text:
        raise ValueError(f"binary content is not supported: {path.name}")
    return text, sha256_bytes(raw), len(raw)


def _new_output_tag() -> str:
    taken: set[str] = set()
    for path in anon.DEFAULT_MAPS.glob("*.map.json") if anon.DEFAULT_MAPS.exists() else ():
        record = _map_record(path)
        if record["tag"]:
            taken.add(record["tag"])
    for path in MANIFEST_DIR.glob("*.composition.json") if MANIFEST_DIR.exists() else ():
        item = _read_json(path)
        _validate_manifest(item)
        tag = item.get("output_tag")
        if isinstance(tag, str):
            taken.add(tag)
    return anon.new_tag(taken)


def compose(
    inputs: list[tuple[Path, Path]],
    parents: list[tuple[Path, Path]],
    output: Path,
    manifest_path: Path | None = None,
    entity_paths: list[Path] | None = None,
) -> tuple[Path, dict]:
    """Compose mapped text inputs and already composed text inputs into one Markdown file."""
    if not inputs and not parents:
        raise ValueError("at least one --source or --parent is required")
    if len(inputs) + len(parents) > MAX_SOURCES:
        raise ValueError(f"composition accepts at most {MAX_SOURCES} source documents")
    if output.suffix.lower() not in {".md", ".markdown", ".txt"}:
        raise ValueError("composition output must be .md, .markdown or .txt")
    output = _resolve_path(output, "output")
    manifest_path = manifest_path or (MANIFEST_DIR / f"{os.urandom(12).hex()}.composition.json")
    manifest_path = _resolve_path(manifest_path, "manifest")
    if manifest_path == output:
        raise ValueError("output and manifest must be different files")
    try:
        manifest_path.relative_to(_resolve_path(anon.ANON_HOME, "private store"))
    except ValueError as exc:
        raise ValueError("composition manifests must stay inside the private HideMark store") from exc
    if manifest_path.exists():
        raise ValueError("refusing to overwrite an existing manifest")
    if output.exists():
        raise ValueError("refusing to overwrite an existing output")

    map_records: dict[str, dict] = {}
    source_records: dict[str, dict] = {}
    parent_refs: list[dict] = []
    fragments: list[tuple[str, str, dict[str, dict]]] = []
    identity_sources: dict[tuple[str, str], set[str]] = {}
    total_input_bytes = 0
    total_mapping_bytes = 0
    dictionaries = [path for path in anon.DEFAULT_DICTIONARIES if path.is_file()]
    for path in entity_paths or []:
        path = path.expanduser()
        if not path.is_file():
            raise ValueError(f"entity dictionary is not a file: {path.name}")
        dictionaries.append(path)
    # Dictionary diagnostics can echo the declared values. Keep them out of terminal output and
    # logs; callers receive only a source index and a rule-count-free failure.
    dictionary_hashes = sorted({sha256_file(path) for path in dictionaries})
    with contextlib.redirect_stderr(io.StringIO()):
        entities = anon.load_entities_many(dictionaries)
    if dictionary_hashes != sorted({sha256_file(path) for path in dictionaries}):
        raise ValueError("an entity dictionary changed during source validation")

    def audit_content(text: str, index: int) -> None:
        if anon.detect(text, entities):
            raise ValueError(f"source {index} still matches configured sensitive-data rules")

    def add_map(record: dict) -> None:
        nonlocal total_mapping_bytes
        current = map_records.get(record["id"])
        if current and current["sha256"] != record["sha256"]:
            raise ValueError(f"mapping id refers to different map files: {record['id']}")
        if not current:
            total_mapping_bytes += record["size"]
            if total_mapping_bytes > MAX_MAPPING_TOTAL_BYTES:
                raise ValueError("required mapping files exceed the 64 MB composition limit")
        map_records[record["id"]] = record

    def load_map(path: Path) -> dict:
        existing = map_records.get(path.name.removesuffix(".map.json"))
        if existing is not None:
            return existing
        record = _map_record(path, MAX_MAPPING_TOTAL_BYTES - total_mapping_bytes)
        add_map(record)
        return record

    def load_parent_map(map_ref: dict) -> dict:
        existing = map_records.get(map_ref["id"])
        if existing is not None:
            for key in ("id", "version", "tag", "sha256"):
                if existing.get(key) != map_ref.get(key):
                    raise ValueError("required mapping does not match its recorded provenance")
            return existing
        record = _find_map(
            map_ref, anon.DEFAULT_MAPS, MAX_MAPPING_TOTAL_BYTES - total_mapping_bytes
        )
        add_map(record)
        return record

    for index, (document, map_path) in enumerate(inputs, 1):
        document, map_path = _resolve_path(document, "source document"), _resolve_path(map_path, "source mapping")
        text, fingerprint, source_size = _read_text_source(document)
        total_input_bytes += source_size
        if total_input_bytes > MAX_DOCUMENT_BYTES:
            raise ValueError("combined sources exceed the 160 MB composition limit")
        audit_content(text, index)
        record = load_map(map_path)
        source_id = f"src_{os.urandom(8).hex()}"
        tokens = list(SUSPICIOUS_TOKEN_RE.finditer(text))
        if not tokens:
            raise ValueError(f"no placeholder proves the source-to-map association: source {index}")
        for match in tokens:
            token = match.group(0)
            if token not in record["entries"] or record["entries"][token].get("original") is None:
                raise ValueError(f"source {index} has an unknown, altered or non-restorable placeholder")
            identity_sources.setdefault((record["id"], token), set()).add(source_id)
        source_records[source_id] = {
            "role": "base" if index == 1 and not parents else "source",
            "sha256": fingerprint,
            "format": document.suffix.lower().lstrip("."),
            "map_ids": [record["id"]],
        }
        fragments.append((source_id, text, {token: {"map_id": record["id"], "entry": token} for token in record["entries"]}))

    for index, (document, parent_path) in enumerate(parents, 1):
        document, parent_path = _resolve_path(document, "parent document"), _resolve_path(parent_path, "parent manifest")
        try:
            parent_path.relative_to(_resolve_path(anon.ANON_HOME, "private store"))
        except ValueError as exc:
            raise ValueError("parent manifests must stay inside the private HideMark store") from exc
        text, fingerprint, source_size = _read_text_source(document)
        total_input_bytes += source_size
        if total_input_bytes > MAX_DOCUMENT_BYTES:
            raise ValueError("combined sources exceed the 160 MB composition limit")
        audit_content(text, index)
        parent_bytes = _read_limited(parent_path, MAX_METADATA_BYTES, "parent composition metadata")
        parent = _parse_json(parent_bytes, parent_path)
        _validate_manifest(parent)
        if parent["output"].get("sha256") != fingerprint:
            raise ValueError(f"parent composition output fingerprint mismatch: {parent_path.name}")
        parent_bindings = parent["bindings"]
        tokens = list(SUSPICIOUS_TOKEN_RE.finditer(text))
        if not tokens:
            raise ValueError(f"no placeholder is bound by the parent manifest: {parent_path.name}")
        inherited_maps = {item["id"]: item for item in parent["maps"]}
        for map_ref in inherited_maps.values():
            load_parent_map(map_ref)
        parent_source_id = f"derived_{os.urandom(8).hex()}"
        source_records[parent_source_id] = {
            "role": "base" if index == 1 else "source",
            "sha256": fingerprint,
            "format": document.suffix.lower().lstrip("."),
            "parent_composition_id": parent["composition_id"],
            "map_ids": sorted(inherited_maps),
        }
        for match in tokens:
            token = match.group(0)
            binding = parent_bindings.get(token)
            if not binding or binding.get("map_id") not in inherited_maps:
                raise ValueError("parent document contains an unknown or unresolved placeholder")
            identity_sources.setdefault((binding["map_id"], binding["entry"]), set()).update(binding["source_ids"])
        fragments.append((parent_source_id, text, parent_bindings))
        parent_refs.append({"composition_id": parent["composition_id"], "manifest_sha256": sha256_bytes(parent_bytes)})
        for item in parent["sources"]:
            inherited = dict(item)
            inherited["origin_role"] = item.get("origin_role", item.get("role"))
            inherited["role"] = "source"
            source_records.setdefault(item["source_id"], inherited)

    tag = _new_output_tag()
    counters: dict[str, int] = {}
    key_to_token: dict[tuple[str, str], str] = {}
    bindings: dict[str, dict] = {}
    rendered: list[str] = []
    output_sources: list[dict] = []
    source_offset = 0
    fragments.sort(key=lambda item: source_records[item[0]]["role"] != "base")
    for source_id, text, token_bindings in fragments:
        def replace(match: re.Match) -> str:
            token = match.group(0)
            binding = token_bindings.get(token)
            if not binding:
                raise ValueError("source contains a placeholder without verified mapping provenance")
            key = (binding["map_id"], binding["entry"])
            output_token = key_to_token.get(key)
            if output_token is None:
                record = map_records.get(binding["map_id"])
                if not record or binding["entry"] not in record["entries"]:
                    raise ValueError("source placeholder refers to a missing mapping entry")
                entry = record["entries"][binding["entry"]]
                if entry.get("original") is None:
                    raise ValueError("source placeholder is not restorable")
                kind = binding["entry"][1:].split("-", 1)[0]
                counters[kind] = counters.get(kind, 0) + 1
                output_token = f"[{kind}-{counters[kind]}-{tag}]"
                key_to_token[key] = output_token
                prior_sources = sorted(identity_sources.get(key, {source_id}))
                bindings[output_token] = {
                    "map_id": binding["map_id"],
                    "entry": binding["entry"],
                    "source_ids": prior_sources,
                }
            return output_token

        transformed = SUSPICIOUS_TOKEN_RE.sub(replace, text)
        if transformed == text and SUSPICIOUS_TOKEN_RE.search(text):
            raise ValueError("source placeholder transformation failed")
        rendered.append(transformed.rstrip("\n"))
        output_sources.append({"source_id": source_id, "start": source_offset, "length": len(transformed.rstrip("\n"))})
        source_offset += len(transformed.rstrip("\n")) + len("\n\n---\n\n")

    content = "\n\n---\n\n".join(rendered) + "\n"
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_DOCUMENT_BYTES:
        raise ValueError("composed output exceeds the 160 MB size limit")
    # Verify the generated token set before publishing either artifact.
    found = [match.group(0) for match in anon.PLACEHOLDER_RE.finditer(content)]
    if not found or any(token not in bindings for token in found):
        raise ValueError("generated output contains an unresolved or unknown placeholder")
    map_refs = [{key: record[key] for key in ("id", "version", "schema", "tag", "sha256")} for record in map_records.values()]
    composition_id = os.urandom(16).hex()
    manifest = {
        "schema": SCHEMA,
        "composition_id": composition_id,
        "engine_version": anon.VERSION,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "output_tag": tag,
        "sources": [dict(value, source_id=key) for key, value in source_records.items()],
        "fragments": output_sources,
        "maps": sorted(map_refs, key=lambda item: item["id"]),
        "bindings": bindings,
        "parents": parent_refs,
        "output": {"format": output.suffix.lower().lstrip("."), "sha256": sha256_bytes(content_bytes)},
        "audit": {"clean": True, "dictionary_sha256": dictionary_hashes,
                  "built_in_patterns": True},
        "verification": {"complete": True, "unresolved": 0, "unknown": 0},
    }
    _validate_manifest(manifest)
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    # The output is published first; the manifest is the logical commit record and is published
    # last. A crash between the two can leave an orphan output, but never a complete manifest for
    # an output that was not published. Orphan outputs are intentionally left for operator review.
    _publish_bytes(output, content_bytes)
    _publish_bytes(manifest_path, encoded, private_parent=True)
    return manifest_path, manifest


def restore(document: Path, manifest_path: Path, output: Path, maps_dir: Path | None = None) -> dict:
    """Restore a composed UTF-8 text document after validating every dependency."""
    document, manifest_path = _resolve_path(document, "composed document"), _resolve_path(manifest_path, "composition manifest")
    maps_dir = maps_dir or anon.DEFAULT_MAPS
    manifest = _read_json(manifest_path)
    _validate_manifest(manifest)
    raw = _read_limited(document, MAX_DOCUMENT_BYTES, "composed document")
    if sha256_bytes(raw) != manifest["output"]["sha256"]:
        raise ValueError("composed document fingerprint mismatch")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("composed document is not UTF-8 text") from exc

    records: dict[str, dict] = {}
    total_mapping_bytes = 0
    for map_ref in manifest["maps"]:
        record = _find_map(
            map_ref, maps_dir, MAX_MAPPING_TOTAL_BYTES - total_mapping_bytes
        )
        total_mapping_bytes += record["size"]
        if total_mapping_bytes > MAX_MAPPING_TOTAL_BYTES:
            raise ValueError("required mapping files exceed the 64 MB composition limit")
        records[record["id"]] = record
    resolutions: dict[str, str] = {}
    for token, binding in manifest["bindings"].items():
        record = records[binding["map_id"]]
        entry = record["entries"].get(binding["entry"])
        if entry is None or entry.get("original") is None:
            raise ValueError("composition refers to a missing or non-restorable mapping entry")
        if token in resolutions:
            raise ValueError("composition has conflicting placeholder resolutions")
        resolutions[token] = entry["original"]

    # Replace tokens in one pass. A restored original is never scanned again as a replacement key.
    replaced = 0
    def resolve(match: re.Match) -> str:
        nonlocal replaced
        token = match.group(0)
        if token not in resolutions:
            return token
        replaced += 1
        return resolutions[token]

    restored = anon.PLACEHOLDER_RE.sub(resolve, text)
    remaining = list(SUSPICIOUS_TOKEN_RE.finditer(restored))
    if not replaced or remaining:
        raise ValueError("restoration is incomplete; output was not written")
    if output.exists():
        raise ValueError("refusing to overwrite an existing restoration output")
    _publish_bytes(output, restored.encode("utf-8"))
    return {"schema": SCHEMA, "format": "text", "replaced": replaced, "complete": True,
            "composition_id": manifest["composition_id"], "output": str(output)}


def _pairs(values: list[str], label: str) -> list[tuple[Path, Path]]:
    result = []
    for value in values:
        if "=" not in value:
            raise ValueError(f"{label} must be DOCUMENT=MAP_OR_MANIFEST")
        document, metadata = value.split("=", 1)
        if not document or not metadata:
            raise ValueError(f"{label} must be DOCUMENT=MAP_OR_MANIFEST")
        result.append((Path(document).expanduser(), Path(metadata).expanduser()))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compose mapped text documents with a private provenance manifest.")
    parser.add_argument("--source", action="append", default=[], metavar="DOCUMENT=MAP", help="mapped UTF-8 text input; repeatable")
    parser.add_argument("--parent", action="append", default=[], metavar="DOCUMENT=MANIFEST", help="previous composition and its manifest; repeatable")
    parser.add_argument("--output", required=True, help="new .md or .txt output; existing files are never overwritten")
    parser.add_argument("--manifest", help="private manifest path (default: ~/.anon/compositions/<id>.composition.json)")
    parser.add_argument("--entities", action="append", default=[], help="extra anonymization dictionary used to audit sources; repeatable")
    args = parser.parse_args(argv)
    try:
        manifest_path, manifest = compose(
            _pairs(args.source, "--source"), _pairs(args.parent, "--parent"),
            Path(args.output).expanduser(), Path(args.manifest).expanduser() if args.manifest else None,
            [Path(value) for value in args.entities],
        )
    except (OSError, ValueError, KeyError, TypeError, RecursionError, OverflowError) as exc:
        print(f"compose: refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"composition_id": manifest["composition_id"], "output": args.output,
                      "manifest": str(manifest_path), "placeholders": len(manifest["bindings"]),
                      "complete": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
