FROM python:3.12-slim

RUN useradd --system --uid 10001 --user-group --home-dir /var/lib/colab-mcp app
WORKDIR /app
COPY deploy/python/pyproject.toml deploy/python/uv.lock ./
COPY deploy/python/uv-version.txt /tmp/uv-version.txt
RUN pip install --no-cache-dir --index-url https://pypi.org/simple "uv==$(cat /tmp/uv-version.txt)" \
    && uv sync --frozen --no-dev \
    && /app/.venv/bin/colab version
COPY server.py cli.py colab_entry.py ./
USER app
EXPOSE 8765
CMD ["/app/.venv/bin/python", "-B", "server.py", "--host", "0.0.0.0", "--port", "8765"]
