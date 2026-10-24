#!/usr/bin/env bash
# Build and exercise the UI in a disposable, loopback-only container.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="$(python3 -c 'import secrets;print(secrets.token_hex(16))')"
IMAGE="${IMAGE:-hidemark:smoke-${RUN_ID}}"
CONTAINER="hidemark-smoke-${RUN_ID}"
REQUESTED_PORT="${PORT:-}"
PORT=""
CONTAINER_ID=""
RUN_ATTEMPTED=0
DATA="$(mktemp -d "${TMPDIR:-/tmp}/anon-smoke-XXXXXX")"
DOCX_DIR=""
mkdir -p "$DATA/maps" "$DATA/catalogs"
printf 'AZIENDA|Contoso\n' > "$DATA/entities.txt"

# Python provides timeout semantics on macOS and Linux without an external `timeout` utility.
_docker_timeout() {
  local seconds="$1"
  shift
  python3 - "$DOCKER_BIN" "$seconds" "$@" <<'PY'
import subprocess, sys

binary, seconds, *args = sys.argv[1:]
try:
    result = subprocess.run([binary, *args], check=False, timeout=float(seconds))
except subprocess.TimeoutExpired:
    print(f"smoke: Docker command timed out after {seconds} seconds", file=sys.stderr)
    raise SystemExit(124)
raise SystemExit(result.returncode)
PY
}
docker() { _docker_timeout "${SMOKE_DOCKER_COMMAND_TIMEOUT:-30}" "$@"; }
docker_build() { _docker_timeout "${SMOKE_DOCKER_BUILD_TIMEOUT:-300}" "$@"; }

diagnose_and_remove_owned_container() {
  local ids id_count candidate ownership
  if [ "$RUN_ATTEMPTED" -eq 1 ]; then
    if [ -n "$CONTAINER_ID" ]; then
      ownership="$(docker inspect --format '{{.Name}}|{{index .Config.Labels "org.hidemark.smoke.run"}}|{{.Config.Image}}' "$CONTAINER_ID" 2>/dev/null || true)"
      if [ "$ownership" != "/${CONTAINER}|${RUN_ID}|${IMAGE}" ]; then
        CONTAINER_ID=""
      fi
    fi
    if [ -z "$CONTAINER_ID" ]; then
      ids="$(docker ps -aq --filter "label=org.hidemark.smoke.run=${RUN_ID}" 2>/dev/null || true)"
      id_count="$(printf '%s\n' "$ids" | sed '/^$/d' | wc -l | tr -d ' ')"
      # A label alone is not authority to remove anything. Require one match and exact identity.
      if [ "$id_count" = 1 ]; then
        candidate="$(printf '%s\n' "$ids" | sed '/^$/d')"
        ownership="$(docker inspect --format '{{.Name}}|{{index .Config.Labels "org.hidemark.smoke.run"}}|{{.Config.Image}}' "$candidate" 2>/dev/null || true)"
        if [ "$ownership" = "/${CONTAINER}|${RUN_ID}|${IMAGE}" ]; then
          CONTAINER_ID="$candidate"
        fi
      fi
    fi
  fi

  if [ -n "$CONTAINER_ID" ]; then
    if [ "$SMOKE_EXIT" -ne 0 ]; then
      printf 'Docker smoke failure diagnostics (container %s):\n' "$CONTAINER_ID" >&2
      docker inspect --format 'state={{.State.Status}} exit_code={{.State.ExitCode}} error={{json .State.Error}} started={{.State.StartedAt}} image={{.Image}} user={{.Config.User}} entrypoint={{json .Config.Entrypoint}} cmd={{json .Config.Cmd}} ports={{json .HostConfig.PortBindings}} health={{json .State.Health}}' "$CONTAINER_ID" >&2 || true
      printf '%s\n' 'container environment (safe allowlist):' >&2
      docker inspect --format '{{json .Config.Env}}' "$CONTAINER_ID" | python3 -c 'import json,sys; values=dict(item.split("=",1) for item in json.load(sys.stdin) if "=" in item); allowed=("ANON_HOME","ANON_CONVERTER","PORT","PYTHONUNBUFFERED","PYTHONDONTWRITEBYTECODE"); print(json.dumps({key:values[key] for key in allowed if key in values},sort_keys=True))' >&2 || true
      printf '%s\n' 'container logs:' >&2
      # The smoke request body and mounted data contain synthetic fixture values only.
      docker logs --timestamps "$CONTAINER_ID" >&2 || true
      printf 'image: ' >&2
      docker image inspect --format 'id={{.Id}} user={{.Config.User}} entrypoint={{json .Config.Entrypoint}}' "$IMAGE" >&2 || true
      if [ -n "$PORT" ] && command -v ss >/dev/null 2>&1; then
        ss -ltnp "( sport = :${PORT} )" >&2 || true
      elif [ -n "$PORT" ] && command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2 || true
      fi
    fi
    docker rm -f "$CONTAINER_ID" >/dev/null 2>&1 || true
  fi
}

