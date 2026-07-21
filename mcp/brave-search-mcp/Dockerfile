FROM python:3.12-slim

ARG BX_VERSION=1.5.0
ARG TARGETARCH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && bx_arch="${TARGETARCH:-$(dpkg --print-architecture)}" \
    && case "$bx_arch" in amd64|arm64) ;; *) exit 1 ;; esac \
    && bx_name="bx-${BX_VERSION}-linux-${bx_arch}" \
    && bx_url="https://github.com/brave/brave-search-cli/releases/download/v${BX_VERSION}/${bx_name}" \
    && curl --proto '=https' --tlsv1.2 -fsSLo /tmp/bx "$bx_url" \
    && curl --proto '=https' --tlsv1.2 -fsSLo /tmp/bx.sha256 "${bx_url}.sha256" \
    && expected=$(cut -d' ' -f1 /tmp/bx.sha256) \
    && actual=$(sha256sum /tmp/bx | cut -d' ' -f1) \
    && test "$expected" = "$actual" \
    && install -m 755 /tmp/bx /usr/local/bin/bx \
    && bx --version

RUN useradd --system --uid 10001 --create-home app
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.10.9 \
    && uv sync --frozen --no-dev
COPY server.py ./
USER app
EXPOSE 8765
CMD ["/app/.venv/bin/python", "server.py", "--host", "0.0.0.0", "--port", "8765"]
