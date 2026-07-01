# Contributing to CommCare Connect Labs

## Code Style

### Python

- **Formatter:** Black with `line-length = 119`
- **Import sorting:** isort with `profile = "black"`, `line_length = 119`
- **Linting:** flake8 (`max-line-length = 119`), pylint with `pylint_django` and `pylint_celery` plugins
- **Pre-commit hooks:** Install and run before committing:
  ```bash
  pre-commit install
  pre-commit run --all-files
  ```

### JavaScript / TypeScript

- **Formatter:** Prettier (tab-width 2, single quotes)
- **Framework:** React 18 with TypeScript
- **Styling:** TailwindCSS v4 (utility-first classes)
- **Build:** Webpack 5 — `npm run dev` (dev build), `npm run dev-watch` (watch mode)

### Frontend Approach

- **React/TS** for complex interactive components (workflow editor, AI chat)
- **Alpine.js** for lightweight interactivity on Django templates (toggles, dropdowns)
- **HTMX** for progressive enhancement (form submissions, partial updates)

## The data_access.py Pattern

Every labs app follows the same three-layer pattern:

1. **Proxy models** (`models.py`) — Subclass `LocalLabsRecord`, add `@property` accessors for typed access to the `data` JSON field
2. **Data access layer** (`data_access.py`) — Class that wraps `LabsRecordAPIClient` with domain-specific methods. Constructor takes `request` to extract OAuth token and context.
3. **Views** — Create a DataAccess instance from the request, call its methods, render templates with the returned lists

**Canonical examples:**

- Simplest: `connect_labs/tasks/data_access.py` — `TaskDataAccess`
- Most complex: `connect_labs/workflow/data_access.py` — `WorkflowDataAccess` with `BaseDataAccess`

**Full pattern documentation:** See [LABS_GUIDE.md](connect_labs/labs/LABS_GUIDE.md) for OAuth setup, API client usage, and code examples.

## Testing Conventions

- **Framework:** pytest with `@pytest.mark.django_db` for tests needing database access
- **Config:** `pyproject.toml` — settings: `--ds=config.settings.test --reuse-db`
- **Factories:** factory-boy for Django model fixtures (see `connect_labs/conftest.py` for shared fixtures)
- **Labs-specific tests:** Mock `LabsRecordAPIClient` and HTTP responses since labs tests cannot hit production
- **Running tests:**
  ```bash
  pytest                                    # Full suite
  pytest connect_labs/audit/tests/      # Single app
  pytest -k "test_audit_create"             # By name
  ```

## PR Process

See [pr_guidelines.md](pr_guidelines.md) for full details. Key points:

- Keep PRs small and focused — one feature or fix per PR
- Write comprehensive descriptions with screenshots/demos where relevant
- Include test coverage for new functionality
- Address AI review comments before requesting human review
- Use the `/deploy-labs` skill to deploy to the labs environment for testing

## How to Add a New Labs Feature

### Step 1: Create the app directory

```
connect_labs/your_app/
  __init__.py
  apps.py
  models.py
  data_access.py
  views.py
  urls.py
```

### Step 2: Define proxy models

```python
# your_app/models.py
from connect_labs.labs.models import LocalLabsRecord

class MyRecord(LocalLabsRecord):
    """Proxy for MyType records."""

    @property
    def title(self):
        return self.data.get("title")

    @property
    def status(self):
        return self.data.get("status", "draft")
```

### Step 3: Create data access layer

```python
# your_app/data_access.py
from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient
from .models import MyRecord

class MyAppDataAccess:
    def __init__(self, request=None, access_token=None, opportunity_id=None):
        if request and hasattr(request, "labs_context"):
            if not opportunity_id:
                opportunity_id = request.labs_context.get("opportunity_id")
        if request and not access_token:
            access_token = request.session["labs_oauth"]["access_token"]

        self.client = LabsRecordAPIClient(
            access_token=access_token,
            opportunity_id=opportunity_id,
        )

    def get_records(self) -> list[MyRecord]:
        return self.client.get_records(
            experiment="your_app",
            type="MyType",
            model_class=MyRecord,
        )

    def create_record(self, title: str, username: str) -> MyRecord:
        return self.client.create_record(
            experiment="your_app",
            type="MyType",
            data={"title": title, "status": "draft"},
            username=username,
        )
```

### Step 4: Create views and URL routes

```python
# your_app/views.py
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView
from .data_access import MyAppDataAccess

class MyRecordListView(LoginRequiredMixin, ListView):
    template_name = "your_app/list.html"

    def get_queryset(self):  # Returns a list, not a QuerySet
        data_access = MyAppDataAccess(request=self.request)
        return data_access.get_records()
```

```python
# your_app/urls.py
from django.urls import path
from . import views

app_name = "your_app"
urlpatterns = [
    path("", views.MyRecordListView.as_view(), name="list"),
]
```

### Step 5: Register in URL config and whitelist

Add your app's URLs to `config/urls.py`:

```python
path("your_app/", include("connect_labs.your_app.urls")),
```

Add the URL prefix to the whitelist in `connect_labs/labs/middleware.py`:

```python
WHITELISTED_PREFIXES = [
    # ... existing prefixes ...
    "/your_app/",
]
```

### Step 6: Add templates

Create templates under `connect_labs/templates/your_app/`.

### Step 7: Write tests

Mock `LabsRecordAPIClient` to test view logic without hitting production.

## Migration Guidelines

- **Labs data lives in production** via the LabsRecord API — there are no local migrations for domain data
- **Local migrations** are only for cache models (e.g., `labs/analysis/backends/sql/models.py`)
- **Do not modify** `opportunity/`, `organization/`, `program/`, or `users/` models — those are production ORM code
- If you add a Celery task, import it in your app's `tasks.py` so autodiscovery finds it
- Run `python manage.py makemigrations && python manage.py migrate` only for local model changes
