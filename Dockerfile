# Application image for CommCare Connect Labs
# Uses a pre-built base image (from Dockerfile.base) that contains all Python
# dependencies, runtime system packages, and docker scripts.
# Uses a pre-built node image (from Dockerfile.node) that contains frontend
# bundles. Falls back to building from source if not provided.
#
# For local development without pre-built images, the defaults fall back
# to plain images — but you'll need to install deps separately.

ARG BASE_IMAGE=python:3.11-slim-bookworm
ARG NODE_IMAGE=node:18-bullseye

# ---------------------------------------------------------------------------
# Stage 1: Build frontend bundles (skipped if pre-built node image has bundles)
# ---------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS build-node

WORKDIR /app

# Install npm deps only if not already present (pre-built image has them)
COPY package.json package-lock.json /app/
RUN [ -d /app/node_modules ] || npm install

# Copy source and build only if bundles don't exist (pre-built image has them)
COPY . /app
RUN [ -d /app/commcare_connect/static/bundles/js ] || npm run build

# ---------------------------------------------------------------------------
# Stage 2: Final application image
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1
ENV DEBUG=0
ENV DJANGO_SETTINGS_MODULE=config.settings.labs_aws

# Copy frontend bundles from node build
COPY --from=build-node /app/commcare_connect/static/bundles /app/commcare_connect/static/bundles

WORKDIR /app

ARG APP_RELEASE="dev"
ENV APP_RELEASE=${APP_RELEASE}

# Copy application code
COPY --chown=django:django . /app

RUN python /app/manage.py collectstatic --noinput
RUN chown django:django -R staticfiles

USER django

EXPOSE 8000

ENTRYPOINT ["/entrypoint"]
