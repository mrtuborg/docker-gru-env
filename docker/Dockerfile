# docker/Dockerfile
FROM ubuntu:24.04

ARG GH_CLI_VERSION=2.67.0
ARG USER_ID=1000
ARG USER_GID=1000
ENV USER=gru USER_ID=${USER_ID} USER_GID=${USER_GID}

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip git curl ca-certificates jq gettext-base \
    openssh-client sshpass bash nodejs npm rsync sudo && \
    rm -rf /var/lib/apt/lists/* && \
    # Ubuntu 24.04 ships a default 'ubuntu' user at UID/GID 1000 — remove it so
    # our build-arg UID/GID can be assigned cleanly to the 'gru' user.
    (userdel -r ubuntu 2>/dev/null || true) && \
    (groupdel ubuntu 2>/dev/null || true) && \
    groupadd --gid "${USER_GID}" "${USER}" && \
    useradd --uid "${USER_ID}" --gid "${USER_GID}" --create-home --shell /bin/bash "${USER}" && \
    usermod -aG sudo "${USER}" && \
    echo "${USER} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Install gh CLI
RUN curl -fsSL https://github.com/cli/cli/releases/download/v${GH_CLI_VERSION}/gh_${GH_CLI_VERSION}_linux_amd64.tar.gz \
    | tar -xz -C /usr/local/bin --strip-components=2 --wildcards '*/bin/gh'

# Install the standalone GitHub Copilot CLI (provides the `copilot` binary).
RUN npm install -g @github/copilot@1.0.62

# Provide a `gh copilot` shim that forwards to the standalone copilot CLI, so
# scripts using `gh copilot -- <args>` (e.g. overnight-run.sh) keep working.
# gh discovers extensions from ~/.local/share/gh/extensions/ (XDG data dir),
# so install into the gru user's home — must be done as root before USER switch.
RUN mkdir -p /home/gru/.local/share/gh/extensions/gh-copilot && \
    printf '#!/usr/bin/env bash\n[ "$1" = "--" ] && shift\nexec copilot "$@"\n' \
      > /home/gru/.local/share/gh/extensions/gh-copilot/gh-copilot && \
    chmod +x /home/gru/.local/share/gh/extensions/gh-copilot/gh-copilot && \
    chown -R gru:gru /home/gru/.local

# Python dependencies (only PyYAML)
COPY requirements.txt /tools/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tools/requirements.txt

# Copy copilot-workflow tools (read-only layer)
COPY . /tools/gru

# Install hooks.json template into a system-wide location so any user can access it.
RUN mkdir -p /etc/copilot/hooks && \
    cp /tools/gru/docker/hooks.json /etc/copilot/hooks/hooks.json

# Ensure bind-mount target directories are pre-created with correct ownership so
# the container user can write to them even before the volume is populated.
RUN mkdir -p /data/copilot /logs /workspace && \
    chown -R "${USER_ID}:${USER_GID}" /data/copilot /logs /workspace /tools/gru

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Switch to non-root user — files written to bind-mounts will have host UID/GID.
USER gru
ENV HOME=/home/gru

ENV COPILOT_DATA_HOME=/data/copilot

VOLUME ["/data/copilot", "/logs", "/workspace"]

ENTRYPOINT ["/entrypoint.sh"]
