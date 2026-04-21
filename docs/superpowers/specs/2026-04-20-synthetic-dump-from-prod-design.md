# Synthetic Sample Data — Dump-from-Prod + Labs-Context Access — Design

**Date:** 2026-04-20
**Author:** jjackson + Claude
**Status:** Design approved; implementation plan pending
**Builds on:** `docs/superpowers/specs/2026-04-20-synthetic-sample-data-design.md`

## Problem

The first pass of the synthetic-opportunity feature requires the user to manually `curl` five export endpoints, save them as JSON, upload to Google Drive, and paste the folder ID into `/labs/synthetic/new/`. That's five steps, a dev OAuth token, and familiarity with the export API. Most labs users won't do it.

Two things also need fixing now that we have real users:
1. The `/labs/synthetic/` pages are **global** — any labs-authenticated user sees and can edit every registered synthetic opp, regardless of whether they have Connect access to the underlying opportunity. The rest of labs scopes by `user_opportunities`; synthetic should too.
2. There's no way to pick which opp a new synthetic entry corresponds to other than typing an integer. The rest of labs already uses the **labs context selector** to identify "the current opportunity"; synthetic should use it.

## Scope

**In scope:**
- A "Dump fresh data from prod" button on `/labs/synthetic/new/` that server-side pulls the 5 export endpoints, creates a new Drive folder, and uploads the five JSON files — via a Server-Sent Events stream so long opps don't time out.
- Scope `SyntheticOpportunity` list/create/edit/delete/reload to the current user's `user_opportunities`. Strict filter for every user, including Dimagi staff.
- Drive an opp selection for create through the labs context (`request.labs_context.opportunity_id`), not a free-text integer field. The form's `opportunity_id` field becomes hidden and derived from context.
- Widen the service-account scope from `drive.readonly` to `drive.file` (SA can read/write only files it owns) and add two methods to `DriveClient`: `create_folder` and `upload_file`.
- New env var `LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID` — the parent folder where dumps land.

**Out of scope (explicitly):**
- Recovering from partial failures. Any exception ends the dump; the user re-runs (timestamped folder names prevent collision).
- Polishing end-user error messages. Raw exception text surfaces to the UI; we want transparency over cosmetics.
- Streaming Drive uploads. A full endpoint's rows are materialized in memory before uploading. Documented limitation.
- Read-side image support (same as the original spec).
- Any changes to how the synthetic reads work at runtime — this PR only adds a new write path and tightens UI access.

## Non-negotiable constraints

- **No partial-success recovery.** If any step fails, raise. No try/except, no cleanup.
- **No Dimagi escape hatch.** The `user_opportunities` filter applies to every user.
- **Dump must not time out.** Long opps can take minutes; use SSE so gunicorn doesn't kill the worker.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  /labs/synthetic/new/   (form view)             │
│  - hidden opp_id = request.labs_context.opp_id  │
│  - banner: "Registering synthetic version       │
│    of opp X (id: N)"                            │
│  - radio: [existing folder] [dump fresh]        │
└────────────────────┬────────────────────────────┘
                     │ click "Start dump"
                     ▼
┌─────────────────────────────────────────────────┐
│  EventSource → /labs/synthetic/dump/stream      │
│  DumpStreamView (BaseSSEStreamView)             │
│  - opp_id from labs_context                     │
│  - access_token from labs_oauth session         │
│  - SA DriveClient (drive.file scope)            │
│  - get_export_client for real reads             │
└────────┬───────────────────┬────────────────────┘
         │                   │
         ▼                   ▼
┌────────────────┐   ┌──────────────────────────┐
│ DriveClient    │   │ ExportAPIClient          │
│ .create_folder │   │ .fetch_all × 5 endpoints │
│ .upload_file   │   │                          │
└────────────────┘   └──────────────────────────┘
                     │
                     ▼
         SSE events: folder, fetching,
         uploading, uploaded, done, (error)
                     │
                     ▼
         Frontend JS populates hidden
         gdrive_folder_id field; user
         saves the form.
```

## Components

### 1. Access filtering (all views)

Helper in `labs/synthetic/registry.py` (colocated because it's registry-adjacent):

```python
def accessible_opp_ids(request) -> set[int]:
    """Opp IDs the current user has Connect access to."""
    org_data = get_org_data(request)
    return {o["id"] for o in org_data.get("opportunities", [])}
```

Every view in `labs/synthetic/views.py` filters its queryset:

```python
class SyntheticListView(LoginRequiredMixin, ListView):
    ...
    def get_queryset(self):
        return super().get_queryset().filter(
            opportunity_id__in=accessible_opp_ids(self.request)
        )
```

`UpdateView`, `DeleteView`, and `reload_fixtures_view` override `get_queryset` (for the CBVs) or add an explicit check (for the function view) so an out-of-access opp pk returns 404.

### 2. Create view redirects if no context opp

```python
class SyntheticCreateView(LoginRequiredMixin, CreateView):
    ...
    def dispatch(self, request, *args, **kwargs):
        opp_id = (getattr(request, "labs_context", {}) or {}).get("opportunity_id")
        if not opp_id or opp_id not in accessible_opp_ids(request):
            messages.warning(
                request,
                "Select an opportunity from the context selector before creating a synthetic entry.",
            )
            return HttpResponseRedirect(reverse("labs:synthetic:list"))
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {"opportunity_id": self.request.labs_context["opportunity_id"]}

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        # opportunity_id hidden from UI; reassert from context to prevent tampering
        form.instance.opportunity_id = self.request.labs_context["opportunity_id"]
        return super().form_valid(form)
