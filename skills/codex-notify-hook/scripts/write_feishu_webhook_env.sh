#!/usr/bin/env bash
set -eu

# Replace the values below, then run this script yourself.
LARK_WEBHOOK_URL="REPLACE_WITH_FEISHU_WEBHOOK_URL"
LARK_WEBHOOK_SECRET="" # Optional: required only when webhook signing is enabled.

case "$LARK_WEBHOOK_URL" in
  REPLACE_*|"") echo "Replace LARK_WEBHOOK_URL before running this script." >&2; exit 1 ;;
esac

umask 077
mkdir -p "$HOME/.codex"
{
  printf 'LARK_WEBHOOK_URL=%s\n' "$LARK_WEBHOOK_URL"
  printf 'LARK_WEBHOOK_SECRET=%s\n' "$LARK_WEBHOOK_SECRET"
} > "$HOME/.codex/feishu-webhook.env"
chmod 600 "$HOME/.codex/feishu-webhook.env"
printf 'Wrote %s\n' "$HOME/.codex/feishu-webhook.env"
