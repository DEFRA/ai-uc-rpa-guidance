# Set default values for build arguments
ARG PARENT_VERSION=2.2.1-python3.14.3
ARG PORT=8085
ARG PORT_DEBUG=8086

FROM defradigital/python-development:${PARENT_VERSION} AS development

USER root

# Optionally trust a corporate/TLS-inspecting proxy CA. `ca-bundle` is a named
# build context that defaults to an empty directory, so this is a no-op unless
# CA_BUNDLE_DIR points at a directory of PEM certificates. A build context is
# used rather than a build secret because BuildKit hashes context contents:
# the layer rebuilds when — and only when — the certificates change.
COPY --from=ca-bundle . /tmp/ca-bundle/
RUN find /tmp/ca-bundle -type f \( -name '*.crt' -o -name '*.pem' \) -exec cat {} + \
      | awk '/BEGIN CERTIFICATE/{b=""} {b=b $0 ORS} /END CERTIFICATE/{if (!seen[b]++) printf "%s", b}' \
      >> /etc/ssl/certs/ca-certificates.crt && \
    rm -rf /tmp/ca-bundle

USER nonroot

ENV PATH="/home/nonroot/.venv/bin:${PATH}"
ENV LOG_CONFIG="logging-dev.json"
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV AWS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /home/nonroot

COPY --chown=nonroot:nonroot pyproject.toml .
COPY --chown=nonroot:nonroot README.md .
COPY --chown=nonroot:nonroot uv.lock .
COPY --chown=nonroot:nonroot app/ ./app/
COPY --chown=nonroot:nonroot data/ ./data/

RUN --mount=type=cache,target=/home/nonroot/.cache/uv,uid=1000,gid=1000 \
    uv sync --locked --link-mode=copy

COPY --chown=nonroot:nonroot logging-dev.json .

ARG PORT=8085
ARG PORT_DEBUG=8086
ENV PORT=${PORT}
EXPOSE ${PORT} ${PORT_DEBUG}

CMD [ "/home/nonroot/.venv/bin/ai-uc-rpa-guidance" ]

FROM defradigital/python:${PARENT_VERSION} AS production

ENV PATH="/home/nonroot/.venv/bin:${PATH}"
ENV LOG_CONFIG="logging.json"

USER root

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

USER nonroot

WORKDIR /home/nonroot

COPY --from=development /home/nonroot/pyproject.toml .
COPY --chown=nonroot:nonroot README.md .
COPY --from=development /home/nonroot/uv.lock .
COPY --from=development /home/nonroot/app ./app
COPY --from=development /home/nonroot/data ./data

COPY logging.json .

RUN --mount=type=cache,target=/home/nonroot/.cache/uv,uid=1000,gid=1000 \
    --mount=from=development,source=/home/nonroot/.local/bin/uv,target=/home/nonroot/.local/bin/uv \
    uv sync --locked --compile-bytecode --link-mode=copy --no-dev

ARG PORT
ENV PORT=${PORT}
EXPOSE ${PORT}

CMD [ "/home/nonroot/.venv/bin/ai-uc-rpa-guidance" ]