cleanup() {
  SMOKE_EXIT=$?
  trap - EXIT
  diagnose_and_remove_owned_container
  [ -z "$DOCX_DIR" ] || rm -rf "$DOCX_DIR"
  rm -rf "$DATA"
  exit "$SMOKE_EXIT"
}
trap cleanup EXIT

fail() { printf 'FAIL  %s\n' "$*" >&2; exit 1; }
pass() { printf 'PASS  %s\n' "$*"; }

command -v docker >/dev/null || { echo "smoke: docker not available" >&2; exit 2; }
DOCKER_BIN="$(command -v docker)"

# Refuse collisions before building. Never stop or remove a pre-existing container or image.
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  fail "smoke container name already exists: $CONTAINER"
fi
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  fail "image tag already exists; choose a fresh IMAGE: $IMAGE"
fi

docker_build build -q -t "$IMAGE" "$REPO" >/dev/null || fail "docker build"
pass "image builds"

if [ -n "$REQUESTED_PORT" ]; then
  case "$REQUESTED_PORT" in *[!0-9]*|'') fail "PORT must be an available numeric host port" ;; esac
  if [ "$REQUESTED_PORT" -lt 1 ] || [ "$REQUESTED_PORT" -gt 65535 ]; then
    fail "PORT must be between 1 and 65535"
  fi
  PUBLISHED_PORT="127.0.0.1:${REQUESTED_PORT}:1407"
else
  # Let Docker reserve an ephemeral port atomically; bind-then-close has a TOCTOU race.
  PUBLISHED_PORT="127.0.0.1::1407"
fi

RUN_ATTEMPTED=1
CONTAINER_ID="$(docker run -d --name "$CONTAINER" \
  --label "org.hidemark.smoke.run=${RUN_ID}" \
  -p "$PUBLISHED_PORT" \
  -v "$DATA:/data" \
  --read-only --tmpfs /tmp \
  --security-opt no-new-privileges:true \
  "$IMAGE")" || fail "docker run"
[ -n "$CONTAINER_ID" ] || fail "docker run returned no container id"

PUBLISHED="$(docker port "$CONTAINER_ID" 1407/tcp)" || fail "container port is not published"
case "$PUBLISHED" in
  127.0.0.1:*) PORT="${PUBLISHED##*:}" ;;
  *) fail "port is not loopback-only (got $PUBLISHED)" ;;
esac

READY=0
for _ in $(seq 1 20); do
  if curl --connect-timeout 0.5 --max-time 0.5 -fsS "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 0.5
done
[ "$READY" -eq 1 ] || fail "container readiness timed out after 20 seconds"
curl --connect-timeout 1 --max-time 2 -fsS "http://127.0.0.1:${PORT}/" >/dev/null || fail "container serves no page"
pass "container serves the UI"

TOKEN="$(curl --connect-timeout 1 --max-time 5 -fsS "http://127.0.0.1:${PORT}/" | grep -o 'ANON_TOKEN = "[^"]*"' | cut -d'"' -f2)"
[ -n "$TOKEN" ] || fail "no token in the page"
api() { curl --connect-timeout 1 --max-time 5 -fsS -H "X-Anon-Token: ${TOKEN}" -H 'Content-Type: application/json' "$@"; }

api "http://127.0.0.1:${PORT}/api/state" | grep -q '"schema": "anon/1"' || fail "api/state"
pass "/api/state"

REDACTED="$(api -X POST -d '{"text":"Cliente Contoso e persona@contoso.local\n"}' \
  "http://127.0.0.1:${PORT}/api/anonymize")"
