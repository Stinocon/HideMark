#!/usr/bin/env python3
"""Synthetic tests for manifest-backed, multi-map composition and restoration."""
from __future__ import annotations

import atexit
import copy
import hashlib
import json
import os
import shutil
import sys
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_old_anon_home = os.environ.get("ANON_HOME")
_test_import_home = Path(tempfile.mkdtemp(prefix="anon-composition-import-"))
os.environ["ANON_HOME"] = str(_test_import_home)
import anon
import composition
if _old_anon_home is None:
    os.environ.pop("ANON_HOME", None)
else:
    os.environ["ANON_HOME"] = _old_anon_home
atexit.register(shutil.rmtree, _test_import_home, ignore_errors=True)


def _read_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class CompositionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="anon-composition-test-"))
        self.home = self.tmp / "private"
        self.maps = self.home / "maps"
        self.manifests = self.home / "compositions"
        self.maps.mkdir(parents=True)
        self.manifests.mkdir()
        self.old_home, self.old_maps, self.old_manifest_dir = anon.ANON_HOME, anon.DEFAULT_MAPS, composition.MANIFEST_DIR
        anon.ANON_HOME = self.home
        anon.DEFAULT_MAPS = self.maps
        composition.MANIFEST_DIR = self.manifests

    def tearDown(self) -> None:
        anon.ANON_HOME, anon.DEFAULT_MAPS, composition.MANIFEST_DIR = self.old_home, self.old_maps, self.old_manifest_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_source(self, name: str, tag: str, value: str, key: str = "[HOST-1") -> tuple[Path, Path]:
        token = f"{key}-{tag}]"
        document = self.tmp / name
        document.write_text(f"Finding: {token}\n", encoding="utf-8")
        map_id = name.replace(".", "-")
        mapping = self.maps / f"{map_id}.map.json"
        payload = {
            "id": map_id, "version": anon.VERSION, "schema": anon.SCHEMA, "tag": tag,
            "entries": {token: {"type": "HOST", "original": value}},
        }
        mapping.write_text(json.dumps(payload), encoding="utf-8")
        return document, mapping

    def test_three_maps_with_the_same_placeholder_are_restored_without_cross_mapping(self) -> None:
        # Forced duplicate run tags model pinned tags or a collision. The complete visible source
        # placeholder is identical, but the explicit per-source maps keep the entries distinct.
        sources = [self.make_source(f"source-{i}.md", "a01234", f"host-{i}.example") for i in range(3)]
        hashes = [hashlib.sha256(mapping.read_bytes()).hexdigest() for _, mapping in sources]
        source_hashes = [hashlib.sha256(document.read_bytes()).hexdigest() for document, _ in sources]
        originals = [document.read_bytes() for document, _ in sources]
        output = self.tmp / "joined.md"
        manifest_path, manifest = composition.compose(sources, [], output)

        self.assertEqual(
            {item["sha256"] for item in manifest["sources"]},
            set(source_hashes),
            "the manifest records each original anonymized source fingerprint",
        )

        tokens = list(manifest["bindings"])
        self.assertEqual(len(tokens), 3)
        self.assertEqual(len(set(tokens)), 3)
        self.assertEqual(len(manifest["maps"]), 3)
        self.assertEqual(len({binding["map_id"] for binding in manifest["bindings"].values()}), 3)
        restored = self.tmp / "joined.deanon.md"
        report = composition.restore(output, manifest_path, restored)
        self.assertTrue(report["complete"])
        self.assertEqual(restored.read_text(encoding="utf-8").count("host-"), 3)
        self.assertEqual([document.read_bytes() for document, _ in sources], originals)
        self.assertEqual([hashlib.sha256(mapping.read_bytes()).hexdigest() for _, mapping in sources], hashes)

    def test_different_mapping_entries_with_the_same_value_remain_distinct(self) -> None:
        first = self.make_source("one.md", "b11234", "same.example", "[HOST-1")
        second = self.make_source("two.md", "b21234", "same.example", "[HOST-2")
        output = self.tmp / "joined.md"
        manifest_path, manifest = composition.compose([first, second], [], output)
        self.assertEqual(len(manifest["bindings"]), 2)
        self.assertEqual(len({item["entry"] for item in manifest["bindings"].values()}), 2)
        self.assertTrue(composition.restore(output, manifest_path, self.tmp / "out.md")["complete"])

    def test_composed_document_can_be_used_as_a_parent(self) -> None:
        first = self.make_source("base.md", "c11234", "base.example")
        second = self.make_source("extra.md", "c21234", "extra.example")
        first_output = self.tmp / "first.md"
        first_manifest, _ = composition.compose([first, second], [], first_output)
        third = self.make_source("third.md", "c31234", "third.example")
        second_output = self.tmp / "second.md"
        second_manifest, second_payload = composition.compose([third], [(first_output, first_manifest)], second_output)
        self.assertEqual(len(second_payload["parents"]), 1)
        self.assertEqual(second_payload["parents"][0]["composition_id"], _read_manifest(first_manifest)["composition_id"])
        self.assertEqual(len(second_payload["maps"]), 3)
        self.assertTrue(
            {item["sha256"] for item in _read_manifest(first_manifest)["sources"]}
            <= {item["sha256"] for item in second_payload["sources"]},
            "chaining retains the original source fingerprint references",
        )
        restored = self.tmp / "second.deanon.md"
        self.assertEqual(composition.restore(second_output, second_manifest, restored)["replaced"], 3)

    def test_base_document_is_registered_as_the_base(self) -> None:
        base = self.make_source("base.md", "d11234", "base.example")
        extra = self.make_source("extra.md", "d21234", "extra.example")
        _, manifest = composition.compose([base, extra], [], self.tmp / "joined.md")
        roles = {source["role"] for source in manifest["sources"]}
        self.assertIn("base", roles)
        self.assertEqual(sum(source["role"] == "base" for source in manifest["sources"]), 1)

    def test_legacy_map_without_metadata_composes_and_restores(self) -> None:
        """Legacy maps supported by deanon may contain only entries and bare placeholders."""
        source = self.tmp / "legacy.md"
        source.write_text("Host: [HOST-1]\n", encoding="utf-8")
        mapping = self.maps / "legacy.map.json"
        legacy_bytes = b'{"entries":{"[HOST-1]":{"type":"HOST","original":"legacy.example"}}}\n'
        mapping.write_bytes(legacy_bytes)
        output = self.tmp / "legacy-joined.md"

        manifest_path, manifest = composition.compose([(source, mapping)], [], output)

        self.assertEqual(manifest["maps"][0]["version"], "legacy")
        self.assertIsNone(manifest["maps"][0]["tag"])
        self.assertEqual(hashlib.sha256(mapping.read_bytes()).digest(), hashlib.sha256(legacy_bytes).digest())
        restored = self.tmp / "legacy-restored.md"
        self.assertTrue(composition.restore(output, manifest_path, restored)["complete"])
        self.assertEqual(restored.read_text(encoding="utf-8"), "Host: legacy.example\n")
        self.assertEqual(mapping.read_bytes(), legacy_bytes)

    def test_engine_placeholder_forms_are_all_transformed_and_restored(self) -> None:
        cases = [
            ("bare.md", "[HOST_KIND-1]", "bare.example", None),
            ("short-tag.md", "[EMAIL-1-abcd]", "short.example", "abcd"),
            ("long-tag.md", "[ID_CODE-1-01234567]", "long.example", "01234567"),
        ]
        sources = []
        source_bytes = []
        map_bytes = []
        for name, token, original, tag in cases:
            document = self.tmp / name
            document.write_text(f"Value: {token}\n", encoding="utf-8")
            map_id = name.replace(".", "-")
            mapping = self.maps / f"{map_id}.map.json"
            payload = {"entries": {token: {"type": token[1:].split("-", 1)[0], "original": original}}}
            if tag:
                payload.update({"id": map_id, "version": anon.VERSION, "schema": anon.SCHEMA, "tag": tag})
            mapping.write_text(json.dumps(payload), encoding="utf-8")
            sources.append((document, mapping))
            source_bytes.append(document.read_bytes())
            map_bytes.append(mapping.read_bytes())

        output = self.tmp / "all-placeholder-forms.md"
        manifest_path, manifest = composition.compose(sources, [], output)
        self.assertEqual(len(manifest["bindings"]), 3)
        for _name, token, _original, _tag in cases:
            self.assertIsNotNone(composition.SUSPICIOUS_TOKEN_RE.fullmatch(token), token)
            self.assertIsNotNone(anon.PLACEHOLDER_RE.fullmatch(token), token)
        restored = self.tmp / "all-placeholder-forms-restored.md"
        self.assertEqual(composition.restore(output, manifest_path, restored)["replaced"], 3)
        self.assertEqual([path.read_bytes() for path, _ in sources], source_bytes)
        self.assertEqual([path.read_bytes() for _, path in sources], map_bytes)

    def test_source_fingerprint_is_a_composition_time_provenance_record(self) -> None:
        source = self.make_source("fingerprinted.md", "b81234", "fingerprinted.example")
        original_source = source[0].read_bytes()
        source_hash = hashlib.sha256(original_source).hexdigest()
        output = self.tmp / "fingerprinted-joined.md"
        manifest_path, manifest = composition.compose([source], [], output)
        source[0].write_bytes(original_source + b"Later source edit\n")

        saved_manifest = _read_manifest(manifest_path)
        self.assertEqual(saved_manifest["sources"][0]["sha256"], source_hash)
        self.assertEqual(saved_manifest["sources"], manifest["sources"])
        # Restore depends on the fingerprinted composition output and its maps, not on reopening
        # the original source document, which is intentionally not part of restoration.
        restored = self.tmp / "fingerprinted-restored.md"
        self.assertTrue(composition.restore(output, manifest_path, restored)["complete"])
        self.assertEqual(source[0].read_bytes(), original_source + b"Later source edit\n")

    def test_output_publication_failure_cleans_temporary_files(self) -> None:
        source = self.make_source("interrupted.md", "b91234", "interrupted.example")
        source_bytes, map_bytes = source[0].read_bytes(), source[1].read_bytes()
        output = self.tmp / "interrupted-joined.md"
        manifest_path = self.manifests / "interrupted.composition.json"
        with mock.patch("composition.os.link", side_effect=OSError("synthetic output publication failure")):
            with self.assertRaisesRegex(OSError, "synthetic output publication failure"):
                composition.compose([source], [], output, manifest_path)

        self.assertEqual(source[0].read_bytes(), source_bytes)
        self.assertEqual(source[1].read_bytes(), map_bytes)
        self.assertFalse(output.exists())
        self.assertFalse(manifest_path.exists())
        self.assertEqual(list(self.tmp.glob(".interrupted-joined.md.tmp-*")), [])
        self.assertEqual(list(self.manifests.glob(".interrupted.composition.json.tmp-*")), [])
        with self.assertRaises((OSError, ValueError)):
            composition.restore(output, manifest_path, self.tmp / "must-not-exist.md")
        self.assertFalse((self.tmp / "must-not-exist.md").exists())

    def test_manifest_publication_failure_leaves_nonrestorable_orphan_output(self) -> None:
        source = self.make_source("manifest-interrupted.md", "bb1234", "manifest.example")
        source_bytes, map_bytes = source[0].read_bytes(), source[1].read_bytes()
        output = self.tmp / "manifest-interrupted-joined.md"
        manifest_path = self.manifests / "manifest-interrupted.composition.json"
        real_link = os.link

        def fail_manifest_link(src, dst):
            if Path(dst).resolve() == manifest_path.resolve():
                raise OSError("synthetic manifest publication failure")
            return real_link(src, dst)

        with mock.patch("composition.os.link", side_effect=fail_manifest_link):
            with self.assertRaisesRegex(OSError, "synthetic manifest publication failure"):
                composition.compose([source], [], output, manifest_path)

        self.assertEqual(source[0].read_bytes(), source_bytes)
        self.assertEqual(source[1].read_bytes(), map_bytes)
        self.assertTrue(output.exists())
        self.assertFalse(manifest_path.exists())
        self.assertEqual(list(self.manifests.glob(".manifest-interrupted.composition.json.tmp-*")), [])
        with self.assertRaises((OSError, ValueError)):
            composition.restore(output, manifest_path, self.tmp / "must-not-exist.md")
        self.assertFalse((self.tmp / "must-not-exist.md").exists())

    def test_concurrent_output_winner_is_never_overwritten(self) -> None:
        source = self.make_source("output-race.md", "bc1234", "race.synthetic.example")
        output = self.tmp / "race-output.md"
        manifest_path = self.manifests / "race.composition.json"
        competitor = b"concurrent winner\n"
        real_link = os.link

        def publish_competitor_then_link(src, dst):
            if Path(dst).resolve() == output.resolve():
                output.write_bytes(competitor)
            return real_link(src, dst)

        with mock.patch("composition.os.link", side_effect=publish_competitor_then_link):
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                composition.compose([source], [], output, manifest_path)
        self.assertEqual(output.read_bytes(), competitor)
        self.assertFalse(manifest_path.exists())
        self.assertEqual(list(self.tmp.glob(".race-output.md.tmp-*")), [])

    def test_concurrent_manifest_winner_is_never_overwritten(self) -> None:
        source = self.make_source("manifest-race.md", "bd1234", "race.synthetic.example")
        output = self.tmp / "race-manifest-output.md"
        manifest_path = self.manifests / "race-winner.composition.json"
        competitor = b"concurrent manifest\n"
        real_link = os.link

        def publish_competitor_then_link(src, dst):
            if Path(dst).resolve() == manifest_path.resolve():
                manifest_path.write_bytes(competitor)
            return real_link(src, dst)

        with mock.patch("composition.os.link", side_effect=publish_competitor_then_link):
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                composition.compose([source], [], output, manifest_path)
        self.assertTrue(output.is_file())
        self.assertEqual(manifest_path.read_bytes(), competitor)
        self.assertEqual(list(self.manifests.glob(".race-winner.composition.json.tmp-*")), [])

    def test_preexisting_output_and_manifest_are_never_overwritten(self) -> None:
        source = self.make_source("protected.md", "ba1234", "protected.example")
        output = self.tmp / "protected-output.md"
        output.write_bytes(b"existing output\n")
        manifest_path = self.manifests / "protected.composition.json"
        output_before = output.read_bytes()

        with self.assertRaisesRegex(ValueError, "refusing to overwrite an existing output"):
            composition.compose([source], [], output, manifest_path)
        self.assertEqual(output.read_bytes(), output_before)
        output.unlink()
        manifest_path.write_bytes(b"existing manifest\n")
        manifest_before = manifest_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "refusing to overwrite an existing manifest"):
            composition.compose([source], [], output, manifest_path)

        self.assertFalse(output.exists())
        self.assertEqual(manifest_path.read_bytes(), manifest_before)

    def test_unknown_or_altered_source_placeholder_fails_before_writing(self) -> None:
        document, mapping = self.make_source("bad.md", "e11234", "bad.example")
        document.write_text("Finding: [HOST-9-e11234]\n", encoding="utf-8")
        output = self.tmp / "never.md"
        with self.assertRaisesRegex(ValueError, "unknown, altered"):
            composition.compose([(document, mapping)], [], output)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.manifests.iterdir()), [])

    def test_modified_output_or_mapping_is_refused_before_restoration(self) -> None:
        source = self.make_source("source.md", "f11234", "safe.example")
        output = self.tmp / "joined.md"
        manifest, _ = composition.compose([source], [], output)
        output.write_text(output.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        target = self.tmp / "restored.md"
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            composition.restore(output, manifest, target)
        self.assertFalse(target.exists())

    def test_altered_mapping_is_refused_before_restoration(self) -> None:
        source = self.make_source("source.md", "a21234", "safe.example")
        output = self.tmp / "joined.md"
        manifest, _ = composition.compose([source], [], output)
        source[1].write_text(source[1].read_text(encoding="utf-8").replace("safe.example", "other.example"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not match its recorded sha256"):
            composition.restore(output, manifest, self.tmp / "restored.md")
        self.assertFalse((self.tmp / "restored.md").exists())

    def test_missing_mapping_is_refused_before_restoration(self) -> None:
        source = self.make_source("source.md", "a91234", "safe.example")
        output = self.tmp / "joined.md"
        manifest, _ = composition.compose([source], [], output)
        source[1].unlink()
        target = self.tmp / "restored.md"
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            composition.restore(output, manifest, target)
        self.assertFalse(target.exists())

    def test_manifest_tampering_and_unsupported_pdf_are_refused(self) -> None:
        source = self.make_source("source.md", "a31234", "safe.example")
        output = self.tmp / "joined.md"
        manifest_path, manifest = composition.compose([source], [], output)
        manifest["output_tag"] = "ffffffff"
        with self.assertRaisesRegex(ValueError, "outside its output namespace"):
            composition._validate_manifest(manifest)
        pdf = self.tmp / "source.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        with self.assertRaisesRegex(ValueError, "unsupported composition format"):
            composition.compose([(pdf, source[1])], [], self.tmp / "bad.md")

    def test_manifest_unhashable_and_wrong_type_fields_are_controlled(self) -> None:
        source = self.make_source("malformed.md", "a41234", "synthetic-secret.example")
        _manifest_path, valid = composition.compose([source], [], self.tmp / "valid.md")
        mutations = [
            lambda item: item["sources"][0].__setitem__("map_ids", [["synthetic-secret"]]),
            lambda item: next(iter(item["bindings"].values())).__setitem__("map_id", {"secret": "synthetic"}),
            lambda item: next(iter(item["bindings"].values())).__setitem__("source_ids", [["synthetic-secret"]]),
            lambda item: item["sources"][0].__setitem__("role", ["synthetic-secret"]),
            lambda item: item["output"].__setitem__("format", {"secret": "synthetic"}),
            lambda item: item["audit"].__setitem__("dictionary_sha256", [{"secret": "synthetic"}]),
        ]
        for mutate in mutations:
            damaged = copy.deepcopy(valid)
            mutate(damaged)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError) as raised:
                composition._validate_manifest(damaged)
            self.assertNotIn("synthetic-secret", str(raised.exception))

    def test_malformed_manifest_cli_exits_cleanly_without_echoing_values(self) -> None:
        source = self.make_source("cli-malformed.md", "a51234", "synthetic-secret.example")
        output = self.tmp / "cli-joined.md"
        manifest_path, manifest = composition.compose([source], [], output)
        manifest["sources"][0]["map_ids"] = [["synthetic-secret"]]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        restored = self.tmp / "cli-restored.md"
        result = subprocess.run(
            [sys.executable, str(ROOT / "deanon.py"), str(output), "--manifest", str(manifest_path), "--out", str(restored)],
            capture_output=True, text=True, check=False,
            env={**os.environ, "ANON_HOME": str(self.home)},
        )
        self.assertEqual(result.returncode, 3)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("synthetic-secret", result.stderr)
        self.assertFalse(restored.exists())

    def test_metadata_read_limit_boundary_and_growth_are_checked(self) -> None:
        path = self.tmp / "bounded.json"
        payload = b'{"x":1}'
        path.write_bytes(payload)
        with mock.patch.object(composition, "MAX_METADATA_BYTES", len(payload)):
            self.assertEqual(composition._read_json(path), {"x": 1})
        with mock.patch.object(composition, "MAX_METADATA_BYTES", len(payload) + 1):
            self.assertEqual(composition._read_json(path), {"x": 1})
        with mock.patch.object(composition, "MAX_METADATA_BYTES", len(payload) - 1):
            with self.assertRaisesRegex(ValueError, "size limit"):
                composition._read_json(path)
        path.write_bytes(payload + b" ")
        with mock.patch.object(composition, "MAX_METADATA_BYTES", len(payload)):
            with self.assertRaisesRegex(ValueError, "size limit"):
                composition._read_json(path)

    def test_mapping_file_limit_is_checked_at_and_above_boundary(self) -> None:
        source = self.make_source("bounded-map.md", "a81234", "bounded.synthetic.example")
        raw = source[1].read_bytes()
        with mock.patch.object(composition, "MAX_METADATA_BYTES", len(raw)):
            self.assertEqual(composition._map_record(source[1])["size"], len(raw))
        with mock.patch.object(composition, "MAX_METADATA_BYTES", len(raw) - 1):
            with self.assertRaisesRegex(ValueError, "size limit"):
                composition._map_record(source[1])

    def test_document_read_limit_is_checked_at_and_above_boundary(self) -> None:
        document = self.tmp / "bounded-document.md"
        document.write_text("Synthetic text\n", encoding="utf-8")
        size = document.stat().st_size
        with mock.patch.object(composition, "MAX_DOCUMENT_BYTES", size):
            self.assertEqual(composition._read_text_source(document)[2], size)
        with mock.patch.object(composition, "MAX_DOCUMENT_BYTES", size + 1):
            self.assertEqual(composition._read_text_source(document)[2], size)
        with mock.patch.object(composition, "MAX_DOCUMENT_BYTES", size - 1):
            with self.assertRaisesRegex(ValueError, "size limit"):
                composition._read_text_source(document)

    def test_limited_read_detects_growth_after_open(self) -> None:
        path = self.tmp / "growing.json"
        path.write_bytes(b'{"x":1}')
        real_open = os.open

        def grow_after_open(target, flags, *args, **kwargs):
            fd = real_open(target, flags, *args, **kwargs)
            if Path(target) == path:
                with path.open("ab") as stream:
                    stream.write(b" ")
            return fd

        with mock.patch("composition.os.open", side_effect=grow_after_open):
            with self.assertRaisesRegex(ValueError, "size limit"):
                composition._read_limited(path, len(b'{"x":1}'), "test metadata")

    def test_mapping_total_limit_is_reapplied_during_restore(self) -> None:
        first = self.make_source("large-one.md", "a61234", "one.synthetic.example")
        second = self.make_source("large-two.md", "a71234", "two.synthetic.example")
        output = self.tmp / "large-joined.md"
        manifest_path, _ = composition.compose([first, second], [], output)
        total = first[1].stat().st_size + second[1].stat().st_size
        with mock.patch.object(composition, "MAX_MAPPING_TOTAL_BYTES", total):
            self.assertTrue(composition.restore(output, manifest_path, self.tmp / "at-limit.md")["complete"])
        with mock.patch.object(composition, "MAX_MAPPING_TOTAL_BYTES", total - 1):
            target = self.tmp / "over-limit.md"
            with self.assertRaisesRegex(ValueError, "composition limit"):
                composition.restore(output, manifest_path, target)
            self.assertFalse(target.exists())

    def test_duplicate_json_keys_in_mapping_are_refused(self) -> None:
        source = self.make_source("source.md", "a71234", "safe.example")
        source[1].write_text(
            '{"id":"source-md","id":"source-md","version":"2.2.1","tag":"a71234",'
            '"entries":{"[HOST-1-a71234]":{"type":"HOST","original":"safe.example"}}}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            composition.compose([source], [], self.tmp / "joined.md")

    def test_malformed_map_keys_are_not_echoed(self) -> None:
        source = self.make_source("source.md", "a81234", "safe.example")
        source[1].write_text(
            json.dumps({"id": "source-md", "tag": "a81234", "entries": {"Private Synthetic Entity": None}}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "source map entries are malformed") as raised:
            composition.compose([source], [], self.tmp / "joined.md")
        self.assertNotIn("Private Synthetic Entity", str(raised.exception))

    def test_manifest_path_must_stay_in_the_private_store(self) -> None:
        source = self.make_source("source.md", "a41234", "safe.example")
        with self.assertRaisesRegex(ValueError, "inside the private"):
            composition.compose([source], [], self.tmp / "joined.md", self.tmp / "public.json")

    def test_mapping_outside_the_private_store_is_refused(self) -> None:
        source = self.make_source("source.md", "aa1234", "safe.example")
        external = self.tmp / "external.map.json"
        shutil.copyfile(source[1], external)
        with self.assertRaisesRegex(ValueError, "private HideMark map directory"):
            composition.compose([(source[0], external)], [], self.tmp / "joined.md")

    def test_configured_dictionary_findings_refuse_composition_without_echoing_values(self) -> None:
        source = self.make_source("source.md", "a51234", "Private Synthetic Entity")
        dictionary = self.tmp / "extra.txt"
        dictionary.write_text("@type AZIENDA\nPrivate Synthetic Entity\n", encoding="utf-8")
        source[0].write_text("Private Synthetic Entity: [HOST-1-a51234]\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "configured sensitive-data rules"):
            composition.compose([source], [], self.tmp / "joined.md", entity_paths=[dictionary])
        self.assertFalse((self.tmp / "joined.md").exists())

    def test_deanon_cli_restores_a_manifest_backed_document(self) -> None:
        source = self.make_source("source.md", "a61234", "synthetic.example")
        output = self.tmp / "joined.md"
        manifest, _ = composition.compose([source], [], output)
        restored = self.tmp / "restored.md"
        result = subprocess.run(
            [sys.executable, str(ROOT / "deanon.py"), str(output), "--manifest", str(manifest), "--out", str(restored), "--json"],
            capture_output=True, text=True, check=False,
            env={**os.environ, "ANON_HOME": str(self.home)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["complete"])
        self.assertIn("synthetic.example", restored.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
