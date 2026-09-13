# Peon web (Phase 0+) — Django + orchestrator library
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=peon.config.settings \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.1.1.tgz \
      | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
    && rm -rf /var/lib/apt/lists/*

# Keep pip console-scripts inside the venv so they cannot shadow native CLIs
# provisioned into /usr/local/bin (same basename collisions are common).
COPY requirements.txt .
RUN python -m venv "$VIRTUAL_ENV" \
    && pip install --no-cache-dir -r requirements.txt

COPY manage.py .
COPY peon/ peon/
COPY orchestrator/ orchestrator/
COPY skills/ skills/
COPY tools/ tools/
COPY Docker/entrypoint-web.sh /entrypoint-web.sh
COPY Docker/entrypoint-worker.sh /entrypoint-worker.sh

RUN chmod +x /app/manage.py /entrypoint-web.sh /entrypoint-worker.sh \
    && mkdir -p /data

EXPOSE 8000

ENTRYPOINT ["/entrypoint-web.sh"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
