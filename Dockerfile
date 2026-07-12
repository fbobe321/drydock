# Drydock — a local-first, provider-agnostic terminal coding agent for your own LLM.
# The image is JUST the agent; point it at your OpenAI-compatible model server, e.g.
#   docker run -it --add-host=host.docker.internal:host-gateway fbobe3/drydock \
#     --base-url http://host.docker.internal:8000/v1 --model gemma4
# Clean-room, Apache-2.0. https://drydock-cli.com
FROM python:3.11-slim

LABEL org.opencontainers.image.title="drydock" \
      org.opencontainers.image.description="Local-first, provider-agnostic terminal coding agent for your own LLM. No accounts, no telemetry, no cloud." \
      org.opencontainers.image.url="https://drydock-cli.com" \
      org.opencontainers.image.source="https://github.com/fbobe321/drydock" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1

# Tools the agent's Bash/Git tools commonly need for real coding work.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl ca-certificates bash build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install the published package for a specific version (reproducible releases).
ARG DRYDOCK_VERSION
RUN pip install --no-cache-dir "drydock-cli==${DRYDOCK_VERSION}"

WORKDIR /work
ENTRYPOINT ["drydock"]
