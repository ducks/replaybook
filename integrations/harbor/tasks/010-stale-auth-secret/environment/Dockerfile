FROM ubuntu:24.04

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl docker.io \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /run/secrets
COPY rotated-auth.env /run/secrets/rotated-auth.env
RUN chmod 0444 /run/secrets/rotated-auth.env

WORKDIR /workspace
