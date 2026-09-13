# Codex New Agent Button

Adds two buttons to the right side of VS Code's editor tab bar:

- `+` runs `Codex: New Codex Agent`.
- The history button lists local Codex sessions and opens the selected session, including its existing messages, in a new Codex editor tab. Session listing uses `sqlite3` when present, and falls back to `python3`'s standard library so Linux TUI hosts without the CLI still work.
