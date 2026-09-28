# Codex New Agent Button

Adds buttons to the right side of VS Code's editor tab bar:

- `+` runs `Codex: New Codex Agent`.
- The history button lists local Codex sessions and opens the selected session, including its existing messages, in a new Codex editor tab. Session listing uses `sqlite3` when present, and falls back to `python3`'s standard library so Linux TUI hosts without the CLI still work.
- The sign-out button exits the current Codex session: it closes the conversation tab so the official extension can `thread/unsubscribe`, then removes leftover `~/.codex/thread-writer-locks/*.lock` files that no process still holds.

Settings:

- `codexNewAgentButton.exitOnEditorClose` (default `true`) runs that exit path when a Codex tab is closed.
- `codexNewAgentButton.releaseStaleWriterLocks` (default `true`) only deletes stale writer locks, never live ones.

Migration:

- Version `0.0.14` removes the legacy shared-daemon wrapper and leaves App Server lifecycle management to the official Codex clients.
- On first activation, the extension removes its old `useSharedDaemon` preference. If `chatgpt.cliExecutable` still points to this extension's legacy wrapper, it restores the value recorded before shared mode was enabled, or clears the override when no safe previous value exists.
- Upgrade the extension on every local and remote VS Code or Cursor host where it is installed, because the application-scoped CLI override can live on the client machine.