```

The form keeps `opportunity_id` as a hidden field (still in `Meta.fields`) so `ModelForm` validation runs cleanly, but the create view authoritatively sets it from `labs_context` to block spoofing via form POST.

The edit form drops `opportunity_id` from the visible fields — it's identity and shouldn't change after creation.

### 3. Template updates to `form.html`

Adds a banner above the form with the current opp name/id, and the two-radio "source mode" section (existing folder vs dump fresh). JS toggles visible panes and drives the SSE EventSource on the dump path.

Frontend JS listens on `EventSource.onmessage` (not named events — `send_sse_event` emits anonymous `data: {...}` frames) and switches on `data.event` ("folder" | "fetching" | "uploading" | "uploaded" | "done") to update the progress log. Errors surface via `data.error` on any frame or via `EventSource.onerror` if the connection drops.

### 4. New view: `DumpStreamView`

`BaseSSEStreamView`'s abstract method is `stream_data(request)` (generator yielding pre-formatted SSE strings). If the generator raises, the response terminates without a structured error event — the browser's `EventSource` sees a disconnect. To make exceptions visible to the user, we wrap the generator body in a single outer try/except that emits a final error event before stopping. That's the minimum required to honor "expose the error directly to the end user if any step breaks" — not recovery, just reporting.

```python
# labs/synthetic/views.py
class DumpStreamView(BaseSSEStreamView):
    def stream_data(self, request):
        try:
            opp_id = request.labs_context["opportunity_id"]
            if opp_id not in accessible_opp_ids(request):
                raise PermissionDenied("Opportunity not in user's accessible set.")
            access_token = request.session["labs_oauth"]["access_token"]
            yield from dump_generator(opp_id, access_token)
        except Exception as e:
            yield send_sse_event("Dump failed", error=f"{type(e).__name__}: {e}")
```

Business logic lives in `labs/synthetic/dump.py` (new file) as a generator:

```python
def dump_generator(opp_id: int, access_token: str):
    parent_id = settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID
    if not parent_id:
        raise ImproperlyConfigured("LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID is not set.")

    drive = DriveClient()
    folder_name = f"opp-{opp_id}-{timezone.now():%Y%m%d-%H%M%S}"
    folder_id = drive.create_folder(folder_name, parent_id=parent_id)
    yield send_sse_event(f"Created folder {folder_name}", data={"folder_id": folder_id, "name": folder_name, "event": "folder"})

    endpoints = [
        ("", "opportunity.json"),
        ("user_visits", "user_visits.json"),
        ("user_data", "user_data.json"),
        ("completed_works", "completed_works.json"),
        ("completed_module", "completed_module.json"),
    ]
    with get_export_client(opportunity_id=opp_id, access_token=access_token) as client:
        for key, filename in endpoints:
            yield send_sse_event(f"Fetching {key or 'opportunity'}...", data={"event": "fetching", "endpoint": key or "opportunity"})
            path = f"/export/opportunity/{opp_id}/{key}/" if key else f"/export/opportunity/{opp_id}/"
            rows = client.fetch_all(path)
            count = len(rows) if isinstance(rows, list) else 1
            yield send_sse_event(f"Uploading {filename} ({count} rows)", data={"event": "uploading", "file": filename, "count": count})
            drive.upload_file(folder_id, filename, json.dumps(rows).encode())
            yield send_sse_event(f"✓ {filename}", data={"event": "uploaded", "file": filename})

    yield send_sse_event("Dump complete", data={"event": "done", "folder_id": folder_id})
