# Auth Simplification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace LabsUser/custom auth middleware with standard Django User + OAuth login.

**Architecture:** OAuth callback creates/updates a real Django User, calls `django.contrib.auth.login()`, stores OAuth token in session for API calls. All `_org_data` access moves from `request.user._org_data` to `request.session["labs_oauth"]["organization_data"]`. LabsURLWhitelistMiddleware removed; root redirect moves to urls.py.

**Tech Stack:** Django auth, Django User model (`commcare_connect.users.models.User`), session-based OAuth token storage.

**Design doc:** `docs/plans/2026-03-12-auth-simplification-design.md`

---

### Task 1: Add helper function for org_data access

Create a centralized helper so all `_org_data` consumers have one function to call.

**Files:**
- Modify: `commcare_connect/labs/context.py`

**Step 1: Add the helper function**

Add at the top of `context.py` (after the existing imports/constants):

```python
def get_org_data(request) -> dict:
    """Get organization data from session.

    Returns the organizations/programs/opportunities dict stored during OAuth login.
    """
    labs_oauth = getattr(request, "session", {}).get("labs_oauth", {}) if hasattr(request, "session") else {}
    return labs_oauth.get("organization_data", {})
```

**Step 2: Update `validate_context_access` to use it**

Replace lines 88-91:
```python
# Old:
if not hasattr(request.user, "_org_data"):
    return {}
org_data = request.user._org_data
```
With:
```python
org_data = get_org_data(request)
if not org_data:
    return {}
```

**Step 3: Update `try_auto_select_context` to use it**

Replace lines 199-202:
```python
# Old:
if not hasattr(request.user, "_org_data"):
    return None
org_data = request.user._org_data
```
With:
```python
org_data = get_org_data(request)
if not org_data:
    return None
```

**Step 4: Run tests**

Run: `pytest commcare_connect/labs/tests/test_context.py -v`

Tests will likely fail because they set up `LabsUser` with `_org_data`. Update tests to also put org data in `request.session["labs_oauth"]["organization_data"]`. The helper reads from session, so tests need session data.

**Step 5: Fix test_context.py**

In each test that creates a `LabsUser` and sets it on `request.user`, also set:
```python
request.session = {
    "labs_oauth": {
        "organization_data": session_data["organization_data"],
    }
}
```

Run tests again, verify they pass.

**Step 6: Commit**

```
feat: add get_org_data helper to read org data from session
```

---

### Task 2: Update all _org_data consumers to use get_org_data

**Files:**
- Modify: `commcare_connect/audit/views.py` (lines 51, 189, 1400, 1412)
- Modify: `commcare_connect/tasks/views.py` (lines 146, 202, 261, 581)
- Modify: `commcare_connect/labs/views.py` (refresh_org_data view — already reads from session, just verify)

**Step 1: Update audit/views.py**

Add import at top:
```python
from commcare_connect.labs.context import get_org_data
```

Replace all 4 occurrences of:
```python
org_data = getattr(self.request.user, "_org_data", {})
```
With:
```python
org_data = get_org_data(self.request)
```

**Step 2: Update tasks/views.py**

Add import at top:
```python
from commcare_connect.labs.context import get_org_data
```

Replace all occurrences of:
```python
org_data = getattr(self.request.user, "_org_data", {})
```
With:
```python
org_data = get_org_data(self.request)
```

And the one in the function-based view:
```python
org_data = getattr(request.user, "_org_data", {})
```
→
```python
org_data = get_org_data(request)
```

**Step 3: Run tests**

Run: `pytest commcare_connect/audit/ commcare_connect/tasks/ -v`

**Step 4: Commit**

```
refactor: use get_org_data() instead of request.user._org_data
```

---

### Task 3: Update is_labs_user checks

Since all users are now standard Django Users authenticated via OAuth, `is_labs_user` checks should just check `is_authenticated`.

