# Per-project Peon sandbox image (Kali base for both Docker sandbox and OpenShell).
# Swap SANDBOX_IMAGE / OPENSHELL_IMAGE freely; entrypoint writes /etc/peon/base-commands
# at start (once per container). Host caches that list per image — no per-project rediscovery.
FROM kalilinux/kali-rolling

# Official Astral uv / uvx (same role as npm / npx for Python MCP packs).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl unzip nodejs npm python3 python3-pip python3-venv \
    python-is-python3 python3-requests python3-yaml python3-bs4 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf "$(command -v nodejs)" /usr/local/bin/node \
    && node --version \
    && npm --version \
    && npx --version \
    && uv --version \
    && uvx --version \
    && python3 -m venv /opt/peon-mcp-client \
    && /opt/peon-mcp-client/bin/pip install --no-cache-dir 'mcp>=1.10,<3' 'fastmcp>=2.0,<3' \
    && mkdir -p /etc/peon /opt/peon-mcp /workspace

# Playwright for every skill / MCP in the sandbox.
#   * distro chromium pulls the shared libraries the Playwright browser needs
#     (Kali is not a distro `playwright install-deps` knows);
#   * PLAYWRIGHT_BROWSERS_PATH is image-level and world-writable so a later
#     `install.post` (another Playwright pin) adds its revision beside this one;
#   * system python3 imports the venv's Playwright via a .pth that lists both
#     purelib and platlib (greenlet lives in platlib);
#   * `playwright` on PATH is the Python CLI. Node's package is installed for
#     npx, then the Python launcher is put back.
# The image build fails if headless Chromium cannot open a page.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV NODE_PATH=/usr/local/lib/node_modules:/usr/lib/node_modules
RUN set -eux \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /ms-playwright \
    && chmod 1777 /ms-playwright \
    && python3 -m venv /opt/playwright \
    && /opt/playwright/bin/pip install --no-cache-dir 'playwright>=1.49' \
    && /opt/playwright/bin/playwright install chromium \
    && pure="$(/opt/playwright/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')" \
    && plat="$(/opt/playwright/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["platlib"])')" \
    && sys="$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')" \
    && mkdir -p "$sys" \
    && { echo "$pure"; if [ "$plat" != "$pure" ]; then echo "$plat"; fi; } > "$sys/peon-playwright.pth" \
    && ln -sfn /opt/playwright/bin/playwright /usr/local/bin/playwright \
    && chmod -R a+rX /opt/playwright \
    && python3 - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("about:blank")
    assert page.url == "about:blank", page.url
    browser.close()
print("playwright-ok")
PY
# Node Playwright shares the browser cache. Restore the Python CLI afterwards
# and launch Chromium again so the symlink swap cannot leave Python broken.
RUN set -eux \
    && rm -f /usr/local/bin/playwright \
    && npm install -g --no-audit --no-fund playwright \
    && /usr/local/lib/node_modules/playwright/cli.js install chromium \
    && ln -sfn /opt/playwright/bin/playwright /usr/local/bin/playwright \
    && python3 - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("about:blank")
    browser.close()
print("playwright-ok")
PY

# Official MCP client venv + supervisor runtime (repo module, not an embedded string).
COPY orchestrator/mcp/supervisor_runtime.py /opt/peon-mcp/supervisor.py

COPY Docker/sandbox/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /opt/peon-mcp/supervisor.py

WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
