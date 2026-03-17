# Codebase Simplification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove ~36K LOC of inherited production CommCare Connect code to simplify the repo for labs-only development.

**Architecture:** Surgical removal of 9 unused Django apps, gutting the opportunity app to keep only its models + migrations, removing production deploy infrastructure, and cleaning up all config references. Each task is one logical commit.

**Tech Stack:** Django 4.2, Python 3.11, pytest

**Design doc:** `docs/plans/2026-03-11-codebase-simplification-design.md`

---

### Task 1: Remove zero-dependency apps (batch 1: form_receiver, reports, microplanning, flags)

**Files:**
- Delete: `commcare_connect/form_receiver/` (entire directory)
- Delete: `commcare_connect/reports/` (entire directory)
- Delete: `commcare_connect/microplanning/` (entire directory)
- Delete: `commcare_connect/flags/` (entire directory)
- Modify: `config/settings/base.py:137-159` — remove from LOCAL_APPS
- Modify: `config/urls.py:32-38` — remove URL includes
- Modify: `config/api_router.py:5` — remove FormReceiver import
- Modify: `config/api_router.py:29` — remove FormReceiver URL pattern

**Step 1: Delete the app directories**

```bash
rm -rf commcare_connect/form_receiver
rm -rf commcare_connect/reports
rm -rf commcare_connect/microplanning
rm -rf commcare_connect/flags
```

**Step 2: Update config/settings/base.py LOCAL_APPS**

Remove these lines from LOCAL_APPS (lines 137-159):
```python
    "commcare_connect.flags",
    "commcare_connect.form_receiver",
    "commcare_connect.reports",
    "commcare_connect.microplanning",
```

**Step 3: Update config/urls.py**

Remove these URL patterns:
```python
    path("a/<slug:org_slug>/opportunity/", include("commcare_connect.opportunity.urls", namespace="opportunity")),
    path("a/<slug:org_slug>/program/", include("commcare_connect.program.urls", namespace="program")),
    path(
        "a/<slug:org_slug>/microplanning/", include("commcare_connect.microplanning.urls", namespace="microplanning")
    ),
    path("flags/", include("commcare_connect.flags.urls", namespace="flags")),
    path("admin_reports/", include("commcare_connect.reports.urls")),
```

Also remove:
```python
    path("hq/", include("commcare_connect.commcarehq.urls", namespace="commcarehq")),
    path("export/", include("commcare_connect.data_export.urls", namespace="data_export")),
```

**Step 4: Update config/api_router.py**

Replace entire file with minimal version — remove all opportunity/form_receiver imports and routes. Keep only the router setup and user endpoint:

```python
from django.conf import settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter, SimpleRouter

from commcare_connect.users.api.views import UserViewSet

if settings.DEBUG:
    router = DefaultRouter()
else:
    router = SimpleRouter()

router.register("users", UserViewSet)

app_name = "api"
urlpatterns = [
    path("", include(router.urls)),
]
```

**Step 5: Remove waffle from THIRD_PARTY_APPS and MIDDLEWARE**

In `config/settings/base.py`:
- Remove `"waffle"` from THIRD_PARTY_APPS (line 131)
- Remove `"vectortiles"` from THIRD_PARTY_APPS (line 134)
- Remove `"waffle.middleware.WaffleMiddleware"` from MIDDLEWARE (line 208)

**Step 6: Verify tests pass**

```bash
pytest commcare_connect/labs/ commcare_connect/audit/ commcare_connect/tasks/ commcare_connect/workflow/ commcare_connect/ai/ commcare_connect/solicitations/ commcare_connect/coverage/ commcare_connect/solicitations/ -x --ds=config.settings.test -o "addopts="
```

Expected: PASS (no labs code depends on removed apps)

**Step 7: Commit**

```bash
git add -A
git commit -m "refactor: remove form_receiver, reports, microplanning, flags apps

These production-only apps have zero labs dependencies. Also removes
waffle middleware, vectortiles, and related URL patterns."
```

