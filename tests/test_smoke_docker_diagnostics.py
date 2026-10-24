#!/usr/bin/env python3
"""Exercise Docker smoke diagnostics and cleanup with synthetic command shims."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "smoke-docker.sh"

FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
args = sys.argv[1:]
trace = pathlib.Path(os.environ["DOCKER_TRACE"])
with trace.open("a") as stream:
    stream.write(json.dumps(args) + "\n")
run_id_file = pathlib.Path(os.environ["RUN_ID_FILE"])
run_id = run_id_file.read_text().strip() if run_id_file.exists() else ""
container = "hidemark-smoke-" + run_id
image = os.environ["IMAGE"]
if args[:1] == ["inspect"]:
    if args[1:2] != ["--format"]:
        sys.exit(0 if os.environ.get("FAKE_OCCUPIED") == "1" else 1)
    fmt = args[2]
    if fmt == '{{.Name}}|{{index .Config.Labels "org.hidemark.smoke.run"}}|{{.Config.Image}}':
        if args[-1] == "ambiguous-container-id":
            print("/unowned-container|other-run|other-image")
            sys.exit(0)
        print("/" + container + "|" + run_id + "|" + image)
    elif fmt == "{{json .Config.Env}}":
        print(json.dumps(["ANON_HOME=/data", "PORT=1407", "ANON_CONVERTER=/app/convert.py"]))
    else:
        print("state=" + os.environ.get("FAKE_CONTAINER_STATE", "exited") +
              " exit_code=1 error=synthetic started=now image=sha256:synthetic")
    sys.exit(0)
if args[:2] == ["image", "inspect"]:
    marker = pathlib.Path(os.environ["FAKE_IMAGE_MARKER"])
    if not marker.exists():
        sys.exit(1)
    print('id=sha256:synthetic user=anon entrypoint=["/entrypoint"]')
    sys.exit(0)
if args[:1] == ["build"]:
    time.sleep(float(os.environ.get("FAKE_BUILD_DELAY", "0")))
    pathlib.Path(os.environ["FAKE_IMAGE_MARKER"]).touch()
    sys.exit(0)
if args[:1] == ["run"]:
    run_id = args[args.index("--label") + 1].split("=", 1)[1]
    run_id_file.write_text(run_id)
    data_root = pathlib.Path(args[args.index("-v") + 1].split(":", 1)[0])
    for path in (data_root, data_root / "maps", data_root / "catalogs"):
        if path.stat().st_mode & 0o007 != 0o007:
            print("fake Docker: smoke data path is not accessible to uid 10001", file=sys.stderr)
            sys.exit(126)
    print("fake Docker: smoke data paths are accessible to uid 10001", file=sys.stderr)
    if os.environ.get("FAKE_RUN_FAILURE") == "1":
        print("ambiguous-container-id")
        sys.exit(125)
    print("synthetic-container-id")
    sys.exit(0)
if args[:1] == ["ps"]:
    if os.environ.get("FAKE_RUN_FAILURE") == "1":
        print("partial-container-id")
    sys.exit(0)
if args[:1] == ["port"]:
    print("127.0.0.1:45678")
    sys.exit(0)
if args[:1] == ["logs"]:
    print("synthetic startup failure log")
    sys.exit(0)
if args[:2] == ["compose", "version"]:
    sys.exit(0)
if args[:4] == ["compose", "--profile", "slim", "config"]:
    print("hidemark-slim")
    sys.exit(0)
if args[:2] == ["rm", "-f"]:
    sys.exit(0)
sys.exit(0)
'''


class SmokeDockerDiagnosticsTest(unittest.TestCase):
    def run_smoke(self, *, state="exited", occupied=False, run_failure=False,
                  success=False, build_timeout="300", build_delay="0"):
        root = pathlib.Path(tempfile.mkdtemp(prefix="smoke-diagnostics-"))
        self.addCleanup(shutil.rmtree, root)
        bin_dir = root / "bin"
        tmp_dir = root / "tmp"
        bin_dir.mkdir()
        tmp_dir.mkdir()
        trace = root / "docker.trace"
        marker = root / "image.inspect"
        run_id_file = root / "run-id"

        docker = bin_dir / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)
        curl = bin_dir / "curl"
        curl.write_text(
            r'''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
with open(os.environ["CURL_TRACE"], "a") as f: f.write("curl\n")
if os.environ.get("FAKE_SUCCESS") != "1": sys.exit(7)
url = args[-1]
if url.endswith("/api/state") and "-w" in args: print("403"); sys.exit(0)
if url.endswith("/api/state"): print('{"schema": "anon/1", "converter": true}'); sys.exit(0)
if url.endswith("/api/anonymize"): print('{"redacted":"[EMAIL-1-123456]","map":{},"entries":1,"counts":{}}'); sys.exit(0)
if url.endswith("/"): print('<script>ANON_TOKEN = "synthetic-token"</script>'); sys.exit(0)
sys.exit(0)
''', encoding="utf-8")
        curl.chmod(0o755)
        sleep = bin_dir / "sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        sleep.chmod(0o755)

        env = os.environ.copy()
        env.update({
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "TMPDIR": str(tmp_dir),
            "IMAGE": "synthetic:smoke-test",
            "DOCKER_TRACE": str(trace),
            "CURL_TRACE": str(root / "curl.trace"),
            "RUN_ID_FILE": str(run_id_file),
            "FAKE_IMAGE_MARKER": str(marker),
            "FAKE_CONTAINER_STATE": state,
            "FAKE_OCCUPIED": "1" if occupied else "0",
            "FAKE_RUN_FAILURE": "1" if run_failure else "0",
            "FAKE_SUCCESS": "1" if success else "0",
            "SMOKE_DOCKER_BUILD_TIMEOUT": build_timeout,
            "FAKE_BUILD_DELAY": build_delay,
        })
        result = subprocess.run(["bash", str(SMOKE)], cwd=ROOT, env=env, text=True,
                                capture_output=True, check=False, timeout=10)
        calls = [json.loads(line) for line in trace.read_text().splitlines()]
        curl_calls = (root / "curl.trace").read_text().splitlines() if (root / "curl.trace").exists() else []
        return result, calls, curl_calls, tmp_dir

    def test_startup_failure_logs_state_before_removing_created_container(self):
        result, calls, _, tmp_dir = self.run_smoke(state="exited")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL  container readiness timed out", result.stderr)
        self.assertIn("state=exited exit_code=1 error=synthetic", result.stderr)
        self.assertIn("synthetic startup failure log", result.stderr)
        self.assertIn('entrypoint=["/entrypoint"]', result.stderr)
        self.assertLess(calls.index(["logs", "--timestamps", "synthetic-container-id"]),
                        calls.index(["rm", "-f", "synthetic-container-id"]))
        self.assertEqual(list(tmp_dir.iterdir()), [])

    def test_readiness_timeout_is_bounded_and_reports_running_container(self):
        result, calls, curl_calls, tmp_dir = self.run_smoke(state="running")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("state=running exit_code=1", result.stderr)
        self.assertEqual(len(curl_calls), 20)
        self.assertIn(["rm", "-f", "synthetic-container-id"], calls)
        self.assertEqual(list(tmp_dir.iterdir()), [])

    def test_occupied_container_name_is_preserved_without_build_or_removal(self):
        result, calls, curl_calls, tmp_dir = self.run_smoke(occupied=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("smoke container name already exists", result.stderr)
        self.assertFalse(any(call[:1] == ["build"] for call in calls))
        self.assertFalse(any(call[:1] == ["run"] for call in calls))
        self.assertFalse(any(call[:2] == ["rm", "-f"] for call in calls))
        self.assertEqual(curl_calls, [])
        self.assertEqual(list(tmp_dir.iterdir()), [])

    def test_partial_docker_run_failure_recovers_only_verified_container(self):
        result, calls, _, tmp_dir = self.run_smoke(run_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL  docker run", result.stderr)
        self.assertIn(["inspect", "--format",
                       '{{.Name}}|{{index .Config.Labels "org.hidemark.smoke.run"}}|{{.Config.Image}}',
                       "ambiguous-container-id"], calls)
        ownership_check = ["inspect", "--format",
                           '{{.Name}}|{{index .Config.Labels "org.hidemark.smoke.run"}}|{{.Config.Image}}',
                           "partial-container-id"]
        self.assertIn(ownership_check, calls)
        self.assertTrue(any(call[:3] == ["ps", "-aq", "--filter"] for call in calls))
        self.assertLess(calls.index(ownership_check),
                        calls.index(["logs", "--timestamps", "partial-container-id"]))
        self.assertIn(["rm", "-f", "partial-container-id"], calls)
        self.assertLess(calls.index(["logs", "--timestamps", "partial-container-id"]),
                        calls.index(["rm", "-f", "partial-container-id"]))
        self.assertEqual(list(tmp_dir.iterdir()), [])

    def test_normal_success_runs_all_smoke_checks_and_cleans_container(self):
        result, calls, curl_calls, tmp_dir = self.run_smoke(success=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ALL DOCKER SMOKE CHECKS PASSED", result.stdout)
        self.assertIn("fake Docker: smoke data paths are accessible to uid 10001", result.stderr)
        for expected in ("/api/state", "/api/anonymize", "127.0.0.1 only"):
            self.assertIn(expected, result.stdout)
        self.assertIn(["rm", "-f", "synthetic-container-id"], calls)
        self.assertTrue(curl_calls)
        self.assertEqual(list(tmp_dir.iterdir()), [])

    def test_docker_build_timeout_is_bounded_and_does_not_start_a_container(self):
        started = time.monotonic()
        result, calls, _, tmp_dir = self.run_smoke(build_timeout="1", build_delay="5")
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 1)
        self.assertIn("Docker command timed out after 1 seconds", result.stderr)
        self.assertIn("FAIL  docker build", result.stderr)
        self.assertLess(elapsed, 4)
        self.assertFalse(any(call[:1] == ["run"] for call in calls))
        self.assertFalse(any(call[:2] == ["rm", "-f"] for call in calls))
        self.assertEqual(list(tmp_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
