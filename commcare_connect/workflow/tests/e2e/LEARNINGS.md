# E2E Testing Learnings for Workflow Templates

Hard-won lessons from building the first Playwright E2E test suite for CommCare Connect Labs workflow templates.

## Running the Tests

```bash
# Refresh CLI token first (expires after 1 hour)
python manage.py get_cli_token

# Run the E2E tests
pytest commcare_connect/workflow/tests/e2e/ --ds=config.settings.local -o "addopts=" -v --opportunity-id=874
```

Key flags:

- `--ds=config.settings.local` — overrides pyproject.toml's default Django settings
- `-o "addopts="` — clears project-level pytest addopts (which would conflict)
- `--opportunity-id=874` — configurable per test run (default: 874)

## Infrastructure Decisions

### No Test Database

E2E tests hit a running Django server, not a test database. Override `django_db_setup` as a no-op:

```python
@pytest.fixture(scope="session")
def django_db_setup():
    pass
```

Without this, pytest-django tries to create a test database and hits `SynchronousOnlyOperation` because Playwright runs inside an async event loop.

### DJANGO_ALLOW_ASYNC_UNSAFE

Set `os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")` at module level in conftest.py. This prevents Django's async safety check from blocking pytest-django's own database setup machinery. The E2E tests don't use Django ORM directly — they talk to the server over HTTP.

### Session-Based Auth via /labs/test-auth/

A DEBUG-only view at `/labs/test-auth/` reads the CLI token (from `get_cli_token`), introspects it against production OAuth, and writes `labs_oauth` into the Django session. The Playwright browser context then carries this session cookie for all subsequent requests.

**The CLI token expires after 1 hour.** If tests fail with `401` at `/labs/test-auth/`, run `python manage.py get_cli_token` to refresh it.

## Windows-Specific Gotchas

### subprocess.PIPE Deadlock (THE BIG ONE)

**Never use `stdout=subprocess.PIPE` or `stderr=subprocess.PIPE`** when starting Django runserver or Celery worker as subprocesses on Windows.

Django's runserver writes request logs to stderr. On Windows, the pipe buffer is only 4-8KB. When it fills, the child process **blocks on write**, freezing the entire server. Every HTTP request hangs indefinitely.

**Symptoms:** POST requests to the server hang forever. GET requests may work initially but eventually hang too. Python `urllib.request` from outside Playwright also hangs — proving it's server-side.

**Fix:** Use `subprocess.DEVNULL` for both stdout and stderr. If you need logs for debugging, redirect to a file:

```python
log_file = open("server.log", "w")
proc = subprocess.Popen([...], stdout=log_file, stderr=subprocess.STDOUT)
```

### Celery Worker: --pool=solo

Use `--pool=solo` for the Celery worker on Windows. The default prefork pool doesn't work on Windows.

## Playwright Patterns

### Use domcontentloaded, Not networkidle

Workflow pages have persistent connections (SSE streams, polling endpoints). `wait_for_load_state("networkidle")` will **never resolve** because the network is never idle.

Always use:

```python
page.wait_for_load_state("domcontentloaded")
```

### Modal Button Scoping

The workflow list page has cards with workflow name buttons. The template creation modal also has buttons with the same workflow name text. To avoid clicking the wrong one:

```python
modal = page.locator(".fixed.inset-0.z-50")
btn = modal.locator("button[type='submit']").filter(has_text="Weekly Audit with AI Review")
```

### Waiting for Either/Or Elements

After an async operation, the UI may show different views depending on the outcome. Use Playwright's `.or_()` method (not CSS comma selectors, which don't work with `text=`):

```python
page.get_by_text("Audit Sessions Created").or_(
    page.get_by_text("Visit Selection")
).first.wait_for(timeout=30_000)
```

### Form Submissions via page.request.post()

Some form POSTs trigger server-side API calls that take several seconds. Playwright's navigation-based form submission can timeout. Use `page.request.post()` instead:

```python
csrf_token = modal.locator("input[name='csrfmiddlewaretoken']").first.input_value()
response = page.request.post(
    f"{live_server_url}/labs/workflow/create/",
    form={"csrfmiddlewaretoken": csrf_token, "template": "audit_with_ai_review"},
    timeout=60_000,
)
```

Then reload the page to pick up the changes.

## Celery: Eager vs Async

### Why Eager Mode Doesn't Work for E2E

`CELERY_TASK_ALWAYS_EAGER=True` runs Celery tasks synchronously in the request thread. For audit creation tasks that make many production API calls (fetching visits, images, running AI), this:

1. Blocks the Django server thread for minutes
2. Prevents the server from handling SSE polling requests
3. Means the React UI can never get progress updates
4. Eventually crashes the page/process

### Async Mode with Worker Subprocess

Set `os.environ["CELERY_TASK_ALWAYS_EAGER"] = "False"` at conftest module level (before fixtures run). Start a Celery worker as a subprocess:

```python
worker = subprocess.Popen(
    [sys.executable, "-m", "celery", "-A", "config.celery_app",
     "worker", "--loglevel=info", "--pool=solo"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
```

The env var is inherited by both the Django server and Celery worker subprocesses.

**If the dev server was already running** before the test, it won't have the env var. Stop it and let the conftest start a fresh one.