---

### Task 2: Remove zero-dependency apps (batch 2: commcarehq, commcarehq_provider, connect_id_client, deid)

**Files:**
- Delete: `commcare_connect/commcarehq/` (entire directory)
- Delete: `commcare_connect/commcarehq_provider/` (entire directory)
- Delete: `commcare_connect/connect_id_client/` (entire directory)
- Delete: `commcare_connect/deid/` (entire directory, if it exists)
- Modify: `config/settings/base.py:137-159` — remove from LOCAL_APPS
- Modify: `config/settings/base.py:118-135` — remove allauth from THIRD_PARTY_APPS
- Modify: `config/settings/base.py:168-171` — remove allauth from AUTHENTICATION_BACKENDS

**Step 1: Delete the app directories**

```bash
rm -rf commcare_connect/commcarehq
rm -rf commcare_connect/commcarehq_provider
rm -rf commcare_connect/connect_id_client
rm -rf commcare_connect/deid
```

**Step 2: Update config/settings/base.py LOCAL_APPS**

Remove these lines:
```python
    "commcare_connect.commcarehq_provider",
    "commcare_connect.commcarehq",
```

**Step 3: Remove allauth from THIRD_PARTY_APPS**

Remove these lines from THIRD_PARTY_APPS:
```python
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
```

**Step 4: Remove allauth from AUTHENTICATION_BACKENDS**

Change AUTHENTICATION_BACKENDS to:
```python
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
]
```

**Step 5: Remove allauth URL from config/urls.py**

Remove line 22:
```python
    path("accounts/", include("allauth.urls")),
```

**Step 6: Verify tests pass**

```bash
pytest commcare_connect/labs/ commcare_connect/audit/ commcare_connect/tasks/ commcare_connect/workflow/ commcare_connect/ai/ -x --ds=config.settings.test -o "addopts="
```

Expected: PASS

**Step 7: Commit**

```bash
git add -A
git commit -m "refactor: remove commcarehq, commcarehq_provider, connect_id_client, deid, allauth

Removes CommCare HQ integration apps and allauth social auth. Labs uses
its own OAuth flow via LabsOAuthBackend, not allauth."
```

---

### Task 3: Remove data_export app, write upstream reference doc

**Files:**
- Delete: `commcare_connect/data_export/` (entire directory)
- Modify: `config/settings/base.py` — remove from LOCAL_APPS
- Create: `docs/upstream-reference.md`

**Step 1: Delete the app directory**

```bash
rm -rf commcare_connect/data_export
```

**Step 2: Remove from LOCAL_APPS**

Remove from config/settings/base.py:
```python
    "commcare_connect.data_export",
```

**Step 3: Write upstream reference doc**

Create `docs/upstream-reference.md`:

```markdown
# Upstream CommCare Connect Reference

This labs repo was forked from [dimagi/commcare-connect](https://github.com/dimagi/commcare-connect). Several production apps were removed during the March 2026 simplification. Here's where to find the original code if you need it for reference.

## Removed Apps

| App | Purpose | Upstream Location |
|-----|---------|-------------------|
| `data_export` | CSV/JSON export API for opportunity data. Provides `/export/labs_record/` endpoint that labs consumes via HTTP. | [commcare_connect/data_export/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/data_export) |
| `form_receiver` | Receives form submissions from CommCare HQ, creates UserVisit/CompletedWork records. | [commcare_connect/form_receiver/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/form_receiver) |
| `reports` | Invoice and delivery reports with django-tables2 and django-filters. | [commcare_connect/reports/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/reports) |
| `microplanning` | Geographic work area mapping with PostGIS and vector tiles. | [commcare_connect/microplanning/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/microplanning) |
| `flags` | Waffle feature flags for production rollouts. | [commcare_connect/flags/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/flags) |
| `connect_id_client` | Client library for ConnectID push notifications. | [commcare_connect/connect_id_client/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/connect_id_client) |
| `commcarehq` | HQServer model and CommCare HQ API integration. | [commcare_connect/commcarehq/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/commcarehq) |
| `commcarehq_provider` | django-allauth OAuth2 provider for CommCare HQ login. | [commcare_connect/commcarehq_provider/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/commcarehq_provider) |
| `deid` | De-identification utilities. | [commcare_connect/deid/](https://github.com/dimagi/commcare-connect/tree/main/commcare_connect/deid) |

## Gutted Apps

| App | What Was Removed | What Remains |
|-----|-----------------|--------------|
| `opportunity` | All views, URLs, admin, forms, tables, management commands, tests (~22K LOC) | `models.py` (all model definitions), `apps.py`, `__init__.py`, `migrations/` |

## How Labs Accesses Production Data

Labs does **not** run data_export locally. Instead:

1. `LabsRecordAPIClient` (in `commcare_connect/labs/integrations/connect/api_client.py`) makes HTTP calls to the **production** CommCare Connect server at `https://commcare-connect.org/export/labs_record/`.
2. The production server runs the upstream `data_export` app which queries the `LabsRecord` model.
3. Labs receives JSON responses and wraps them in `LocalLabsRecord` proxy objects.

