# Application image for CommCare Connect Labs
# Uses a pre-built base image (from Dockerfile.base) that contains all Python
# dependencies, runtime system packages, and docker scripts.
# Uses a pre-built node image (from Dockerfile.node) that contains frontend
# bundles. Falls back to building from source if not provided.
#
# For local development without pre-built images, the defaults fall back
# to plain images — but you'll need to install deps separately.

ARG BASE_IMAGE=python:3.13-slim-bookworm
ARG NODE_IMAGE=node:20-bookworm

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
RUN [ -d /app/connect_labs/static/bundles/js ] || npm run build

# ---------------------------------------------------------------------------
# Stage 1b: Build the Labs help site (user_docs/ -> static HTML)
# Served login-gated at /labs/docs/help/ by connect_labs/labs/help_site.py.
# Not --strict here: the docs bot commits straight to main, and a broken doc link
# must not block a deploy. The strict check runs on PRs (docs-deploy.yml).
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS build-docs

RUN pip install --no-cache-dir mkdocs==1.6.1 mkdocs-material==9.7.7
WORKDIR /docs
COPY mkdocs.yml /docs/
COPY user_docs /docs/user_docs
RUN mkdocs build --site-dir /docs/help_site

# ---------------------------------------------------------------------------
# Stage 2: Final application image
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1
ENV DEBUG=0
ENV DJANGO_SETTINGS_MODULE=config.settings.labs_aws

# Copy frontend bundles from node build
COPY --from=build-node /app/connect_labs/static/bundles /app/connect_labs/static/bundles

WORKDIR /app

# Copy application code
COPY --chown=django:django . /app
COPY --from=build-docs --chown=django:django /docs/help_site /app/help_site

RUN python /app/manage.py collectstatic --noinput
RUN chown django:django -R staticfiles

# Sentry release marker (the deploy passes the commit SHA). Declared last so a
# changed value can't invalidate the collectstatic layer above it.
ARG APP_RELEASE="dev"
ENV APP_RELEASE=${APP_RELEASE}

USER django

EXPOSE 8000

ENTRYPOINT ["/entrypoint"]
