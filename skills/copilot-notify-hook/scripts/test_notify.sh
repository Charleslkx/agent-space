#!/usr/bin/env bash
set -eu

script="$(dirname "$0")/notify.py"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

printf '%s' '{"cwd":"/tmp/demo"}' \
  | NOTIFY_DEBUG=1 NOTIFY_DRY_RUN=1 NOTIFY_FORCE=1 python3 "$script" stop 2>"$tmp/stop"
grep -Fq 'kind=done project=demo content=任务已完成' "$tmp/stop"

printf '%s' '{"cwd":"/tmp/demo","message":"需要批准 shell"}' \
  | NOTIFY_DEBUG=1 NOTIFY_DRY_RUN=1 NOTIFY_FORCE=1 python3 "$script" notification 2>"$tmp/attention"
grep -Fq 'kind=attention project=demo content=需要批准 shell' "$tmp/attention"

printf '%s\n' 'ok: completion and attention payloads parsed'