## Fork Point

This repo diverged from upstream around October 2025. The `labs-main` branch contains all labs-specific development.
```

**Step 4: Verify tests pass**

```bash
pytest commcare_connect/labs/ commcare_connect/audit/ commcare_connect/workflow/ -x --ds=config.settings.test -o "addopts="
```

**Step 5: Commit**

```bash
git add -A
git commit -m "refactor: remove data_export app, add upstream reference doc

Labs calls production /export/ endpoint via HTTP — data_export code
never runs locally. Reference doc points to upstream GitHub for all
removed apps."
```

---

### Task 4: Gut the opportunity app

Keep only models.py, apps.py, __init__.py, and migrations/. Delete everything else.

**Files:**
- Delete: `commcare_connect/opportunity/views.py`
- Delete: `commcare_connect/opportunity/urls.py`
- Delete: `commcare_connect/opportunity/admin.py`
- Delete: `commcare_connect/opportunity/forms.py`
- Delete: `commcare_connect/opportunity/tables.py`
- Delete: `commcare_connect/opportunity/filters.py`
- Delete: `commcare_connect/opportunity/helpers.py`
- Delete: `commcare_connect/opportunity/export.py`
- Delete: `commcare_connect/opportunity/tasks.py`
- Delete: `commcare_connect/opportunity/visit_import.py`
- Delete: `commcare_connect/opportunity/deletion.py`
- Delete: `commcare_connect/opportunity/app_xml.py`
- Delete: `commcare_connect/opportunity/api/` (entire directory)
- Delete: `commcare_connect/opportunity/utils/` (entire directory)
- Delete: `commcare_connect/opportunity/management/` (entire directory)
- Delete: `commcare_connect/opportunity/tests/` (entire directory)
- Delete: `commcare_connect/opportunity/templates/` (if exists)
- Keep: `commcare_connect/opportunity/models.py`
- Keep: `commcare_connect/opportunity/apps.py`
- Keep: `commcare_connect/opportunity/__init__.py`
- Keep: `commcare_connect/opportunity/migrations/` (entire directory)

**Step 1: Delete non-essential files**

```bash
cd commcare_connect/opportunity
rm -f views.py urls.py admin.py forms.py tables.py filters.py helpers.py export.py tasks.py visit_import.py deletion.py app_xml.py
rm -rf api/ utils/ management/ tests/ templates/
```

**Step 2: Create a minimal admin.py**

Create `commcare_connect/opportunity/admin.py`:
```python
# Production admin registrations removed during labs simplification.
# See docs/upstream-reference.md for original code.
```

**Step 3: Remove opportunity URL patterns from config/urls.py**

These should already be removed in Task 1. Verify lines referencing `opportunity.urls` are gone.

**Step 4: Verify tests pass**

```bash
pytest commcare_connect/labs/ commcare_connect/audit/ commcare_connect/workflow/ commcare_connect/ai/ -x --ds=config.settings.test -o "addopts="
```

**Step 5: Commit**

```bash
git add -A
git commit -m "refactor: gut opportunity app to models + migrations only

