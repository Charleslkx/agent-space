FROM python:3.12-slim

ARG LARK_CLI_VERSION=1.0.81
ENV LARK_CLI_MCP_INSTALLED_VERSION=${LARK_CLI_VERSION}
ARG TARGETARCH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && cli_arch="${TARGETARCH:-$(dpkg --print-architecture)}" \
    && case "$cli_arch" in amd64|arm64) ;; *) echo "unsupported architecture: $cli_arch" >&2; exit 1 ;; esac \
    && asset="lark-cli-${LARK_CLI_VERSION}-linux-${cli_arch}.tar.gz" \
    && base="https://github.com/larksuite/cli/releases/download/v${LARK_CLI_VERSION}" \
    && curl --proto '=https' --tlsv1.2 -fsSLo /tmp/lark.tar.gz "$base/$asset" \
    && curl --proto '=https' --tlsv1.2 -fsSLo /tmp/checksums.txt "$base/checksums.txt" \
    && expected=$(awk -v f="$asset" '$2 == f { print $1 }' /tmp/checksums.txt) \
    && test -n "$expected" \
    && actual=$(sha256sum /tmp/lark.tar.gz | cut -d' ' -f1) \
    && test "$expected" = "$actual" \
    && tar -xzf /tmp/lark.tar.gz -C /tmp lark-cli \
    && install -m 755 /tmp/lark-cli /usr/local/bin/lark-cli \
    && lark-cli --version

RUN useradd --system --uid 10001 --home-dir /var/lib/lark-cli app
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.10.9 \
    && uv sync --frozen --no-dev
COPY server.py ./
USER app
EXPOSE 8765
CMD ["/app/.venv/bin/python", "server.py", "--host", "0.0.0.0", "--port", "8765"]
