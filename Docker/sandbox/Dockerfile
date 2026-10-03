# Per-project Peon sandbox image (Kali base for both Docker sandbox and OpenShell).
# Swap SANDBOX_IMAGE / OPENSHELL_IMAGE freely; entrypoint writes /etc/peon/base-commands
# at start (once per container). Host caches that list per image — no per-project rediscovery.
FROM kalilinux/kali-rolling

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl unzip python3 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /etc/peon /workspace

COPY Docker/sandbox/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
