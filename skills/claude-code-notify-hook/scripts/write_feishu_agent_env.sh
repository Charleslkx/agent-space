#!/usr/bin/env bash
set -eu

# Replace the values below, then run this script yourself.
FEISHU_APP_ID="REPLACE_WITH_FEISHU_APP_ID"
FEISHU_APP_SECRET="REPLACE_WITH_FEISHU_APP_SECRET"
FEISHU_HOME_CHANNEL="REPLACE_WITH_CHAT_ID"
FEISHU_APPROVAL_RECEIVE_ID_TYPE="chat_id"

case "$FEISHU_APP_ID:$FEISHU_APP_SECRET:$FEISHU_HOME_CHANNEL" in
  *REPLACE_*|*::*) echo "Replace all required FEISHU_* values before running this script." >&2; exit 1 ;;
esac

umask 077
mkdir -p "$HOME/.claude"
{
  printf 'FEISHU_APP_ID=%s\n' "$FEISHU_APP_ID"
  printf 'FEISHU_APP_SECRET=%s\n' "$FEISHU_APP_SECRET"
  printf 'FEISHU_HOME_CHANNEL=%s\n' "$FEISHU_HOME_CHANNEL"
  printf 'FEISHU_APPROVAL_RECEIVE_ID_TYPE=%s\n' "$FEISHU_APPROVAL_RECEIVE_ID_TYPE"
} > "$HOME/.claude/feishu-agent.env"
chmod 600 "$HOME/.claude/feishu-agent.env"
printf 'Wrote %s\n' "$HOME/.claude/feishu-agent.env"