**Files:**
- Modify: `commcare_connect/solicitations/views.py` (lines 34, 52, 71, 98)
- Modify: `commcare_connect/solicitations/views.py` (line 34)
- Modify: `commcare_connect/tasks/views.py` (lines 100, 136, 223)
- Modify: `commcare_connect/templates/layouts/header.html` (lines 8, 19, 24)

**Step 1: Simplify solicitations/views.py**

The `is_labs_user` checks currently guard access. Since this is a labs-only codebase, replace each:
```python
if getattr(self.request.user, "is_labs_user", False):
    return True
```
With:
```python
return self.request.user.is_authenticated
```

Remove the production fallback code after each `is_labs_user` check (the `# Production: Follow ...` branches are dead code).

For the decorator `solicitation_access_required`, replace:
```python
if getattr(request.user, "is_labs_user", False):
    return view_func(request, *args, **kwargs)
```
With:
```python
if request.user.is_authenticated:
    return view_func(request, *args, **kwargs)
```
And remove the production fallback after it.

**Step 2: Simplify solicitations/views.py**

Replace:
```python
return getattr(self.request.user, "is_labs_user", False)
```
With:
```python
return self.request.user.is_authenticated
```

**Step 3: Simplify tasks/views.py**

Replace the 3 occurrences of:
```python
if hasattr(self.request.user, "is_labs_user") and self.request.user.is_labs_user:
    has_token = True
```
With:
```python
has_token = self.request.user.is_authenticated
```

**Step 4: Simplify header.html**

Remove `is_labs_user` conditionals. Since this is labs-only, the labs version is always used:

- Line 8-12: Always show the labs overview link. Remove the `{% else %}` branch.
- Line 19-21: Always include the labs context selector. Remove the `{% if %}` wrapper.
- Line 24-68: Remove the entire `{% if not request.user.is_labs_user %}` block (production org selector).
- Line 88: `{% url 'labs:logout' %}` is already correct.

**Step 5: Run tests**

Run: `pytest -v`

**Step 6: Commit**

```
refactor: replace is_labs_user checks with is_authenticated
```

---

### Task 4: Modify OAuth callback to use Django User + login()

**Files:**
- Modify: `commcare_connect/labs/integrations/connect/oauth_views.py`

**Step 1: Update the callback**

Add imports:
```python
from django.contrib.auth import login
from commcare_connect.users.models import User
```

After the `request.session["labs_oauth"] = { ... }` block (line ~218), add User creation and login:

```python
# Create or update Django User from OAuth profile
first_name = profile_data.get("first_name", "")
last_name = profile_data.get("last_name", "")
name = f"{first_name} {last_name}".strip() or profile_data.get("username", "")

user, created = User.objects.update_or_create(
    username=profile_data.get("username"),
    defaults={
        "email": profile_data.get("email", ""),
        "name": name,
    },
)

# Log the user in via Django's standard auth
login(request, user, backend="django.contrib.auth.backends.ModelBackend")
```

**Step 2: Update the logout**

In `labs_logout`, add Django logout:
```python
from django.contrib.auth import logout
```

Replace the manual session clearing with:
```python
# Get username before clearing session
username = None
labs_oauth = request.session.get("labs_oauth")
if labs_oauth:
    username = labs_oauth.get("user_profile", {}).get("username")

# Django logout clears the session entirely
logout(request)

if username:
    logger.info(f"User {username} logged out")

messages.info(request, "You have been logged out.")
return redirect("labs:login")
```

**Step 3: Run the server and test login/logout manually**

Run: `python manage.py runserver`
- Visit http://localhost:8000/labs/login/
- Complete OAuth flow
- Verify you land on /labs/overview/ with user info displayed
- Verify logout works

**Step 4: Commit**

```
feat: use Django User with login() in OAuth callback
```

---

### Task 5: Remove LabsAuthenticationMiddleware and LabsURLWhitelistMiddleware

