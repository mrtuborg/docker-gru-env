# docker/Dockerfile
FROM ubuntu:24.04

ARG GH_CLI_VERSION=2.67.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip git curl ca-certificates jq gettext-base \
    openssh-client sshpass bash nodejs npm rsync && \
    rm -rf /var/lib/apt/lists/*

# Install gh CLI
RUN curl -fsSL https://github.com/cli/cli/releases/download/v${GH_CLI_VERSION}/gh_${GH_CLI_VERSION}_linux_amd64.tar.gz \
    | tar -xz -C /usr/local/bin --strip-components=2 --wildcards '*/bin/gh'

# Install the standalone GitHub Copilot CLI (provides the `copilot` binary).
RUN npm install -g @github/copilot@1.0.62

# Provide a `gh copilot` shim that forwards to the standalone copilot CLI, so
# scripts using `gh copilot -- <args>` (e.g. overnight-run.sh) keep working.
RUN mkdir -p /root/.local/share/gh/extensions/gh-copilot && \
    printf '#!/usr/bin/env bash\n[ "$1" = "--" ] && shift\nexec copilot "$@"\n' \
      > /root/.local/share/gh/extensions/gh-copilot/gh-copilot && \
    chmod +x /root/.local/share/gh/extensions/gh-copilot/gh-copilot

# Python dependencies (only PyYAML)
COPY requirements.txt /tools/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tools/requirements.txt

# Copy copilot-workflow tools (read-only layer)
COPY . /tools/gru

# Install hooks.json template
RUN mkdir -p /root/.copilot/hooks && \
    cp /tools/gru/docker/hooks.json /root/.copilot/hooks/hooks.json

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV COPILOT_DATA_HOME=/data/copilot

VOLUME ["/data/copilot", "/logs", "/workspace"]

ENTRYPOINT ["/entrypoint.sh"]
