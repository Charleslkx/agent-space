# Lark Obsidian Publish MCP

Self-contained uv project for publishing Markdown to Lark/Feishu Docx and managing embedded whiteboards.

## Requirements

- Python 3.11+
- `uv`
- `lark-cli` on `PATH`, configured with a verified user login

## Install and test

```bash
uv sync --frozen
uv run python -m unittest discover -s scripts -p 'test_*.py'
```

Local HTTP remains available without a token because it binds only to loopback:

```bash
uv run python scripts/mcp_server.py --transport http --host 127.0.0.1 --port 8765
```

## Codex installation

Install the complete directory and its locked environment:

```bash
rsync -a --delete --exclude .venv --exclude .lark_publish \
  ./lark-obsidian-publish/ ~/.codex/skills/lark-obsidian-publish/
cd ~/.codex/skills/lark-obsidian-publish
uv sync --frozen
lark-cli auth status --json --verify
```

Start the local server from the installed directory, then register its HTTP endpoint:

```bash
cd ~/.codex/skills/lark-obsidian-publish
uv run python scripts/mcp_server.py \
  --transport http --host 127.0.0.1 --port 8765

codex mcp add lark-obsidian-publish \
  --url http://127.0.0.1:8765/mcp
codex mcp get lark-obsidian-publish
```

If the entry already exists, replace only that entry:

```bash
codex mcp remove lark-obsidian-publish
codex mcp add lark-obsidian-publish \
  --url http://127.0.0.1:8765/mcp
```

Restart Codex after installing or replacing the skill so it reloads `SKILL.md`.

## Personal HTTPS deployment

Create a random token and store it in the server's secret manager or service environment. Do not commit it or pass it as a command-line argument.

```bash
export LARK_MCP_AUTH_TOKEN="$(openssl rand -hex 32)"
uv run python scripts/mcp_server.py \
  --transport http --host 0.0.0.0 --port 8765 \
  --tls-cert /etc/letsencrypt/live/mcp.example.com/fullchain.pem \
  --tls-key /etc/letsencrypt/live/mcp.example.com/privkey.pem
```

Connect clients to `https://mcp.example.com:8765/mcp` and pass the token without the `Bearer` prefix:

```python
from fastmcp import Client

async with Client("https://mcp.example.com:8765/mcp", auth="your-token") as client:
    print(await client.list_tools())
```

Register the public endpoint in Codex without writing the token into `config.toml`:

```bash
export LARK_MCP_AUTH_TOKEN="your-existing-server-token"
codex mcp remove lark-obsidian-publish
codex mcp add lark-obsidian-publish \
  --url https://mcp.example.com:8765/mcp \
  --bearer-token-env-var LARK_MCP_AUTH_TOKEN
```

The Codex process must inherit `LARK_MCP_AUTH_TOKEN`. On macOS Desktop, set it before launching Codex or provide it through the process/service environment; the MCP configuration stores only the variable name.

## Verification

```bash
uv run python -m unittest discover -s scripts -p 'test_mcp_server.py'
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/mcp
codex mcp get lark-obsidian-publish
```

An unauthenticated public HTTPS request must return `401`; a raw local GET may return `406` because MCP expects a protocol request rather than a browser page.

Non-loopback binding fails closed unless the token, certificate, and key are all present. TLS files may use absolute paths because they are server configuration, not `lark-cli` payload paths.

The server machine must complete `lark-cli auth login` itself. Copying this folder does not copy Feishu credentials.

## Distribution

Copy the entire folder, including `uv.lock`, then run `uv sync --frozen`. Runtime state and secrets are ignored; `.lark_publish/` is cleaned after each operation.