**Files:**
- Modify: `commcare_connect/labs/middleware.py` — delete both classes
- Modify: `config/settings/base.py` — fix LOGIN_URL, remove allauth settings
- Modify: `config/settings/local.py` — simplify middleware setup
- Modify: `config/settings/labs_aws.py` — simplify middleware setup
- Delete: `commcare_connect/labs/auth_backend.py`

**Step 1: Clean up base.py settings**

Set `LOGIN_URL = "/labs/login/"` (replace `"account_login"`).

Set `LOGIN_REDIRECT_URL = "/labs/overview/"` (replace `"users:redirect"`).

Remove the allauth settings block (lines ~344-363):
```python
# Remove all ACCOUNT_* and SOCIALACCOUNT_* settings
```

Remove `"commcare_connect.users.context_processors.allauth_settings"` from the context processors list.

Remove `"commcare_connect.users.middleware.OrganizationMiddleware"` from base MIDDLEWARE list (it's already being removed by local/labs_aws, just remove it from base).

**Step 2: Simplify local.py**

Remove:
```python
ACCOUNT_ALLOW_REGISTRATION = False
LOGIN_URL = "/labs/login/"
AUTHENTICATION_BACKENDS = [
    "commcare_connect.labs.auth_backend.LabsOAuthBackend",
]
```

Replace the middleware manipulation block with just adding LabsContextMiddleware:
```python
# Add labs context middleware after auth
MIDDLEWARE = list(MIDDLEWARE)
_auth_idx = MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware")
MIDDLEWARE.insert(_auth_idx + 1, "commcare_connect.labs.context.LabsContextMiddleware")
```

**Step 3: Simplify labs_aws.py**

Same approach as local.py. Remove LabsOAuthBackend from AUTHENTICATION_BACKENDS. Simplify middleware to only add LabsContextMiddleware:

```python
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
]

MIDDLEWARE = list(MIDDLEWARE)
_auth_idx = MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware")
MIDDLEWARE.insert(_auth_idx + 1, "commcare_connect.labs.context.LabsContextMiddleware")
```

Also remove `ACCOUNT_ALLOW_REGISTRATION`, `ACCOUNT_DEFAULT_HTTP_PROTOCOL`.

**Step 4: Add root redirect to urls.py**

In `config/urls.py`, add:
```python
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(url="/labs/overview/", permanent=False), name="home"),
    ...
]
```
Replace the existing `home` path that uses `TemplateView`.

**Step 5: Delete middleware classes and auth backend**

In `commcare_connect/labs/middleware.py`, delete `LabsAuthenticationMiddleware` and `LabsURLWhitelistMiddleware`. Keep the file if there's nothing left, or delete it entirely.

Delete `commcare_connect/labs/auth_backend.py`.

**Step 6: Run tests**

Run: `pytest -v`

**Step 7: Commit**

```
refactor: remove LabsAuthenticationMiddleware, LabsURLWhitelistMiddleware, and allauth config
```

---

### Task 6: Remove LabsUser class and update CLI/AI consumers

**Files:**
- Modify: `commcare_connect/labs/models.py` — remove LabsUser class
- Modify: `commcare_connect/ai/tasks.py` (~lines 125-174)
- Modify: `commcare_connect/labs/integrations/connect/cli/client.py` (~lines 263, 345)
- Modify: `commcare_connect/tasks/run_experiment_task_integration.py` (~line 71)
- Modify: `commcare_connect/audit/management/commands/test_async_audit.py`
- Modify: `commcare_connect/utils/middleware.py` (~line 49-50)

**Step 1: Update ai/tasks.py**

The AI task creates a LabsUser to pass to a MockRequest so that `_org_data` is available. Since org_data is now read from session, update the MockRequest to include org_data in its session dict (it already partially does this at line 161). Remove the LabsUser creation:

Replace the LabsUser creation block (~lines 144-149) and the comment about `_org_data` (~lines 171-173). Instead, just use the regular `user` and make sure `mock_request.session["labs_oauth"]["organization_data"]` is set (which it should be from the session_data already built above).

**Step 2: Update cli/client.py**

`get_labs_user_from_token()` creates a LabsUser from a CLI token. Change it to return a Django User instead:

```python
from commcare_connect.users.models import User

user, _ = User.objects.update_or_create(
    username=user_profile.get("username"),
    defaults={
        "email": user_profile.get("email", ""),
        "name": f"{user_profile.get('first_name', '')} {user_profile.get('last_name', '')}".strip(),
    },
)
return user
```

`create_cli_request()` similarly creates a LabsUser. Replace with the same User.objects.update_or_create pattern, and ensure `request.session["labs_oauth"]["organization_data"]` is set (it already is at line 433).

**Step 3: Update test files**

- `tasks/run_experiment_task_integration.py`: Replace LabsUser with User
- `audit/management/commands/test_async_audit.py`: Replace LabsUser with User
- `labs/tests/test_oauth_integration.py`: Remove `is_labs_user` assertion, update to check real User
- `labs/tests/test_context.py`: Replace LabsUser with User in test setup

**Step 4: Update utils/middleware.py**

The `CustomPGHistoryMiddleware` checks `hasattr(request.user, "_meta")` to skip LabsUser. Since all users are now Django Users (which have `_meta`), this guard is no longer needed. Simplify:

```python
# Old:
if request.user.is_authenticated and hasattr(request.user, "_meta"):
# New:
if request.user.is_authenticated:
```

**Step 5: Remove LabsUser from models.py**

Delete the `LabsUser` class and the `_get_empty_memberships` helper. Keep `LocalLabsRecord` and the cache model imports.

**Step 6: Run tests**

Run: `pytest -v`

**Step 7: Commit**

```
refactor: remove LabsUser class, use Django User everywhere
```

---

### Task 7: Clean up dead allauth templates and code

**Files:**
- Delete: `commcare_connect/templates/account/email.html`
- Delete: `commcare_connect/templates/account/email_confirm.html`
- Delete: `commcare_connect/templates/account/verified_email_required.html`
- Delete: `commcare_connect/templates/account/logout.html`
- Delete: `commcare_connect/users/adapters.py`
- Modify: `commcare_connect/users/models.py` — fix `get_absolute_url` (already partially done)
- Modify: `commcare_connect/users/views.py` — remove dead views
- Modify: `commcare_connect/templates/users/user_detail.html` — remove `account_email` reference
- Modify: `commcare_connect/organization/decorators.py` — fix `reverse("account_login")`
- Modify: `commcare_connect/users/context_processors.py` — remove `allauth_settings`
- Modify: `requirements/base.txt` — remove `django-allauth` package

**Step 1: Delete dead templates**

Delete the 4 template files listed above (they reference nonexistent allauth URLs).

**Step 2: Fix organization/decorators.py**

Replace `reverse("account_login")` with `"/labs/login/"`.

**Step 3: Clean up users/models.py**

`get_absolute_url` was already changed to return `/labs/overview/` (from earlier fix). Verify this is still correct.

**Step 4: Clean up users/views.py**

The `UserUpdateView` and `UserRedirectView` are dead code (users app URLs aren't in the URL config). Remove them if possible, or at minimum verify the `account_email` references are already fixed.

**Step 5: Remove allauth_settings context processor**

Delete `commcare_connect/users/context_processors.py` or remove the `allauth_settings` function and its reference in `base.py`.

**Step 6: Remove django-allauth from requirements**

Remove `django-allauth==0.54.0` from `requirements/base.txt`.

Run: `pip uninstall django-allauth`

**Step 7: Fix user_detail.html**

Replace `{% url 'account_email' %}` with `{% url 'labs:overview' %}` or remove the email link.

**Step 8: Delete users/adapters.py**

It's an empty file (just a comment about allauth removal).

**Step 9: Run tests and verify server starts**

Run: `pytest -v && python manage.py check`

**Step 10: Commit**

```
chore: remove allauth templates, adapters, package dependency, and dead code
```

---

### Task 8: Update CLAUDE.md and overview template

**Files:**
- Modify: `CLAUDE.md`
- Modify: `commcare_connect/templates/labs/overview.html`
- Modify: `commcare_connect/templates/labs/context_selector.html`

**Step 1: Update CLAUDE.md**

In the Architecture at a Glance section, replace:
```
- **OAuth session auth** — no Django User model for labs. `LabsUser` is transient ...
```
With:
```
- **OAuth + Django User** — OAuth login via production Connect creates/updates a Django User. OAuth tokens stored in session for API calls. Org data (organizations, programs, opportunities) in `request.session["labs_oauth"]["organization_data"]`.
```

Remove any references to `LabsUser`, `LabsAuthenticationMiddleware`, `LabsURLWhitelistMiddleware`.

**Step 2: Update overview.html**

The template uses `{{ user.name }}` which maps to the Django User's `name` field. Also uses `{{ user.username }}` and `{{ user.email }}` — both exist on Django User. Also uses `{{ user.organizations }}`, `{{ user.programs }}`, `{{ user.opportunities }}` which were LabsUser properties.

Replace these with session data access. Since templates can't call `get_org_data()`, add them to the view's `get_context_data`:

In `LabsOverviewView.get_context_data()`, add:
```python
labs_oauth = self.request.session.get("labs_oauth", {})
org_data = labs_oauth.get("organization_data", {})
context["organizations"] = org_data.get("organizations", [])
context["programs"] = org_data.get("programs", [])
context["opportunities"] = org_data.get("opportunities", [])
```

Then update the template to use `{{ organizations }}`, `{{ programs }}`, `{{ opportunities }}` instead of `{{ user.organizations }}`, etc.

**Step 3: Update context_selector.html**

Same issue: `{{ user.organizations }}`, `{{ user.programs }}`, `{{ user.opportunities }}`. These need to come from context or session.

Add a context processor or update the LabsContextMiddleware to inject org data lists into the template context. A simple context processor works best:

In `commcare_connect/labs/context.py`, add:
```python
def labs_org_data_context(request):
    """Template context processor: expose org data from session."""
    org_data = get_org_data(request)
    return {
        "user_organizations": org_data.get("organizations", []),
        "user_programs": org_data.get("programs", []),
        "user_opportunities": org_data.get("opportunities", []),
    }
```

Register it in `config/settings/base.py` template context processors.

Update `context_selector.html` to use `{{ user_organizations }}`, `{{ user_programs }}`, `{{ user_opportunities }}`.

Update `overview.html` similarly (can remove the manual context additions from the view).

**Step 4: Run tests and verify**

Run: `pytest -v && python manage.py runserver`
Visit `/labs/overview/` and verify all data displays correctly.

**Step 5: Commit**

```
docs: update CLAUDE.md and templates for Django User auth
```

---

### Task 9: Final verification

**Step 1: Full test suite**

Run: `pytest -v`

**Step 2: Manual smoke test**

1. Start server: `python manage.py runserver`
2. Visit `/` — should redirect to `/labs/overview/` (if authenticated) or `/labs/login/` (if not)
3. Complete OAuth login — should land on `/labs/overview/`
4. Verify user info displays (name, email, organizations, programs, opportunities)
5. Verify context selector works
6. Navigate to audit, tasks, workflow pages
7. Logout — should redirect to `/labs/login/`
8. Verify `/labs/overview/` redirects to login when not authenticated

**Step 3: Pre-commit**

Run: `pre-commit run --all-files`

**Step 4: Final commit if any fixes needed**

```
fix: address any issues found in final verification
```
