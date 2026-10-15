# Peon web (Phase 0+) — Django + orchestrator library

# Frontend bundles (frontend/ -> peon/static/dist). Tailwind scans the Django
# templates and legacy page scripts, so those are copied in too.
FROM node:22-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY peon/templates/ /build/peon/templates/
COPY peon/static/js/ /build/peon/static/js/
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=peon.config.settings \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git xz-utils \
    && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.1.1.tgz \
      | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
    && rm -rf /var/lib/apt/lists/*

# Keep pip console-scripts inside the venv so they cannot shadow native CLIs
# provisioned into /usr/local/bin (same basename collisions are common).
COPY requirements.txt .
RUN python -m venv "$VIRTUAL_ENV" \
    && pip install --no-cache-dir -r requirements.txt

# Marketplace skill install shells out to `npx openskills`. Official tarball
# includes npm/npx without Debian's npm dependency tree.
ARG NODE_VERSION=22.20.0
RUN curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
      | tar -xJ -C /usr/local --strip-components=1 \
    && node --version && npx --version

COPY manage.py .
COPY peon/ peon/
COPY orchestrator/ orchestrator/
COPY agent_runtime/ agent_runtime/
COPY skills/ skills/
COPY tools/ tools/
COPY --from=frontend /build/peon/static/dist/ peon/static/dist/
COPY Docker/entrypoint-web.sh /entrypoint-web.sh
COPY Docker/entrypoint-worker.sh /entrypoint-worker.sh

RUN chmod +x /app/manage.py /entrypoint-web.sh /entrypoint-worker.sh \
    && mkdir -p /workspaces/platform

EXPOSE 8000

ENTRYPOINT ["/entrypoint-web.sh"]
# One worker: terminal PTY sessions live in this process (terminal_session.py).
# ASGI so long-lived SSE streams (live console, terminal) don't pin WSGI threads.
CMD ["uvicorn", "peon.config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*", "--lifespan", "off", "--timeout-graceful-shutdown", "10"]