### Queue Purge Before Worker Start

With `--pool=solo`, the Celery worker processes one task at a time. If a previous test run left tasks queued in Redis (e.g., the test timed out before the task finished), the worker picks up the stale task first and the new task stays "pending" indefinitely.

**Fix:** Purge the queue before starting the worker:

```python
subprocess.run(
    [sys.executable, "-m", "celery", "-A", "config.celery_app", "purge", "-f"],
    capture_output=True, timeout=10,
)
```

## SQL Cache Pitfalls

### Cache Must Be Populated

The audit pipeline uses a SQL cache (`RawVisitCache` in PostgreSQL) to filter visits. This cache is loaded **lazily** from the production API on first access.

**Bug discovered:** When `expected_visit_count` is 0 (which happens when the Celery task creates a mock request without opportunity metadata), `has_valid_raw_cache(0)` is vacuously true even on an empty table. The API download is skipped, and the audit finds 0 visits.

**Workaround:** Pre-populate the cache before running the audit, or use the `test_sql_visit_level` management command:

```bash
python manage.py test_sql_visit_level --opportunity-id 874
```

### Type Mismatch in Visit ID Filtering (Bug Found & Fixed)

`RawVisitCache.visit_id` is a `CharField` → `get_filtered_visit_ids()` returns **strings** like `"1557457"`. But `parse_csv_bytes()` uses `get_int("id")` → parsed visits have **int** IDs like `1557457`. The filter in `fetch_raw_visits` compared `int` to `set[str]`, which always returned `False`, silently discarding all visits.

**Fix** (in `backend.py`): Normalize both sides to strings:

```python
str_filter = {str(vid) for vid in filter_visit_ids}
visit_dicts = [v for v in visit_dicts if str(v.get("id")) in str_filter]
```

### Cache TTL

The SQL cache expires after 1 hour by default (`PIPELINE_CACHE_TTL_HOURS`). If tests run after the cache expires, visits will need to be re-fetched from the production API.

## React UI Behavior

### Audit Creation Flow

1. Click "Create Weekly Audit" → POST to `/audit/api/audit/create-async/`
2. API returns immediately with `{ task_id, job_id }`
3. React opens SSE stream to `/audit/api/audit/task/<task_id>/stream/`
4. Falls back to polling `/audit/api/audit/task/<task_id>/status/` if SSE fails
5. On completion: shows "Audit Sessions Created" header and session rows

### Race Condition with Fast Completion

If the Celery task completes very quickly (e.g., 0 visits → 2 seconds), the SSE stream may report SUCCESS before the React UI has transitioned to the progress view. The UI might briefly flash the progress state and return to showing results (or the form if 0 sessions).

## Debugging Tips

### Celery Worker Logs

Redirect worker output to a file for debugging:

```python
log_file = open("e2e_celery_worker.log", "w")
worker = subprocess.Popen([...], stdout=log_file, stderr=subprocess.STDOUT)
```

### Screenshots at Key Points

```python
page.screenshot(path="e2e_debug.png")
```

Take screenshots before assertions that might fail. The screenshot shows the actual UI state.

### Console Log Capture

```python
page.on("console", lambda msg: print(f"[CONSOLE] {msg.type}: {msg.text}"))
```

### Check SQL Cache State

```python
# In Django shell
from commcare_connect.labs.analysis.backends.sql.models import RawVisitCache
from django.utils import timezone
RawVisitCache.objects.filter(opportunity_id=874, expires_at__gt=timezone.now()).count()
```

## Test Cleanup

The test creates workflow records in the production labs API. The cleanup step deletes the workflow run:

```python
page.request.post(
    f"{live_server_url}/labs/workflow/api/run/{run_id}/delete/?opportunity_id={opportunity_id}",
    headers={"X-CSRFToken": csrf_token},
)
```

If the test fails before cleanup, orphaned workflows/runs accumulate. Consider adding a pytest finalizer or fixture-based cleanup.

## Common Failure Modes

| Symptom                         | Cause                                           | Fix                                                       |
| ------------------------------- | ----------------------------------------------- | --------------------------------------------------------- |
| 401 at `/labs/test-auth/`       | CLI token expired                               | `python manage.py get_cli_token`                          |
| All POSTs hang forever          | subprocess.PIPE buffer deadlock                 | Use `subprocess.DEVNULL`                                  |
| `networkidle` timeout           | SSE/polling keeps network active                | Use `domcontentloaded`                                    |
| Wrong button clicked            | Modal vs card button text overlap               | Scope to `.fixed.inset-0.z-50`                            |
| `SynchronousOnlyOperation`      | pytest-django + Playwright async                | Set `DJANGO_ALLOW_ASYNC_UNSAFE` + no-op `django_db_setup` |
| 0 visits/images found           | Type mismatch: int vs str visit IDs in filter   | Fixed in `backend.py` — normalize to strings              |
| Task stays "pending" forever    | Stale task in Redis queue blocks solo worker    | Queue purge in conftest before worker start               |
| Audit never completes           | `CELERY_TASK_ALWAYS_EAGER=True` blocking server | Use async mode with worker subprocess                     |
| Celery worker exits immediately | Missing Redis / wrong broker URL                | Check `docker ps` for redis container                     |