```

Note on `send_sse_event` shape: the existing helper emits `{"message": ..., "complete": bool, "data": ...}`. We piggyback event kinds as `data["event"]` so the frontend can switch on them — preserves the existing envelope rather than inventing a new one.

Inside `dump_generator` itself, no try/except — if anything raises, the outer `DumpStreamView.stream_data` catches it and reports.

### 5. `DriveClient` additions (`gdrive.py`)

Scope change:

```python
SCOPES = ["https://www.googleapis.com/auth/drive"]
```

The SA has no Drive of its own — it only sees files/folders explicitly shared with it. The `drive` scope gives read/write on that shared set. `drive.file` was considered but restricts access to files the SA itself created (or opened via Drive Picker), which would break the existing read path for user-authored fixture folders. `drive` is appropriately narrow for an SA that only ever touches the labs-synthetic parent folder tree.

Operational requirement: the labs-synthetic parent folder must be shared with the SA as **Editor** (was Viewer). This lets the SA create subfolders/files inside it.

New methods:

```python
def create_folder(self, name: str, parent_id: str) -> str:
    resp = httpx.post(
        f"{DRIVE_API}/files",
        headers={**self._headers(), "Content-Type": "application/json"},
        json={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        timeout=self._timeout,
    )
    resp.raise_for_status()
    return resp.json()["id"]

def upload_file(self, folder_id: str, filename: str, content: bytes) -> str:
    metadata = {"name": filename, "parents": [folder_id]}
    boundary = "----labs-synthetic-" + secrets.token_hex(8)
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: application/json\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--".encode()

    resp = httpx.post(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
        headers={**self._headers(), "Content-Type": f"multipart/related; boundary={boundary}"},
        content=body,
        timeout=self._timeout,
    )
    resp.raise_for_status()
    return resp.json()["id"]
```

### 6. URL additions

```python
# labs/synthetic/urls.py
path("dump/stream/", views.DumpStreamView.as_view(), name="dump_stream"),
```

### 7. Env var additions

- `LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID` — required for dump to work. Absent → dump view raises `ImproperlyConfigured` on first call, surfaced to the user via SSE error event.

## Data flow

1. User visits `/labs/synthetic/new/` with opportunity 999 selected in labs_context.
2. View renders the form with a hidden `opportunity_id=999` and a banner.
3. User clicks "Dump fresh data from prod → Start dump".
4. JS opens `EventSource("/labs/synthetic/dump/stream")`.
5. Server creates Drive folder `opp-999-20260420-143000` under the parent folder (SA as owner).
6. Server pulls 5 endpoints sequentially via `ExportAPIClient` using the user's OAuth token, uploads each as JSON.
7. SSE stream emits `folder`, `fetching`, `uploading`, `uploaded` events per file, and a final `done` with the folder_id.
8. JS populates the hidden `gdrive_folder_id` field, displays "Done — save to register."
9. User fills in label/notes and saves. The form submits normally; the create view re-derives `opportunity_id` from labs_context.

## Error handling (minimal by design)

| Situation                               | Behavior                                   |
|-----------------------------------------|--------------------------------------------|
| No opp in labs_context on create page   | Redirect to list + flash message           |
| User tries to edit/delete opp they lack access to | 404 via scoped queryset             |
| Dump invoked with no labs_context opp   | `KeyError`, surfaces to user via SSE error |
| Dump invoked with no access to opp      | `PermissionDenied`, surfaces via SSE error |
| `LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID` unset | `ImproperlyConfigured`, surfaces    |
| Drive API error mid-dump                | `DriveAPIError`, raw message via SSE       |
| Export API error mid-dump               | `ExportAPIError`, raw message via SSE      |
| SA lacks write permission on parent     | `DriveAPIError` 403, raw message via SSE   |

Folder may be left behind on partial failure — acceptable. User re-runs the dump and gets a new timestamped folder.

## Testing

**New tests (`tests/test_dump_flow.py`):**

- `test_dump_stream_requires_context_opp` — no opp in labs_context → error event
- `test_dump_stream_requires_access` — opp not in `user_opportunities` → error event (PermissionDenied)
- `test_dump_stream_end_to_end` — fake DriveClient + mocked ExportAPIClient; stream yields `folder`, `fetching`, `uploading`, `uploaded` events for each of the 5 endpoints, final `done` event carries folder_id
- `test_dump_stream_surfaces_export_error` — ExportAPIError mid-stream → error event with message
- `test_dump_stream_surfaces_drive_error` — DriveAPIError on upload → error event with message
- `test_dump_stream_missing_parent_folder_env` → error event with ImproperlyConfigured message

**Existing test updates:**

- `test_views.py` — add fixtures that mock `get_org_data` to include the test opp_id in `opportunities` so `accessible_opp_ids` returns the right set. Current tests break without this; this is the scope tightening actively happening.
- `test_views.py::test_create_round_trip` — POST no longer sends `opportunity_id` directly; it's derived from labs_context. Test fixture adds `labs_context` to the request session.
- `test_views.py::test_create_requires_context_opp` — NEW: unauthed context → redirect to list with flash message.
- `test_views.py::test_create_requires_access_to_context_opp` — NEW: labs_context opp not in user_opportunities → redirect.

**`test_gdrive.py` additions:**

- `test_create_folder_posts_mimetype_folder` — verify POST body shape, return file ID
- `test_upload_file_multipart_body` — verify boundary, metadata/content parts, return file ID

## Rollout plan (for the implementation plan, not this spec)

1. Registry/access helper + list view scoping + view tests updated.
2. Create view redirect + hidden opp_id + labs_context wiring.
3. Edit/delete/reload scoping (queryset override).
4. `DriveClient.create_folder` + `upload_file` + scope change.
5. `dump.py` generator + `DumpStreamView` + URL.
6. `form.html` updates + JS for SSE.
7. Tests (gdrive + dump_flow + updated views).
8. Ops: set `LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID`, share parent folder with SA as Editor.
9. Docs: update `docs/SYNTHETIC_OPPS.md` with the new dump flow.

## Future work

- Streamed (chunked) Drive uploads for huge opps instead of in-memory JSON. Use `uploadType=resumable`.
- Re-dump button on existing rows (re-upload to the same folder, overwriting files).
- "Dump and auto-register" shortcut that skips the save step.