Removes all views, URLs, admin, forms, tables, tests, management
commands (~22K LOC). Keeps models.py for DB schema and migrations.
See docs/upstream-reference.md for original code."
```

---

### Task 5: Refactor linking.py to remove UserVisit dependency

**Files:**
- Modify: `commcare_connect/labs/configurable_ui/linking.py:11` — remove UserVisit import

**Step 1: Read linking.py to understand current usage**

UserVisit is used only for type hints and accessing `.form_json` attribute. Replace with a duck-typed approach or use `dict` / `Any`.

**Step 2: Update linking.py**

Replace the import:
```python
from commcare_connect.opportunity.models import UserVisit
```

With no import — change type annotations to use `Any`:
```python
from typing import Any
```

Update method signatures:
- `def link_visits(self, visits: list[Any]) -> dict[str, list[Any]]:`
- `def get_child_id_from_visit(self, visit: Any) -> str | None:`
- `def _extract_identifier(self, visit: Any) -> str | None:`

The `.form_json` attribute access continues to work via duck typing.

**Step 3: Verify tests pass**

```bash
pytest commcare_connect/labs/ -x --ds=config.settings.test -o "addopts="
```

**Step 4: Commit**

```bash
git add commcare_connect/labs/configurable_ui/linking.py
git commit -m "refactor: remove UserVisit import from linking.py

Replace typed import with Any for duck-typing. The .form_json
attribute access works the same way."
```

---

### Task 6: Remove production deploy infrastructure

**Files:**
- Delete: `deploy/` (entire directory)
- Delete: `config/settings/production.py`
- Delete: `config/settings/staging.py`
- Delete: `.github/workflows/deploy.yml` (if present)
- Delete: `locale/` (empty directory)
- Modify: `config/settings/labs_aws.py:10` — change `from .staging import *` to `from .base import *`
- Modify: `config/settings/labs_aws.py` — inline needed staging settings

**Step 1: Delete directories and files**

```bash
rm -rf deploy/
rm -rf locale/
rm -f config/settings/production.py
rm -f .github/workflows/deploy.yml
```

**Step 2: Refactor labs_aws.py**

`labs_aws.py` currently does `from .staging import *`. Since we're deleting staging.py, we need to inline the settings labs actually needs.

Replace the entire file with:

```python
"""
Labs AWS environment settings (labs.connect.dimagi.com).

This settings file is ONLY for the AWS ECS Fargate deployment.
For local development (even of labs features), use config.settings.local.

Session-based OAuth authentication with no user database storage.
"""

import logging

from .base import *  # noqa
from .base import INSTALLED_APPS, MIDDLEWARE, env

# GENERAL
# ------------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)  # noqa: F405

# SECURITY
# ------------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=False)
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 60
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True)
SECURE_HSTS_PRELOAD = env.bool("DJANGO_SECURE_HSTS_PRELOAD", default=True)
SECURE_CONTENT_TYPE_NOSNIFF = env.bool("DJANGO_SECURE_CONTENT_TYPE_NOSNIFF", default=True)

# STORAGES (S3)
# ------------------------------------------------------------------------------
INSTALLED_APPS = list(INSTALLED_APPS)
INSTALLED_APPS += ["storages"]
AWS_STORAGE_BUCKET_NAME = env("DJANGO_AWS_STORAGE_BUCKET_NAME", default="commcare-connect-media")
AWS_QUERYSTRING_AUTH = False
_AWS_EXPIRY = 60 * 60 * 24 * 7
AWS_S3_OBJECT_PARAMETERS = {
    "CacheControl": f"max-age={_AWS_EXPIRY}, s-maxage={_AWS_EXPIRY}, must-revalidate",
}
AWS_S3_MAX_MEMORY_SIZE = env.int("DJANGO_AWS_S3_MAX_MEMORY_SIZE", default=100_000_000)
AWS_S3_REGION_NAME = env("AWS_DEFAULT_REGION", default=None)
AWS_S3_CUSTOM_DOMAIN = env("DJANGO_AWS_S3_CUSTOM_DOMAIN", default=None)
aws_s3_domain = AWS_S3_CUSTOM_DOMAIN or f"{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com"
MEDIA_URL = f"https://{aws_s3_domain}/media/"
STORAGES["default"]["BACKEND"] = "commcare_connect.utils.storages.MediaRootS3Boto3Storage"  # noqa: F405

