# syntax=docker/dockerfile:1

########## Stage 1: build the Rust analysis engine (code-graph-mcp) ##########
FROM rust:1-bookworm AS engine-builder
WORKDIR /build
COPY third_party/code-graph-mcp/ ./

# Apply the same pinned security updates as scripts/build_bridge.py
# (kept in sync with scripts/security_scan.py findings).
RUN for crate in quinn-proto rustls rustls-webpki crossbeam-epoch tar paste anyhow memmap2 rand; do \
      cargo update -p "$crate" || true; \
    done

# Offline/air-gapped binary: embed-model feature disabled (it downloads
# model files at build time, which Trellis never does by design).
RUN cargo build --release --no-default-features \
 && strip target/release/code-graph-mcp || true

########## Stage 2: runtime ##########
FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY server.py ./
COPY spec_manager.py ./
COPY visualizer.html ./
COPY vendor/ ./vendor/
COPY skills/ ./skills/
COPY --from=engine-builder /build/target/release/code-graph-mcp ./bin/code-graph-mcp

# Network sidecar defaults. Non-loopback binds REQUIRE TRELLIS_API_KEY
# (the server refuses to start without it) — pass it at run time:
#   docker run -e TRELLIS_API_KEY=secret -p 17317:17317 trellis
ENV TRELLIS_DATA_DIR=/data \
    TRELLIS_TRANSPORT=http \
    TRELLIS_HOST=0.0.0.0 \
    TRELLIS_PORT=17317

EXPOSE 17317
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:17317/health', timeout=4)"

# Default command serves everything on one port: REST API + visualizer UI +
# MCP endpoint at /mcp. Override the command for other modes, e.g.:
#   docker run trellis api          # REST API only
#   docker run trellis sync /repos/my-app --project-id my-app
ENTRYPOINT ["python", "server.py"]
CMD ["mcp"]