echo "$REDACTED" | grep -qE '\[EMAIL-1-[0-9a-f]{6,8}\]' || fail "anonymize (tagged placeholder missing)"
echo "$REDACTED" | grep -q 'persona@contoso.local' && fail "anonymize left the value in place"
pass "/api/anonymize (tagged placeholder, value gone)"

api "http://127.0.0.1:${PORT}/api/state" | grep -q '"converter": true' || fail "converter missing in the image"
pass "converter available inside the image"

STATUS="$(curl --connect-timeout 1 --max-time 5 -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/api/state")"
[ "$STATUS" = "403" ] || fail "the API answered without the token (got $STATUS)"
pass "the API refuses requests without the token"

if command -v pandoc >/dev/null; then
  # Exercise the document path inside the container with a generated fixture; never use a real file.
  DOCX_DIR="$(mktemp -d "${TMPDIR:-/tmp}/anon-smoke-doc-XXXXXX")"
  printf 'Cliente Contoso, referente persona@contoso.local, server 10.42.7.19\n' > "$DOCX_DIR/doc.md"
  pandoc "$DOCX_DIR/doc.md" -o "$DOCX_DIR/doc.docx"
  export DOC_RESULT
  DOC_RESULT="$(curl --connect-timeout 1 --max-time 30 -fsS -X POST \
    -H "X-Anon-Token: ${TOKEN}" -H 'X-Filename: doc.docx' -H 'Content-Type: application/octet-stream' \
    --data-binary "@$DOCX_DIR/doc.docx" "http://127.0.0.1:${PORT}/api/anonymize-document")" || fail "docx request failed"
  echo "$DOC_RESULT" | grep -q '"origin": "container"' || fail "docx was not rewritten in place"
  echo "$DOC_RESULT" | grep -q '"container_name": "doc.redacted.docx"' || fail "no redacted document offered"
  echo "$DOC_RESULT" | grep -qE '\[AZIENDA-1-[0-9a-f]{6,8}\]' || fail "docx text was not redacted"
  echo "$DOC_RESULT" | grep -q 'contoso.local' && fail "the response leaked the value"
  echo "$DOC_RESULT" | grep -q 'container_b64' && fail "the document must not travel inside the JSON"
  DOC_URL="$(echo "$DOC_RESULT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("container_url", ""))')"
  [ -n "$DOC_URL" ] || fail "no download url for the redacted document"
  curl --connect-timeout 1 --max-time 30 -fsS -H "X-Anon-Token: ${TOKEN}" \
    "http://127.0.0.1:${PORT}${DOC_URL}" -o "$DOCX_DIR/out.docx" || fail "redacted document download failed"
  DOCX_DIR="$DOCX_DIR" python3 -c '
import json, os, pathlib, zipfile
result=json.loads(os.environ["DOC_RESULT"])
path=pathlib.Path(os.environ["DOCX_DIR"])/"out.docx"
assert zipfile.is_zipfile(path), "download is not a document container"
with zipfile.ZipFile(path) as archive:
    inside="\n".join(archive.read(name).decode("utf-8","replace") for name in archive.namelist())
assert "contoso.local" not in inside, "value survived inside the document"
tag=result["tag"]
  assert f"[EMAIL-1-{tag}]" in inside, "placeholder missing inside document"
  assert f"[EMAIL-1-{tag}]" in result["redacted"], "document and response do not share the tag"
' || fail "downloaded document did not pass redaction verification"
  pass "docx rewritten in place and verified inside output"
else
  printf 'SKIP  docx end-to-end (pandoc not installed)\n'
fi

docker port "$CONTAINER_ID" 1407/tcp | grep -q "^127.0.0.1:${PORT}$" || fail "port is not loopback-only"
pass "port published on 127.0.0.1 only"

if docker compose version >/dev/null 2>&1; then
  SERVICES="$(cd "$REPO" && docker compose --profile slim config --services 2>/dev/null | sort | tr '\n' ' ')"
  case "$SERVICES" in
    *hidemark-slim*) pass "compose profile 'slim' exposes hidemark-slim" ;;
    *) fail "compose profile 'slim' is missing (services: ${SERVICES:-none})" ;;
  esac
fi

printf '\nALL DOCKER SMOKE CHECKS PASSED\n'
