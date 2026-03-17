# Auth Simplification Design

**Date:** 2026-03-12
**Status:** Approved

## Problem

Connect-labs inherited a hybrid auth stack from commcare-connect:
- `LabsUser` — a transient object mimicking Django's User, created from session on every request
- `LabsAuthenticationMiddleware` — custom middleware replacing Django's auth to populate `request.user` with LabsUser
- `LabsOAuthBackend` — a no-op auth backend (actual auth is in middleware)
- `LabsURLWhitelistMiddleware` — URL filtering + global auth gate (redirects unknown URLs to production)
- Dead django-allauth settings, templates, adapters, and package dependency

This was necessary when labs shared the commcare-connect codebase. Now that connect-labs is its own repo with its own DB, these workarounds add complexity without value.

## Design

### Use Django's User model with OAuth login

**OAuth callback** does `User.objects.update_or_create()` + `django.contrib.auth.login()`:
- Django's standard `AuthenticationMiddleware` handles `request.user` from the session
- No custom middleware needed for auth
- OAuth token stays in `request.session["labs_oauth"]` for API calls (unchanged)

**User model mapping:**
- OAuth `username` → `User.username`
- OAuth `email` → `User.email`
- OAuth `first_name` + `last_name` → `User.name` (the existing User model uses a combined `name` field)

### Remove LabsURLWhitelistMiddleware

Non-labs URLs will 404 naturally since they don't exist in `config/urls.py`. Auth requirement is handled per-view via `LoginRequiredMixin` (already in place on all labs views). Root path redirect (`/` → `/labs/overview/`) moves to `urls.py` as a `RedirectView`.

### Org data stays in session

The `_org_data` dict (organizations, programs, opportunities from OAuth) stays in `request.session["labs_oauth"]["organization_data"]`. The ~7 places that access `request.user._org_data` change to read from session instead.

### LabsContextMiddleware stays

Still needed to manage opportunity/program/org context selection. Its `_org_data` access points update to read from session.

## What gets removed

| Component | Location | Reason |
|-----------|----------|--------|
| `LabsUser` class | `labs/models.py` | Replaced by Django User |
| `LabsAuthenticationMiddleware` | `labs/middleware.py` | Django's AuthenticationMiddleware handles this |
| `LabsOAuthBackend` | `labs/auth_backend.py` | Use Django's ModelBackend |
| `LabsURLWhitelistMiddleware` | `labs/middleware.py` | Non-labs URLs 404 naturally |
| `is_labs_user` checks | templates, views | Only one user type now |
| allauth settings | `config/settings/base.py` | allauth not used |
| allauth templates | `templates/account/*` | Dead templates |
| allauth adapters | `users/adapters.py` | Empty file |
| allauth package | `requirements/base.txt` | Not installed in apps |
| Dead user views | `users/views.py` | `UserRedirectView`, `UserUpdateView` reference nonexistent URLs |
| `users/context_processors.allauth_settings` | context processor | Only exposed `ACCOUNT_ALLOW_REGISTRATION` |

## What gets modified

| Component | Change |
|-----------|--------|
| OAuth callback (`oauth_views.py`) | Add `User.update_or_create()` + `login()` |
| OAuth logout (`oauth_views.py`) | Add `django.contrib.auth.logout()` |
| `config/settings/base.py` | `LOGIN_URL = "/labs/login/"`, remove allauth settings, clean MIDDLEWARE |
| `config/settings/local.py` | Remove middleware insertion dance, remove `LabsOAuthBackend` |
| `config/settings/labs_aws.py` | Same as local.py |
| `labs/context.py` | `_org_data` reads from session instead of `request.user._org_data` |
| `audit/views.py` | `_org_data` reads from session |
| `tasks/views.py` | `_org_data` reads from session, remove `is_labs_user` checks |
| `solicitations/views.py` | Remove `is_labs_user` checks |
| `solicitations/views.py` | Remove `is_labs_user` checks |
| `templates/layouts/header.html` | Remove `is_labs_user` conditionals, simplify to one mode |
| `config/urls.py` | Add root redirect to `/labs/overview/` |
| `organization/decorators.py` | Fix `reverse("account_login")` → `/labs/login/` |

## Data flow after change

```
Login:
  /labs/login/ → OAuth authorize → callback
    → User.objects.update_or_create(username=..., defaults={email, name})
    → django.contrib.auth.login(request, user)
    → session["labs_oauth"] = {access_token, refresh_token, expires_at, user_profile, organization_data}
    → redirect to next_url

Request:
  Django AuthenticationMiddleware → request.user (real User from session/DB)
  LabsContextMiddleware → request.labs_context (unchanged)
  Views: session["labs_oauth"]["organization_data"] for org/program/opp lists

Logout:
  django.contrib.auth.logout(request)  # clears session
  redirect to /labs/login/
```
