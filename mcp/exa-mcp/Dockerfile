FROM python:3.12-slim

# No CLI binary to fetch or checksum, unlike the sibling MCP services in this
# repository: every tool this server exposes lives on Exa's hosted MCP endpoint
# and is reached over HTTPS at request time.

RUN useradd --system --uid 10001 --create-home app
WORKDIR /app
COPY deploy/python/pyproject.toml deploy/python/uv.lock ./
COPY deploy/python/uv-version.txt /tmp/uv-version.txt
RUN pip install --no-cache-dir --index-url https://pypi.org/simple "uv==$(cat /tmp/uv-version.txt)" \
    && uv sync --frozen --no-dev
COPY server.py ./
USER app
EXPOSE 8765
CMD ["/app/.venv/bin/python", "server.py", "--host", "0.0.0.0", "--port", "8765"]
