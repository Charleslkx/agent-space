#!/usr/bin/env bash
# Read-only prerequisite checks; setup mode creates only a disposable virtualenv.
set -euo pipefail
mode=${1:---runtime}
python_bin=${PYTHON_BIN:-python3}
case "$mode" in --runtime|--setup|--uv) ;; *) printf 'usage: check-python.sh [--runtime|--setup|--uv]\n' >&2; exit 2 ;; esac
fail() { printf 'Python prerequisite failed: %s\n' "$*" >&2; exit 1; }
command -v "$python_bin" >/dev/null 2>&1 || fail 'selected Python interpreter is missing. Ubuntu/Debian: sudo apt-get install python3 python3-venv; macOS: install Python with your package manager, then recheck PATH.'
"$python_bin" -c 'import sys; import ssl, json, urllib.request; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1 || fail 'Python 3.10+ with ssl/json/urllib is required; select a compatible python3 in PATH before continuing.'
if [ "$mode" = --setup ]; then
  temporary=$(mktemp -d)
  trap 'rm -rf "$temporary"' EXIT
  "$python_bin" -m venv "$temporary/venv" >/dev/null 2>&1 || fail 'venv/ensurepip is unavailable. Ubuntu/Debian: install python3-venv (or the matching python3.X-venv package); macOS: use a Python distribution with venv/ensurepip. Recheck before writing configuration.'
  "$temporary/venv/bin/python" -m pip --version >/dev/null 2>&1 || fail 'pip is unavailable inside a fresh virtualenv; repair this Python installation before continuing.'
fi
if [ "$mode" = --uv ]; then
  command -v uv >/dev/null 2>&1 && uv --version >/dev/null 2>&1 || fail 'uv is required by this operation but unavailable in PATH; install it following https://docs.astral.sh/uv/getting-started/installation/ and rerun this check. Keep an existing uv version for debugging.'
fi
printf 'Python prerequisite check passed (%s).\n' "$mode"
