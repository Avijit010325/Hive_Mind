# The shared Linux computer agents work in. Deliberately unprivileged: no sudo, no root.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash ca-certificates curl wget git jq zip unzip procps psmisc less nano nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --shell /bin/bash agent \
    && mkdir /workspace && chown agent:agent /workspace

COPY --chown=agent:agent runner.py /opt/runner.py
USER agent
ENV HOME=/home/agent PATH=/home/agent/.local/bin:$PATH PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_USER=1 NPM_CONFIG_PREFIX=/home/agent/.local
WORKDIR /workspace
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=6 CMD curl -fs http://127.0.0.1:7000/health || exit 1
CMD ["python3", "/opt/runner.py"]
