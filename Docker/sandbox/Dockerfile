# Per-project Peon sandbox image.
# Swap SANDBOX_IMAGE freely; entrypoint writes /etc/peon/base-commands at start
# (once per container). Host caches that list per image — no per-project rediscovery.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /etc/peon /workspace

COPY Docker/sandbox/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