# EMAIL (SES)
# ------------------------------------------------------------------------------
INSTALLED_APPS += ["anymail"]
ANYMAIL = {}

# SENTRY
# ------------------------------------------------------------------------------
SENTRY_DSN = env("SENTRY_DSN", default="")
APP_RELEASE = env("APP_RELEASE", default=None)
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger
    from sentry_sdk.integrations.redis import RedisIntegration

    ignore_logger("django.security.DisallowedHost")
    sentry_logging = LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[sentry_logging, DjangoIntegration(), CeleryIntegration(), RedisIntegration()],
        environment=env("DEPLOY_ENVIRONMENT", default="labs"),
        release=APP_RELEASE,
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0),
    )

# CSRF
# ------------------------------------------------------------------------------
CSRF_TRUSTED_ORIGINS = ["https://*.127.0.0.1"] + env.list("CSRF_TRUSTED_ORIGINS", default=[])
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"

# LABS ENVIRONMENT
# ------------------------------------------------------------------------------
IS_LABS_ENVIRONMENT = True
DEPLOY_ENVIRONMENT = "labs"

# OAuth configuration
LABS_OAUTH_SCOPES = ["export"]
ACCOUNT_ALLOW_REGISTRATION = False
LOGIN_URL = "/labs/login/"

# Custom authentication (session-based OAuth for users, Django auth for admin)
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "commcare_connect.labs.auth_backend.LabsOAuthBackend",
]

# Add labs app and custom_analysis
INSTALLED_APPS.append("commcare_connect.labs")
INSTALLED_APPS.append("commcare_connect.custom_analysis.chc_nutrition")

# Replace default AuthenticationMiddleware with labs version
MIDDLEWARE = list(MIDDLEWARE)
_auth_idx = MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware")
MIDDLEWARE[_auth_idx] = "commcare_connect.labs.middleware.LabsAuthenticationMiddleware"

SILENCED_SYSTEM_CHECKS = ["admin.E408"]
MIDDLEWARE.remove("commcare_connect.users.middleware.OrganizationMiddleware")
MIDDLEWARE.insert(_auth_idx + 1, "commcare_connect.labs.middleware.LabsURLWhitelistMiddleware")
MIDDLEWARE.insert(_auth_idx + 2, "commcare_connect.labs.context.LabsContextMiddleware")

# CommCare OAuth configuration
COMMCARE_HQ_URL = env("COMMCARE_HQ_URL", default="https://www.commcarehq.org")
COMMCARE_OAUTH_CLIENT_ID = env("COMMCARE_OAUTH_CLIENT_ID", default="")
COMMCARE_OAUTH_CLIENT_SECRET = env("COMMCARE_OAUTH_CLIENT_SECRET", default="")
COMMCARE_OAUTH_CLI_CLIENT_ID = env("COMMCARE_OAUTH_CLI_CLIENT_ID", default="")

# Open Chat Studio OAuth configuration
OCS_URL = env("OCS_URL", default="https://www.openchatstudio.com")
OCS_OAUTH_CLIENT_ID = env("OCS_OAUTH_CLIENT_ID", default="")
OCS_OAUTH_CLIENT_SECRET = env("OCS_OAUTH_CLIENT_SECRET", default="")

