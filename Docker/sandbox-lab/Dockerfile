# Toolsmith lab image — the project sandbox image plus a pinned OpenCode CLI.
#
# Used ONLY by the lab container (peon/projects/lab_runtime.py). Never
# used for engagement project sandboxes or OpenShell — those stay on
# peon-sandbox:local, unmodified, with no OpenCode binary at all.
#
# Pinned, not fetched at authoring time: a version bump here is a deliberate,
# reviewed change (rebuild this image), not a silent "whatever opencode.ai
# serves today" surprise the next time someone drafts a skill.
FROM peon-sandbox:local

ARG OPENCODE_VERSION=1.19.4

RUN VERSION="${OPENCODE_VERSION}" curl -fsSL https://opencode.ai/install | bash \
    && ln -sf "$HOME/.opencode/bin/opencode" /usr/local/bin/opencode \
    && opencode --version
