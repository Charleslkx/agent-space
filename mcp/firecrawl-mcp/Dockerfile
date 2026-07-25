FROM python:3.12-slim

ARG FIRECRAWL_VERSION=1.19.27

# Resolved from `uname -m` at build time rather than Docker's ARG TARGETARCH,
# which is only auto-populated under BuildKit/buildx -- this stays correct
# under the classic builder too, on either an x86_64 or arm64 build host.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && case "$(uname -m)" in \
         x86_64) fc_arch=x64 ;; \
         aarch64) fc_arch=arm64 ;; \
         *) echo "unsupported build architecture: $(uname -m)" >&2; exit 1 ;; \
       esac \
    && fc_tarball="firecrawl-linux-${fc_arch}.tar.gz" \
    && fc_url="https://github.com/firecrawl/cli/releases/download/v${FIRECRAWL_VERSION}/${fc_tarball}" \
    && fc_checksums_url="https://github.com/firecrawl/cli/releases/download/v${FIRECRAWL_VERSION}/checksums.txt" \
    && curl --proto '=https' --tlsv1.2 -fsSLo /tmp/firecrawl.tar.gz "$fc_url" \
    && curl --proto '=https' --tlsv1.2 -fsSLo /tmp/checksums.txt "$fc_checksums_url" \
    && expected=$(awk -v f="$fc_tarball" '$2 == f { print $1 }' /tmp/checksums.txt) \
    && [ -n "$expected" ] || { echo "no checksum entry for $fc_tarball" >&2; exit 1; } \
    && actual=$(sha256sum /tmp/firecrawl.tar.gz | cut -d' ' -f1) \
    && test "$expected" = "$actual" \
    && tar -xzf /tmp/firecrawl.tar.gz -C /tmp \
    && install -m 755 "/tmp/firecrawl-linux-${fc_arch}" /usr/local/bin/firecrawl \
    && rm -f /tmp/firecrawl.tar.gz /tmp/checksums.txt "/tmp/firecrawl-linux-${fc_arch}" \
    && firecrawl --version

RUN useradd --system --uid 10001 --create-home app
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.10.9 \
    && uv sync --frozen --no-dev
COPY server.py ./
COPY skills ./skills
USER app
EXPOSE 8765
CMD ["/app/.venv/bin/python", "server.py", "--host", "0.0.0.0", "--port", "8765"]