# Allow large POST bodies for snapshot save
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50 MB
```

**Step 3: Delete staging.py (now inlined into labs_aws.py)**

```bash
rm -f config/settings/staging.py
```

**Step 4: Verify Django starts locally**

```bash
python manage.py check --settings=config.settings.local
```

Expected: System check identified no issues.

**Step 5: Commit**

```bash
git add -A
git commit -m "refactor: remove production deploy infra, inline labs_aws settings

Removes deploy/ (Ansible/Kamal), production.py, staging.py, and
deploy.yml. Refactors labs_aws.py to import from base.py directly
with needed staging settings inlined."
```

---

### Task 7: Clean up requirements

**Files:**
- Modify: `requirements/base.in` — remove unused dependencies
- Delete: `requirements/production.in` (merged into labs_aws or base as needed)
- Delete: `requirements/production.txt` (compiled output)
- Modify: `Dockerfile` — remove production.txt install step

**Step 1: Update requirements/base.in**

Remove these dependencies that are only used by removed apps:
- `twilio` — used by connect_id_client for SMS
- `django-waffle` — flags app
- `django-vectortiles` — microplanning app
- `django-allauth` — removed auth provider
- `django-weasyprint` — reports PDF generation
- `django-allow-cidr` — staging middleware (now handled in labs_aws.py if needed)

Keep everything else (Django, DRF, celery, redis, httpx, etc. are used by labs).

**Step 2: Move production deps to base.in**

These deps from production.in are still needed by labs_aws deployment:
- `gunicorn[gevent]` — already used by Docker
- `psycopg2` — database driver
- `django-storages[boto3]` — S3 storage in labs_aws
- `django-anymail[amazon-ses]` — email in labs_aws

Add these to base.in, then delete production.in and production.txt.

**Step 3: Recompile requirements**

```bash
pip-compile requirements/base.in -o requirements/base.txt
```

**Step 4: Update Dockerfile**

Remove the production.txt install step. Change lines 48-50 from:
```dockerfile
RUN pip install --no-index -r /requirements/base.txt && \
    pip install --no-index -r /requirements/production.txt && \
    pip install --no-index -r /requirements/labs.txt --force-reinstall
```

To:
```dockerfile
RUN pip install --no-index -r /requirements/base.txt && \
    pip install --no-index -r /requirements/labs.txt --force-reinstall
```

Also update the wheel build step to not build production.txt wheels.

**Step 5: Verify local install works**

```bash
pip install -r requirements/base.txt -r requirements/dev.txt -r requirements/labs.txt
```

**Step 6: Commit**

```bash
git add -A
git commit -m "refactor: clean up requirements, merge production deps into base

Removes twilio, waffle, vectortiles, allauth, weasyprint, allow-cidr.
Moves gunicorn, psycopg2, storages, anymail into base.in since labs
deployment needs them. Removes production.in/txt."
```

---

### Task 8: Clean up remaining config references

**Files:**
- Modify: `config/settings/base.py` — remove pghistory/pgtrigger if unused by labs
- Modify: `config/urls.py` — remove organization_create import and URL if unused
- Modify: `.github/workflows/ci.yml` — update pytest to only test labs apps
- Check: Any remaining imports from removed apps in utils/, templates/, etc.

**Step 1: Search for remaining broken imports**

```bash
grep -r "from commcare_connect.form_receiver" --include="*.py" .
grep -r "from commcare_connect.reports" --include="*.py" .
grep -r "from commcare_connect.microplanning" --include="*.py" .
grep -r "from commcare_connect.flags" --include="*.py" .
grep -r "from commcare_connect.commcarehq" --include="*.py" .
grep -r "from commcare_connect.data_export" --include="*.py" .
grep -r "from commcare_connect.connect_id_client" --include="*.py" .
grep -r "from commcare_connect.deid" --include="*.py" .
grep -r "from commcare_connect.opportunity.views" --include="*.py" .
grep -r "from commcare_connect.opportunity.urls" --include="*.py" .
grep -r "from commcare_connect.opportunity.admin" --include="*.py" .
grep -r "from commcare_connect.opportunity.forms" --include="*.py" .
grep -r "from commcare_connect.opportunity.tables" --include="*.py" .
grep -r "from commcare_connect.opportunity.helpers" --include="*.py" .
grep -r "from commcare_connect.opportunity.tasks" --include="*.py" .
grep -r "from commcare_connect.opportunity.api" --include="*.py" .
```

Fix any remaining references found.

**Step 2: Check if pghistory/pgtrigger are used by labs apps**

```bash
grep -r "pghistory\|pgtrigger" commcare_connect/labs/ commcare_connect/audit/ commcare_connect/workflow/ commcare_connect/ai/ commcare_connect/tasks/ commcare_connect/solicitations/ commcare_connect/coverage/ --include="*.py"
```

If not used by labs, remove from THIRD_PARTY_APPS and requirements.

**Step 3: Update CI to test labs apps only**

In `.github/workflows/ci.yml`, update the pytest step to:
```yaml
      - name: Run tests
        run: |
          pytest commcare_connect/labs/ commcare_connect/audit/ commcare_connect/tasks/ commcare_connect/workflow/ commcare_connect/ai/ commcare_connect/solicitations/ commcare_connect/coverage/ commcare_connect/solicitations/ -x
