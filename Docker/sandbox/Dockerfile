# Per-project Peon sandbox image (Kali base for both Docker sandbox and OpenShell).
# Swap SANDBOX_IMAGE / OPENSHELL_IMAGE freely; entrypoint writes /etc/peon/base-commands
# at start (once per container). Host caches that list per image — no per-project rediscovery.
FROM kalilinux/kali-rolling

# Official Astral uv / uvx (same role as npm / npx for Python MCP packs).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl unzip nodejs npm python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf "$(command -v nodejs)" /usr/local/bin/node \
    && node --version \
    && npm --version \
    && npx --version \
    && uv --version \
    && uvx --version \
    && python3 -m venv /opt/peon-mcp-client \
    && /opt/peon-mcp-client/bin/pip install --no-cache-dir 'mcp>=1.10,<3' \
    && mkdir -p /etc/peon /opt/peon-mcp /workspace

# Official MCP client venv + supervisor runtime (repo module, not an embedded string).
COPY orchestrator/mcp/supervisor_runtime.py /opt/peon-mcp/supervisor.py

COPY Docker/sandbox/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /opt/peon-mcp/supervisor.py

WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
