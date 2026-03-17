# Application image for CommCare Connect Labs
# Uses a pre-built base image (from Dockerfile.base) that contains all Python
# dependencies, runtime system packages, and docker scripts.
#
# For local development without a base image, the default BASE_IMAGE falls back
# to the plain Python image — but you'll need to install deps separately.

ARG BASE_IMAGE=python:3.11-slim-bookworm

# ---------------------------------------------------------------------------
# Stage 1: Build frontend bundles
# ---------------------------------------------------------------------------
FROM node:18-bullseye AS build-node

RUN nodejs -v && npm -v
WORKDIR /app

# Install npm deps first (layer cache on package files)
COPY package.json package-lock.json /app/
RUN npm install

# Then copy the rest and build
COPY . /app
RUN npm run build

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