```

**Step 4: Clean up config/urls.py**

Check if `organization_create` view (line 10, 24) is needed. If it only creates production organizations, remove it.

Also verify all remaining URL includes resolve to existing apps.

**Step 5: Full test run**

```bash
pytest --ds=config.settings.test -o "addopts="
```

Expected: PASS

**Step 6: Commit**

```bash
git add -A
git commit -m "refactor: clean up remaining config references and CI

Fix any broken imports from removed apps, update CI to test labs apps
only, remove unused third-party integrations."
```

---

### Task 9: Update documentation

**Files:**
- Modify: `CLAUDE.md` — update app map, remove references to deleted apps, update warnings
- Modify: `.claude/AGENTS.md` — update architecture reference
- Modify: `docs/LABS_ARCHITECTURE.md` — update if it references removed apps
- Modify: `MEMORY.md` — add note about simplification

**Step 1: Update CLAUDE.md**

Update the App Map table to remove deleted apps. Update the Critical Warnings section. Remove references to form_receiver, reports, microplanning, flags, commcarehq, data_export.

Simplify the warning about not querying production ORM models — now only users/org/program models remain as "core" apps, and their tables are still empty locally.

**Step 2: Update .claude/AGENTS.md**

Remove sections about removed apps. Update architecture description.

**Step 3: Update MEMORY.md**

Add a note about the simplification:
```markdown
## Codebase Simplification (2026-03-11)
- Removed 9 production apps: form_receiver, reports, microplanning, flags, connect_id_client, commcarehq, commcarehq_provider, data_export, deid
- Gutted opportunity app to models + migrations only
- Removed deploy/, production/staging settings
- labs_aws.py now imports from base.py directly
- See docs/upstream-reference.md for original code locations
```

**Step 4: Commit**

```bash
git add -A
git commit -m "docs: update documentation for simplified codebase

Updates CLAUDE.md, AGENTS.md, and memory files to reflect the removal
of production apps and deploy infrastructure."
```

---

### Task 10: Final verification

**Step 1: Run full test suite**

```bash
pytest --ds=config.settings.test -o "addopts="
```

**Step 2: Verify Django check passes**

```bash
python manage.py check --settings=config.settings.local
python manage.py check --settings=config.settings.test
```

**Step 3: Verify migrations are consistent**

```bash
python manage.py showmigrations --settings=config.settings.local
```

**Step 4: Verify dev server starts**

```bash
python manage.py runserver --settings=config.settings.local
```

Visit http://localhost:8000/labs/ — should load.

**Step 5: Run pre-commit**

```bash
pre-commit run --all-files
```

Fix any lint issues found.

**Step 6: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix: resolve lint and check issues from simplification"
```
