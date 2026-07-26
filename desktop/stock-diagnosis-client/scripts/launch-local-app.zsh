#!/bin/zsh
set -euo pipefail

CLIENT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$CLIENT_ROOT/../.." && pwd)"
RUNTIME_DIR="$CLIENT_ROOT/.runtime"
READ_PORT="${ASTOCK_READ_SERVICE_PORT:-8011}"
READ_SERVICE_URL="${ASTOCK_READ_SERVICE_URL:-http://127.0.0.1:${READ_PORT}}"
READ_SERVICE_HEALTH="$READ_SERVICE_URL/health"
READ_SERVICE_LOG="$RUNTIME_DIR/read-service.log"
ELECTRON_REBUILD_TIMEOUT_SECONDS="${ASTOCK_ELECTRON_REBUILD_TIMEOUT_SECONDS:-180}"

print_header() {
  printf "\n== %s ==\n" "$1"
}

run_with_timeout() {
  local timeout_seconds="$1"
  shift
  local deadline=$((SECONDS + timeout_seconds))

  "$@" &
  local child_pid="$!"

  while kill -0 "$child_pid" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      kill "$child_pid" >/dev/null 2>&1 || true
      sleep 2
      kill -9 "$child_pid" >/dev/null 2>&1 || true
      wait "$child_pid" >/dev/null 2>&1 || true
      return 124
    fi
    sleep 2
  done

  wait "$child_pid"
}

service_is_up() {
  curl -fsS "$READ_SERVICE_HEALTH" >/dev/null 2>&1
}

wait_for_read_service() {
  local attempt
  for attempt in {1..35}; do
    if service_is_up; then
      return 0
    fi
    sleep 1
  done
  return 1
}

start_read_service() {
  mkdir -p "$RUNTIME_DIR"
  if service_is_up; then
    echo "Local read service is already running: $READ_SERVICE_URL"
    return 0
  fi

  print_header "Starting Python read service"
  (
    cd "$REPO_ROOT/factor_research"
    exec python3 -m uvicorn api.main:app --host 127.0.0.1 --port "$READ_PORT"
  ) >"$READ_SERVICE_LOG" 2>&1 &

  echo "$!" >"$RUNTIME_DIR/read-service.pid"

  if wait_for_read_service; then
    echo "Local read service ready: $READ_SERVICE_URL"
    return 0
  fi

  echo "Local read service did not become ready."
  echo "Log: $READ_SERVICE_LOG"
  tail -n 40 "$READ_SERVICE_LOG" || true
  return 1
}

ensure_node_deps() {
  cd "$CLIENT_ROOT"
  if [[ -d node_modules ]]; then
    return 0
  fi

  print_header "Installing desktop dependencies"
  npm install
}

electron_runtime_is_available() {
  cd "$CLIENT_ROOT"
  node -e 'const { existsSync } = require("node:fs"); const electronPath = require("electron"); if (!existsSync(electronPath)) process.exit(1);' >/dev/null 2>&1
}

electron_app_path() {
  cd "$CLIENT_ROOT"
  local electron_binary
  electron_binary="$(node -e 'process.stdout.write(require("electron"))')"
  cd "$(dirname "$electron_binary")/../.."
  pwd
}

ensure_electron_runtime() {
  cd "$CLIENT_ROOT"
  if electron_runtime_is_available; then
    return 0
  fi

  print_header "Repairing Electron runtime"
  echo "Electron is installed as a package, but its macOS runtime binary is missing."
  export ELECTRON_MIRROR="${ELECTRON_MIRROR:-https://npmmirror.com/mirrors/electron/}"
  export npm_config_electron_mirror="${npm_config_electron_mirror:-$ELECTRON_MIRROR}"
  echo "Trying npm rebuild electron once with mirror: $ELECTRON_MIRROR"
  echo "Timeout: ${ELECTRON_REBUILD_TIMEOUT_SECONDS}s"

  if run_with_timeout "$ELECTRON_REBUILD_TIMEOUT_SECONDS" npm rebuild electron --foreground-scripts && electron_runtime_is_available; then
    echo "Electron runtime repaired."
    return 0
  fi

  cat <<'MESSAGE'

Electron runtime is still unavailable.

The app cannot open until Electron's macOS runtime binary is downloaded.

Run this once from the desktop client directory:

  ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ npm rebuild electron

Then double-click AStock Lens.app again.
MESSAGE
  return 1
}

ensure_electron_codesign() {
  cd "$CLIENT_ROOT"
  if ! command -v codesign >/dev/null 2>&1; then
    return 0
  fi

  local electron_app
  electron_app="$(electron_app_path)" || return 0

  if codesign --verify --deep --strict "$electron_app" >/dev/null 2>&1; then
    return 0
  fi

  print_header "Repairing Electron code signature"
  echo "macOS rejected Electron's downloaded app bundle signature."
  echo "Applying a local ad-hoc signature for development launch."

  xattr -dr com.apple.quarantine "$electron_app" >/dev/null 2>&1 || true

  if codesign --force --deep --sign - "$electron_app" && codesign --verify --deep --strict "$electron_app" >/dev/null 2>&1; then
    echo "Electron code signature repaired."
    return 0
  fi

  cat <<'MESSAGE'

Electron code signature repair failed.

Run this once from the desktop client directory:

  codesign --force --deep --sign - node_modules/electron/dist/Electron.app

Then double-click AStock Lens.app again.
MESSAGE
  return 1
}

launch_desktop_client() {
  cd "$CLIENT_ROOT"
  export ASTOCK_READ_SERVICE_URL="$READ_SERVICE_URL"
  print_header "Opening AStock Lens"
  npm run dev
}

print_header "AStock Lens Local App"
echo "Client: $CLIENT_ROOT"
echo "Read service: $READ_SERVICE_URL"

start_read_service
ensure_node_deps
ensure_electron_runtime
ensure_electron_codesign
launch_desktop_client
