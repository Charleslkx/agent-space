# Codex New Agent Button

Adds buttons to the right side of VS Code's editor tab bar:

- `+` runs `Codex: New Codex Agent`.
- The history button lists local Codex sessions and opens the selected session, including its existing messages, in a new Codex editor tab. Session listing uses `sqlite3` when present, and falls back to `python3`'s standard library so Linux TUI hosts without the CLI still work.
- The sign-out button exits the current Codex session: it closes the conversation tab so the official extension can `thread/unsubscribe`, then removes leftover `~/.codex/thread-writer-locks/*.lock` files that no process still holds.
- On Linux and macOS, the extension routes the official Cursor Codex extension through the shared local `codex app-server` daemon. Multiple Cursor/terminal clients therefore use one session owner instead of competing writer processes.

Settings:

- `codexNewAgentButton.exitOnEditorClose` (default `true`) runs that exit path when a Codex tab is closed.
- `codexNewAgentButton.releaseStaleWriterLocks` (default `true`) only deletes stale writer locks, never live ones.
- `codexNewAgentButton.useSharedDaemon` (default `false`) sets the official extension's `chatgpt.cliExecutable` to the included proxy wrapper. Enable it through the command below so consent to remote-control access is explicit, then reload Cursor.

Commands:

- `Codex: Use Shared App Server Daemon` explains the remote-control security boundary and asks for confirmation. It enables remote control for a managed daemon, or reuses an already-proxyable App Server owned by Codex Desktop, then offers to reload Cursor.
- `Codex: Restore Bundled App Server` restores the previous `chatgpt.cliExecutable` value.

The wrapper resolves `codex` from `CODEX_SHARED_CLI`, `PATH`, or common user install locations, attempts to start the managed daemon if needed, and then runs `codex app-server proxy`. If Codex Desktop already owns the control socket, the daemon start attempt may be rejected and the wrapper connects to the existing App Server instead. The installed Codex CLI must support the `app-server daemon` and `app-server proxy` commands. Because the app-server protocol is versioned with Codex, keep the CLI reasonably close to the version bundled by the official Cursor extension.
