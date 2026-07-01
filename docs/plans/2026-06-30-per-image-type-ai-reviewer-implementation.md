# Per-Image-Type AI Reviewer Selection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an auditor pair an AI reviewer (and its required settings) with each image type during audit creation, so each image type runs only its chosen reviewer.

**Architecture:** Agents declare their own settings via a declarative `config_fields` schema the wizard renders generically. A new merged wizard step attaches one reviewer per selected image type. The wizard sends an `image_audits` (+ `context_fields`) payload; the create task translates it into the existing internal `related_fields` rules plus a `question_id → reviewer` map, and the AI-review run loop resolves the agent per image's `question_id`. Results storage stays one verdict per image.

**Tech Stack:** Django, Celery, Alpine.js (no build step — template is server-rendered), pytest. Spec: `docs/superpowers/specs/2026-06-30-per-image-type-ai-reviewer-design.md`.

## Global Constraints

- Strictly **one AI review per image** — do not change `set_assessment` storage, the
  per-`blob_id` assessment shape, or the review/render UI. `reviewers` is list-shaped in
  the payload only; the backend runs at most one per type in v1.
- `comparison_field` is the author/config/payload-facing name. Internal runtime plumbing
  stays `form_data["reading"]` / `requires_reading` — do **not** rename those.
- **Backward compatibility:** the create path must keep accepting the legacy
  `ai_agent_id` + `criteria.relatedFields` shape. `image_audits` is a new, optional key.
- Run tests with GDAL/GEOS env on macOS:
  `GDAL_LIBRARY_PATH` / `GEOS_LIBRARY_PATH` (see memory `env_gdal_geos.md`). Prefix pytest
  with them if libgdal import fails.
- Commit after each task. Use `make commit` or prepend the venv:
  `PATH="$HOME/emdash-projects/connect-labs/.venv/bin:$PATH" git commit`.

---

### Task 1: Agents declare a `config_fields` schema; surface it to the frontend

**Files:**
- Modify: `connect_labs/labs/ai_review_agents/base.py:52-55`
- Modify: `connect_labs/labs/ai_review_agents/agents/scale_validation.py:54-68`
- Modify: `connect_labs/audit/views.py:1568-1580` (`AIAgentsListAPIView`)
- Test: `connect_labs/audit/tests/test_ai_agents_list_view.py` (create)

**Interfaces:**
- Produces: `BaseAIReviewAgent.config_fields: list[dict]` — each item
  `{"key": str, "label": str, "type": str, "required": bool, "help": str}`.
  `ScaleValidationAgent.config_fields` has one item with `key="comparison_field"`,
  `type="form_field"`. `MUACOverzoomAgent.config_fields == []` (inherited default).
- Produces: `AIAgentsListAPIView` GET response — each agent dict gains
  `"config_fields": list[dict]`.

- [ ] **Step 1: Write the failing test**

Create `connect_labs/audit/tests/test_ai_agents_list_view.py`:

```python
"""Tests for AIAgentsListAPIView config_fields surfacing."""
import time

import pytest
from django.test import Client


@pytest.fixture
def labs_client(db):
    from connect_labs.users.models import User

    user, _ = User.objects.update_or_create(
        username="testuser", defaults={"email": "testuser@example.com"}
    )
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "tok", "expires_at": time.time() + 3600}
    session.save()
    return client


def test_agents_list_includes_config_fields(labs_client):
    resp = labs_client.get("/audit/api/ai-agents/")
    assert resp.status_code == 200
    agents = {a["agent_id"]: a for a in resp.json()["agents"]}

    # Every agent exposes a config_fields list
    for agent in agents.values():
        assert isinstance(agent["config_fields"], list)

    # Scale agent declares the comparison_field form-field setting
    scale = agents["scale_validation"]
    keys = [f["key"] for f in scale["config_fields"]]
    assert "comparison_field" in keys
    cf = next(f for f in scale["config_fields"] if f["key"] == "comparison_field")
    assert cf["type"] == "form_field"
    assert cf["required"] is True
    assert cf["label"] == "Manual Scale Value"

    # MUAC agent declares no settings
    assert agents["muac_overzoom"]["config_fields"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest connect_labs/audit/tests/test_ai_agents_list_view.py -v`
Expected: FAIL — `KeyError: 'config_fields'` (endpoint does not return that key yet).

- [ ] **Step 3: Add `config_fields` to the base class**

In `connect_labs/labs/ai_review_agents/base.py`, after line 55 (`result_actions: dict = {}`), add:

```python
    result_actions: dict = {}
    # Declarative settings the creation wizard renders when this agent is chosen
    # for an image type. Each item: {key, label, type, required, help}.
    # type "form_field" renders a picker of the opportunity's form-field paths.
    config_fields: list[dict] = []
```

- [ ] **Step 4: Declare `config_fields` on the scale agent**

In `connect_labs/labs/ai_review_agents/agents/scale_validation.py`, inside the
`ScaleValidationAgent` class body, immediately after the `result_actions = {...}` block
(after line 68), add:

```python
    config_fields = [
        {
            "key": "comparison_field",
            "label": "Manual Scale Value",
            "type": "form_field",
            "required": True,
            "help": "Form field whose value is compared against the scale photo",
        }
    ]
```

(`MUACOverzoomAgent` needs no change — it inherits the empty default.)

- [ ] **Step 5: Surface `config_fields` in the agents-list endpoint**

In `connect_labs/audit/views.py`, in `AIAgentsListAPIView.get`, extend the appended
dict (after line 1578 `"auto_apply_result": ...`) with:

```python
                    "auto_apply_result": getattr(agent_class, "auto_apply_result", False),
                    # Declarative settings the wizard renders for this agent (e.g. the
                    # scale agent's "Manual Scale Value" form-field picker).
                    "config_fields": getattr(agent_class, "config_fields", []),
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest connect_labs/audit/tests/test_ai_agents_list_view.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add connect_labs/labs/ai_review_agents/base.py \
        connect_labs/labs/ai_review_agents/agents/scale_validation.py \
        connect_labs/audit/views.py \
        connect_labs/audit/tests/test_ai_agents_list_view.py
git commit -m "feat(audit): agents declare config_fields schema, surfaced in agents-list API"
```

---

### Task 2: Form-field discovery endpoint (`field-questions`)

**Files:**
- Modify: `connect_labs/audit/analysis_config.py` (add `extract_field_paths`)
- Modify: `connect_labs/audit/views.py` (add `OpportunityFieldQuestionsAPIView`, near `OpportunityImageTypesAPIView` ~line 1804)
- Modify: `connect_labs/audit/urls.py:16-20` (add route)
- Test: `connect_labs/audit/tests/test_analysis_config.py` (create)

**Interfaces:**
- Produces: `extract_field_paths(form_json: dict) -> list[str]` — sorted, de-duped leaf
  scalar question paths (e.g. `["form/child_weight", "group/photo_a"]`). Skips repeat
  groups (lists) and `SKIP_KEYS`.
- Produces: GET `/audit/api/opportunity/<opp_id>/field-questions/` →
  `[{"id": path, "label": leaf, "path": path}, ...]`.

- [ ] **Step 1: Write the failing test for `extract_field_paths`**

Create `connect_labs/audit/tests/test_analysis_config.py`:

```python
"""Tests for analysis_config form-field extraction helpers."""
from connect_labs.audit.analysis_config import extract_field_paths


def test_extract_field_paths_flattens_leaf_scalars():
    form_json = {
        "form": {
            "child_weight": "12.5",
            "group": {"photo_a": "img1.jpg", "muac": "11.0"},
            "meta": {"timeEnd": "2026-01-01"},  # SKIP_KEYS -> excluded
            "@name": "Form",  # SKIP_KEYS -> excluded
            "repeat": [{"x": "1"}, {"x": "2"}],  # list -> skipped in v1
        }
    }
    paths = extract_field_paths(form_json)
    assert paths == ["child_weight", "group/muac", "group/photo_a"]


def test_extract_field_paths_handles_top_level_without_form_key():
    assert extract_field_paths({"a": "1", "b": {"c": "2"}}) == ["a", "b/c"]


def test_extract_field_paths_empty():
    assert extract_field_paths({}) == []
    assert extract_field_paths(None) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest connect_labs/audit/tests/test_analysis_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'extract_field_paths'`.

- [ ] **Step 3: Implement `extract_field_paths`**

In `connect_labs/audit/analysis_config.py`, after `_build_filename_map` (after line 45),
add:

```python
def _collect_leaf_paths(data: dict, path: str = "") -> list[str]:
    """Collect paths to all leaf scalar values in a form_json tree.

    Skips SKIP_KEYS and repeat groups (lists) — v1 only targets non-repeating
    scalar questions for the comparison-field picker.
    """
    result: list[str] = []
    if not isinstance(data, dict):
        return result

    for key, value in data.items():
        if key in SKIP_KEYS:
            continue
        current_path = f"{path}/{key}" if path else key
        if isinstance(value, dict):
            result.extend(_collect_leaf_paths(value, current_path))
        elif isinstance(value, list):
            continue  # repeat group — skipped in v1
        else:
            result.append(current_path)  # scalar leaf (str / number / None)

    return result


def extract_field_paths(form_json: dict | None) -> list[str]:
    """Return sorted, de-duped leaf scalar question paths from a visit's form_json.

    Mirrors how extract_images_with_question_ids reads form_json: it unwraps the
    top-level "form" key when present.
    """
    if not isinstance(form_json, dict):
        return []
    form_data = form_json.get("form", form_json)
    return sorted(set(_collect_leaf_paths(form_data)))
```

- [ ] **Step 4: Run the helper test to verify it passes**

Run: `pytest connect_labs/audit/tests/test_analysis_config.py -v`
Expected: PASS.

- [ ] **Step 5: Add the `OpportunityFieldQuestionsAPIView`**

In `connect_labs/audit/views.py`, immediately after the `OpportunityImageTypesAPIView`
class (it ends at line 1882 with `return JsonResponse(result, safe=False)`), add a sibling
view. It reuses the same streaming sampler but accumulates field paths instead of image
question ids:

```python
class OpportunityFieldQuestionsAPIView(LoginRequiredMixin, View):
    """Discover non-image form-field paths by sampling visits.

    Sibling of OpportunityImageTypesAPIView — same streaming sampler, but flattens
    form_json leaf scalar paths so the creation wizard can offer a real dropdown for
    agent comparison-field settings (e.g. the scale agent's "Manual Scale Value").

    GET /audit/api/opportunity/<opp_id>/field-questions/
    Response: [{id, label, path}, ...]
    """

    MAX_ROWS = 200
    STABLE_THRESHOLD = 50

    def get(self, request, opp_id: int):
        labs_oauth = request.session.get("labs_oauth", {})
        access_token = labs_oauth.get("access_token", "")
        if not access_token:
            return JsonResponse({"error": "No OAuth token"}, status=401)

        from connect_labs.audit.analysis_config import extract_field_paths
        from connect_labs.labs.integrations.connect.export_client import ExportAPIError
        from connect_labs.labs.integrations.connect.factory import get_export_client

        endpoint = f"/export/opportunity/{opp_id}/user_visits/"
        params = {"images": "true"}

        seen_paths: set[str] = set()
        rows_processed = 0
        rows_seen = 0
        stop_early = False

        try:
            with get_export_client(opportunity_id=opp_id, access_token=access_token, timeout=60.0) as client:
                for page in client.paginate(endpoint, params=params):
                    for record in page:
                        rows_processed += 1
                        if rows_processed > self.MAX_ROWS:
                            stop_early = True
                            break

                        form_json = record.get("form_json") or {}
                        if not isinstance(form_json, dict):
                            continue

                        new_found = False
                        for p in extract_field_paths(form_json):
                            if p not in seen_paths:
                                seen_paths.add(p)
                                new_found = True

                        rows_seen += 1
                        if not new_found and rows_seen >= self.STABLE_THRESHOLD:
                            stop_early = True
                            break

                    if stop_early:
                        break

        except ExportAPIError as e:
            logger.error(f"[FieldTypes] Connect export API failure for opp {opp_id}: {e}")
            return JsonResponse({"error": "Connect API error"}, status=502)
        except Exception:
            logger.exception("[FieldTypes] Failed to discover field types for opp %s", opp_id)
            return JsonResponse({"error": "An internal error occurred"}, status=500)

        result = [{"id": p, "label": p.rsplit("/", 1)[-1], "path": p} for p in sorted(seen_paths)]
        return JsonResponse(result, safe=False)
```

- [ ] **Step 6: Add the URL route**

In `connect_labs/audit/urls.py`, after the `opportunity_image_questions` path
(line 20), add:

```python
    path(
        "api/opportunity/<int:opp_id>/field-questions/",
        views.OpportunityFieldQuestionsAPIView.as_view(),
        name="opportunity_field_questions",
    ),
```

- [ ] **Step 7: Write a view smoke test**

Append to `connect_labs/audit/tests/test_analysis_config.py`:

```python
import time

import pytest
from django.test import Client


@pytest.fixture
def labs_client(db):
    from connect_labs.users.models import User

    user, _ = User.objects.update_or_create(
        username="fielduser", defaults={"email": "fielduser@example.com"}
    )
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "tok", "expires_at": time.time() + 3600}
    session.save()
    return client


def test_field_questions_requires_oauth(db):
    from connect_labs.users.models import User

    user, _ = User.objects.update_or_create(
        username="noauth", defaults={"email": "noauth@example.com"}
    )
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    resp = client.get("/audit/api/opportunity/42/field-questions/")
    assert resp.status_code == 401
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest connect_labs/audit/tests/test_analysis_config.py -v`
Expected: PASS (helper tests + the 401 smoke test).

- [ ] **Step 9: Commit**

```bash
git add connect_labs/audit/analysis_config.py \
        connect_labs/audit/views.py \
        connect_labs/audit/urls.py \
        connect_labs/audit/tests/test_analysis_config.py
git commit -m "feat(audit): add field-questions endpoint for comparison-field picker"
```

---

### Task 3: `build_review_config` translation helper

**Files:**
- Create: `connect_labs/audit/ai_review_config.py`
- Test: `connect_labs/audit/tests/test_ai_review_config.py` (create)

**Interfaces:**
- Produces: `build_review_config(image_audits: list[dict], context_fields: list[dict] | None = None) -> tuple[list[dict], dict[str, dict]]`.
  Returns `(related_fields, ai_reviewers)` where:
  - `related_fields` items: `{image_path, field_path, label, filter_by_image, filter_by_field}`
    — the internal shape consumed by `AuditDataAccess._add_related_fields_to_images` and
    `_filter_visits_by_related_fields`.
  - `ai_reviewers`: `{question_id: {"agent_id": str, "auto_apply_actions": list|None}}`.

- [ ] **Step 1: Write the failing test**

Create `connect_labs/audit/tests/test_ai_review_config.py`:

```python
"""Tests for build_review_config (image_audits -> related_fields + ai_reviewers)."""
from connect_labs.audit.ai_review_config import build_review_config


def test_scale_reviewer_produces_filter_rule_reading_rule_and_map():
    image_audits = [
        {
            "image_path": "form/scale_photo",
            "reviewers": [
                {
                    "agent_id": "scale_validation",
                    "config": {"comparison_field": "form/child_weight"},
                    "auto_apply_actions": ["pass_matched", "fail_unmatched"],
                }
            ],
        }
    ]
    related_fields, ai_reviewers = build_review_config(image_audits)

    # One filter rule (scope to visits with the image) + one reading rule (comparison field)
    assert {
        "image_path": "form/scale_photo",
        "field_path": "",
        "label": "",
        "filter_by_image": True,
        "filter_by_field": False,
    } in related_fields
    assert {
        "image_path": "form/scale_photo",
        "field_path": "form/child_weight",
        "label": "",
        "filter_by_image": False,
        "filter_by_field": False,
    } in related_fields

    assert ai_reviewers == {
        "form/scale_photo": {
            "agent_id": "scale_validation",
            "auto_apply_actions": ["pass_matched", "fail_unmatched"],
        }
    }


def test_image_only_agent_has_no_reading_rule():
    image_audits = [
        {
            "image_path": "form/muac_photo",
            "reviewers": [{"agent_id": "muac_overzoom", "config": {}, "auto_apply_actions": ["fail_overzoomed"]}],
        }
    ]
    related_fields, ai_reviewers = build_review_config(image_audits)
    # Only the filter rule — no reading rule because there's no comparison_field
    assert related_fields == [
        {
            "image_path": "form/muac_photo",
            "field_path": "",
            "label": "",
            "filter_by_image": True,
            "filter_by_field": False,
        }
    ]
    assert ai_reviewers["form/muac_photo"]["agent_id"] == "muac_overzoom"


def test_type_with_no_reviewer_filters_but_no_map_entry():
    related_fields, ai_reviewers = build_review_config([{"image_path": "form/consent", "reviewers": []}])
    assert related_fields == [
        {
            "image_path": "form/consent",
            "field_path": "",
            "label": "",
            "filter_by_image": True,
            "filter_by_field": False,
        }
    ]
    assert ai_reviewers == {}


def test_context_fields_become_display_rules():
    related_fields, ai_reviewers = build_review_config(
        [],
        context_fields=[{"image_path": "form/scale_photo", "field_path": "form/child_id", "label": "Child ID"}],
    )
    assert related_fields == [
        {
            "image_path": "form/scale_photo",
            "field_path": "form/child_id",
            "label": "Child ID",
            "filter_by_image": False,
            "filter_by_field": False,
        }
    ]
    assert ai_reviewers == {}


def test_blank_image_path_is_ignored():
    related_fields, ai_reviewers = build_review_config([{"image_path": "", "reviewers": []}])
    assert related_fields == []
    assert ai_reviewers == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest connect_labs/audit/tests/test_ai_review_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'connect_labs.audit.ai_review_config'`.

- [ ] **Step 3: Implement the helper**

Create `connect_labs/audit/ai_review_config.py`:

```python
"""Translate the wizard's image_audits payload into the audit pipeline's
internal related_fields rules plus a question_id -> reviewer map.

image_audits (from the creation wizard):
    [{"image_path": "form/scale_photo",
      "reviewers": [{"agent_id": "scale_validation",
                     "config": {"comparison_field": "form/child_weight"},
                     "auto_apply_actions": ["pass_matched", "fail_unmatched"]}]}]

context_fields (slim agent-less display):
    [{"image_path": "form/scale_photo", "field_path": "form/child_id", "label": "Child ID"}]

related_fields rules consumed by AuditDataAccess:
    {image_path, field_path, label, filter_by_image, filter_by_field}

ai_reviewers map consumed by tasks._run_ai_review_on_sessions:
    {question_id: {"agent_id": str, "auto_apply_actions": list | None}}
"""


def _filter_rule(image_path: str) -> dict:
    return {
        "image_path": image_path,
        "field_path": "",
        "label": "",
        "filter_by_image": True,
        "filter_by_field": False,
    }


def _value_rule(image_path: str, field_path: str, label: str = "") -> dict:
    return {
        "image_path": image_path,
        "field_path": field_path,
        "label": label,
        "filter_by_image": False,
        "filter_by_field": False,
    }


def build_review_config(
    image_audits: list[dict] | None,
    context_fields: list[dict] | None = None,
) -> tuple[list[dict], dict[str, dict]]:
    """Return (related_fields, ai_reviewers) for the given wizard payload."""
    related_fields: list[dict] = []
    ai_reviewers: dict[str, dict] = {}

    for entry in image_audits or []:
        image_path = (entry or {}).get("image_path")
        if not image_path:
            continue

        # Selecting an image type scopes the audit to visits that have it.
        related_fields.append(_filter_rule(image_path))

        reviewers = entry.get("reviewers") or []
        reviewer = reviewers[0] if reviewers else None  # v1: one reviewer per type
        if not reviewer or not reviewer.get("agent_id"):
            continue

        ai_reviewers[image_path] = {
            "agent_id": reviewer["agent_id"],
            "auto_apply_actions": reviewer.get("auto_apply_actions"),
        }

        # A form_field config value (e.g. the scale agent's comparison_field) becomes
        # the reading rule that supplies form_data["reading"] to the agent.
        config = reviewer.get("config") or {}
        comparison_field = config.get("comparison_field")
        if comparison_field:
            related_fields.append(_value_rule(image_path, comparison_field))

    for cf in context_fields or []:
        image_path = (cf or {}).get("image_path")
        field_path = (cf or {}).get("field_path")
        if image_path and field_path:
            related_fields.append(_value_rule(image_path, field_path, cf.get("label", "")))

    return related_fields, ai_reviewers
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest connect_labs/audit/tests/test_ai_review_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add connect_labs/audit/ai_review_config.py \
        connect_labs/audit/tests/test_ai_review_config.py
git commit -m "feat(audit): add build_review_config translation helper"
```

---

### Task 4: Per-`question_id` agent resolver in the AI-review run loop

**Files:**
- Modify: `connect_labs/audit/tasks.py:108-363` (`_run_ai_review_on_sessions`)
- Test: `connect_labs/audit/tests/test_ai_review_per_type.py` (create)

**Interfaces:**
- Consumes: `build_review_config` output shape (`ai_reviewers`) from Task 3;
  `connect_labs.labs.ai_review_agents.registry.get_agent`.
- Produces: new signature
  `_run_ai_review_on_sessions(data_access, session_ids, access_token, opp_id, ai_agent_id=None, auto_apply_actions=None, ai_reviewers=None, progress_callback=None)`.
  When `ai_reviewers` is given, the agent is resolved per image's `question_id`; images
  whose `question_id` has no reviewer are skipped silently. Legacy single-agent behavior
  is preserved when `ai_reviewers is None` and `ai_agent_id` is set.

- [ ] **Step 1: Write the failing test**

Create `connect_labs/audit/tests/test_ai_review_per_type.py`. It uses fakes — no real
HTTP — to assert the right agent runs per image type:

```python
"""Tests for per-image-type agent resolution in _run_ai_review_on_sessions."""
import pytest

from connect_labs.audit import tasks
from connect_labs.labs.ai_review_agents.types import ReviewResult


class _FakeSession:
    def __init__(self, data):
        self.data = data
        self.assessments = []  # (visit_id, blob_id, question_id, result, ai_result, ai_notes)

    def set_assessment(self, visit_id, blob_id, question_id, result, notes, ai_result=None, ai_notes=None):
        self.assessments.append((visit_id, blob_id, question_id, result, ai_result, ai_notes))


class _FakeDataAccess:
    def __init__(self, session):
        self._session = session

    def get_audit_session(self, session_id):
        return self._session

    def download_image_from_connect(self, blob_id, opp_id):
        return b"\xff\xd8fakejpeg"

    def save_audit_session(self, session):
        pass


class _MatchAgent:
    """Stand-in agent that records which blob_ids it was asked to review."""

    name = "Match Agent"
    requires_reading = False
    result_actions = {"ok": {"ai_result": "match", "human_result": "pass", "button_label": "OK"}}
    seen = []

    def review(self, ctx):
        type(self).seen.append(ctx.metadata["blob_id"])
        return ReviewResult.success(match=True)


class _OtherAgent(_MatchAgent):
    name = "Other Agent"
    seen = []


@pytest.fixture
def patched_registry(monkeypatch):
    agents = {"agent_a": _MatchAgent(), "agent_b": _OtherAgent()}
    _MatchAgent.seen = []
    _OtherAgent.seen = []
    monkeypatch.setattr(tasks, "get_agent", lambda aid: agents[aid], raising=False)
    # tasks imports get_agent locally inside the function; patch the source too
    from connect_labs.labs.ai_review_agents import registry

    monkeypatch.setattr(registry, "get_agent", lambda aid: agents[aid])
    return agents


def _session_with_two_image_types():
    return _FakeSession(
        {
            "visit_images": {
                "1": [
                    {"blob_id": "blobA", "question_id": "form/photo_a", "related_fields": []},
                    {"blob_id": "blobB", "question_id": "form/photo_b", "related_fields": []},
                ]
            }
        }
    )


def test_each_image_type_runs_only_its_reviewer(patched_registry):
    session = _session_with_two_image_types()
    data_access = _FakeDataAccess(session)
    ai_reviewers = {
        "form/photo_a": {"agent_id": "agent_a", "auto_apply_actions": ["ok"]},
        "form/photo_b": {"agent_id": "agent_b", "auto_apply_actions": ["ok"]},
    }

    tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=42,
        ai_reviewers=ai_reviewers,
    )

    assert _MatchAgent.seen == ["blobA"]
    assert _OtherAgent.seen == ["blobB"]


def test_image_type_without_reviewer_is_skipped(patched_registry):
    session = _session_with_two_image_types()
    data_access = _FakeDataAccess(session)
    ai_reviewers = {"form/photo_a": {"agent_id": "agent_a", "auto_apply_actions": ["ok"]}}

    tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=42,
        ai_reviewers=ai_reviewers,
    )

    assert _MatchAgent.seen == ["blobA"]
    assert _OtherAgent.seen == []  # photo_b had no reviewer
    # Only the reviewed image produced an assessment
    assert [a[1] for a in session.assessments] == ["blobA"]


def test_legacy_single_agent_still_runs_on_all(patched_registry):
    session = _session_with_two_image_types()
    data_access = _FakeDataAccess(session)

    tasks._run_ai_review_on_sessions(
        data_access=data_access,
        session_ids=[10],
        access_token="tok",
        opp_id=42,
        ai_agent_id="agent_a",
        auto_apply_actions=["ok"],
    )

    assert sorted(_MatchAgent.seen) == ["blobA", "blobB"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest connect_labs/audit/tests/test_ai_review_per_type.py -v`
Expected: FAIL — `_run_ai_review_on_sessions()` does not accept `ai_reviewers` (TypeError),
and `get_agent` is referenced as a local import, not a module attribute.

- [ ] **Step 3: Change the function signature and add the resolver**

In `connect_labs/audit/tasks.py`, replace the signature (lines 108-116) with:

```python
def _run_ai_review_on_sessions(
    data_access,
    session_ids: list[int],
    access_token: str,
    opp_id: int,
    ai_agent_id: str | None = None,
    auto_apply_actions: list[str] | None = None,
    ai_reviewers: dict | None = None,
    progress_callback=None,
) -> dict:
```

Then replace the body from line 137 (`from ... import get_agent`) through line 150 (the
`logger.info(f"[AIReview] Auto-apply map ...")`) with a unified resolver:

```python
    from connect_labs.labs.ai_review_agents.registry import get_agent

    # Resolve a reviewer for a given image question_id. Unifies two modes:
    #   * per-type (ai_reviewers given): look up the agent by question_id
    #   * legacy (ai_agent_id given): the same agent applies to every question_id
    # Returns (agent, requires_reading, ai_to_human_map) or None when no reviewer applies.
    _reviewer_cache: dict = {}

    def resolve(question_id):
        if ai_reviewers is not None:
            spec = ai_reviewers.get(question_id)
            if not spec or not spec.get("agent_id"):
                return None
            cache_key = ("qid", question_id)
            agent_id_ = spec["agent_id"]
            actions = spec.get("auto_apply_actions")
        else:
            if not ai_agent_id:
                return None
            cache_key = ("global",)
            agent_id_ = ai_agent_id
            actions = auto_apply_actions
        if cache_key not in _reviewer_cache:
            ag = get_agent(agent_id_)
            _reviewer_cache[cache_key] = (
                ag,
                getattr(ag, "requires_reading", True),
                _build_ai_to_human_result(ag, actions),
            )
        return _reviewer_cache[cache_key]

    if ai_reviewers is not None:
        logger.info(f"[AIReview] Per-image-type review on {len(session_ids)} sessions: {ai_reviewers}")
    else:
        logger.info(f"[AIReview] Running agent '{ai_agent_id}' on {len(session_ids)} sessions")
```

- [ ] **Step 4: Use the resolver in the first-pass count**

Replace the count loop (lines 152-171, from `total_images_to_review = 0` through the
`except Exception: pass`) with:

```python
    # First pass: count only images that have a reviewer AND meet its reading requirement
    total_images_to_review = 0
    session_image_counts = {}
    for session_id in session_ids:
        try:
            session = data_access.get_audit_session(session_id)
            if session:
                visit_images = session.data.get("visit_images", {})
                reviewable_count = 0
                for images in visit_images.values():
                    for image_data in images:
                        if not image_data.get("blob_id"):
                            continue
                        resolved = resolve(image_data.get("question_id", ""))
                        if not resolved:
                            continue
                        _agent, requires_reading, _map = resolved
                        related_fields = image_data.get("related_fields", [])
                        has_reading = any(rf.get("value") for rf in related_fields)
                        if has_reading or not requires_reading:
                            reviewable_count += 1
                session_image_counts[session_id] = reviewable_count
                total_images_to_review += reviewable_count
        except Exception:
            pass
```

- [ ] **Step 5: Use the resolver in work-item collection**

Replace the work-item collection block (lines 205-227, from the
`# Phase 1: collect reviewable work items` comment through the `work_items.append(...)`)
with the version that resolves per image and carries the image's own `question_id`:

```python
            # Phase 1: collect reviewable work items, skip the rest.
            # Each item: (visit_id_str, blob_id, reading, question_id, image_qid)
            #   image_qid -> the image's own question path, used to resolve its reviewer
            #   question_id -> stored on the assessment (may be the reading field's path)
            work_items = []
            for visit_id_str, images in visit_images.items():
                logger.debug(f"[AIReview] Visit {visit_id_str}: {len(images)} images")
                for image_data in images:
                    blob_id = image_data.get("blob_id")
                    if not blob_id:
                        continue
                    image_qid = image_data.get("question_id", "")
                    resolved = resolve(image_qid)
                    if not resolved:
                        continue  # no reviewer configured for this image type
                    _agent, requires_reading, _map = resolved
                    related_fields = image_data.get("related_fields", [])
                    reading = None
                    question_id = image_qid
                    for rf in related_fields:
                        if rf.get("value"):
                            reading = str(rf.get("value"))
                            question_id = rf.get("path") or question_id
                            break
                    if not reading and requires_reading:
                        logger.debug(f"[AIReview] Skipping blob={blob_id}: no reading and agent requires one")
                        total_skipped += 1
                        images_processed += 1
                        continue
                    work_items.append((visit_id_str, blob_id, reading, question_id, image_qid))
```

- [ ] **Step 6: Use the resolved agent inside the worker**

Replace the worker function (lines 233-276, `def _fetch_and_review(item):` through its
final `return`) with one that unpacks `image_qid`, resolves its agent, and returns it:

```python
            def _fetch_and_review(item):
                v_id, b_id, rdg, q_id, img_qid = item
                agent, _rr, _map = resolve(img_qid)
                try:
                    img_bytes = data_access.download_image_from_connect(b_id, opp_id)
                    if not img_bytes:
                        return (v_id, b_id, q_id, rdg, img_qid, None, None, True)  # skipped
                except Exception as exc:
                    logger.warning(f"[AIReview] Failed to fetch image {b_id}: {exc}")
                    return (v_id, b_id, q_id, rdg, img_qid, None, None, True)  # skipped

                from connect_labs.labs.ai_review_agents.types import ReviewContext

                ctx = ReviewContext(
                    images={"scale": img_bytes},
                    form_data={"reading": rdg} if rdg else {},
                    metadata={
                        "visit_id": v_id,
                        "blob_id": b_id,
                        "opportunity_id": opp_id,
                        "session_id": session_id,
                    },
                )
                ai_n = None
                try:
                    rv = agent.review(ctx)
                    if rv.passed:
                        ai_r = "match"
                        ai_n = rv.details.get("pass_label")
                    elif rv.failed:
                        ai_r = "no_match"
                        ai_n = rv.details.get("badge_label")
                    else:
                        ai_r = "error"
                        ai_n = "; ".join(rv.errors) if rv.errors else None
                except Exception as exc:
                    logger.exception(f"[AIReview] Agent raised exception for blob={b_id}")
                    ai_r = "error"
                    ai_n = str(exc)

                return (v_id, b_id, q_id, rdg, img_qid, ai_r, ai_n, False)  # not skipped
```

- [ ] **Step 7: Unpack `image_qid` and use the per-type auto-apply map at persist time**

In the results loop, replace the unpack line (line 282) and the persistence block
(lines 309-318). First the unpack:

```python
                    try:
                        visit_id_str, blob_id, question_id, reading, img_qid, ai_result, ai_notes, skipped = (
                            fut.result()
                        )
                    except Exception as exc:
```

Then the persistence (the `human_result = ...` + `session.set_assessment(...)` block):

```python
                        # Per-type auto-apply: human_result is None unless this verdict was
                        # opted into auto-apply for this image type's reviewer.
                        _agent, _rr, ai_to_human_result = resolve(img_qid)
                        human_result = ai_to_human_result.get(ai_result)
                        session.set_assessment(
                            visit_id=int(visit_id_str),
                            blob_id=blob_id,
                            question_id=question_id,
                            result=human_result,
                            notes="",
                            ai_result=ai_result,
                            ai_notes=ai_notes,
                        )
                        session_updated = True
```

- [ ] **Step 8: Fix the return summary (no single global agent in per-type mode)**

Replace the return block (lines 354-363) with:

```python
    if ai_reviewers is not None:
        summary_agent_id = ",".join(sorted({s["agent_id"] for s in ai_reviewers.values() if s.get("agent_id")}))
        summary_agent_name = "per-image-type"
    else:
        summary_agent_id = ai_agent_id
        summary_agent_name = get_agent(ai_agent_id).name if ai_agent_id else ""

    return {
        "agent_id": summary_agent_id,
        "agent_name": summary_agent_name,
        "sessions_processed": len(session_ids),
        "total_reviewed": total_reviewed,
        "total_passed": total_passed,
        "total_failed": total_failed,
        "total_errors": total_errors,
        "total_skipped": total_skipped,
    }
```

- [ ] **Step 9: Run the test to verify it passes**

Run: `pytest connect_labs/audit/tests/test_ai_review_per_type.py -v`
Expected: PASS (all three tests).

- [ ] **Step 10: Run the existing AI-review test to confirm no regression**

Run: `pytest connect_labs/audit/tests/test_ai_review_auto_apply.py -v`
Expected: PASS (legacy path unchanged). If that test calls `_run_ai_review_on_sessions`
positionally with `ai_agent_id` as the 3rd arg, update the call there to keyword
`ai_agent_id=...` (the 3rd positional is now `access_token`).

- [ ] **Step 11: Commit**

```bash
git add connect_labs/audit/tasks.py connect_labs/audit/tests/test_ai_review_per_type.py
git commit -m "feat(audit): resolve AI reviewer per image type in run loop"
```

---

### Task 5: Wire `image_audits` through the create task and async view

**Files:**
- Modify: `connect_labs/audit/tasks.py` (`run_audit_creation`: lines 366-378 signature,
  ~424-448 criteria/stage setup, ~728-736 review call)
- Modify: `connect_labs/audit/views.py:919-993` (`ExperimentAuditCreateAsyncAPIView.post`)
- Test: `connect_labs/audit/tests/test_run_audit_creation_wiring.py` (create)

**Interfaces:**
- Consumes: `build_review_config` (Task 3); `_run_ai_review_on_sessions(..., ai_reviewers=...)` (Task 4).
- Produces: `run_audit_creation(..., image_audits=None, context_fields=None)` kwarg-accepting
  task; async view forwards `data["image_audits"]` / `data["context_fields"]`.

- [ ] **Step 1: Write the failing test (translation wiring)**

Create `connect_labs/audit/tests/test_run_audit_creation_wiring.py`. This isolates the
translation decision inside `run_audit_creation` without running the full Celery pipeline,
by asserting `build_review_config` drives `related_fields` and `ai_reviewers`:

```python
"""Wiring test: run_audit_creation translates image_audits via build_review_config."""
from connect_labs.audit.ai_review_config import build_review_config


def test_build_review_config_drives_related_fields_and_reviewers():
    image_audits = [
        {
            "image_path": "form/scale_photo",
            "reviewers": [
                {
                    "agent_id": "scale_validation",
                    "config": {"comparison_field": "form/child_weight"},
                    "auto_apply_actions": ["fail_unmatched"],
                }
            ],
        }
    ]
    related_fields, ai_reviewers = build_review_config(image_audits, context_fields=None)

    # filter rule scopes the audit; reading rule attaches the comparison value
    image_paths = {(r["image_path"], r["field_path"], r["filter_by_image"]) for r in related_fields}
    assert ("form/scale_photo", "", True) in image_paths
    assert ("form/scale_photo", "form/child_weight", False) in image_paths
    assert ai_reviewers["form/scale_photo"]["agent_id"] == "scale_validation"
    assert ai_reviewers["form/scale_photo"]["auto_apply_actions"] == ["fail_unmatched"]
```

(Task 3 already proves `build_review_config`; this test documents the exact contract
`run_audit_creation` relies on. It passes immediately after Task 3 — its purpose is to lock
the wiring contract before editing the task. Run it now to confirm green, then make the task
changes below.)

- [ ] **Step 2: Run the wiring test**

Run: `pytest connect_labs/audit/tests/test_run_audit_creation_wiring.py -v`
Expected: PASS (depends only on Task 3).

- [ ] **Step 3: Add task kwargs and translate at the top of `run_audit_creation`**

In `connect_labs/audit/tasks.py`, extend the `run_audit_creation` signature
(after line 378 `ai_auto_apply_actions: list[str] | None = None,`) with:

```python
    ai_agent_id: str | None = None,
    ai_auto_apply_actions: list[str] | None = None,
    image_audits: list[dict] | None = None,
    context_fields: list[dict] | None = None,
) -> dict:
```

Then replace the `related_fields = audit_criteria.related_fields or []` line (line 428) with:

```python
    # Per-image-type reviewers (new wizard) translate into the internal related_fields
    # rules + a question_id -> reviewer map. Legacy payloads (no image_audits) keep using
    # criteria.related_fields and the single ai_agent_id.
    if image_audits is not None:
        from connect_labs.audit.ai_review_config import build_review_config

        related_fields, ai_reviewers = build_review_config(image_audits, context_fields)
    else:
        ai_reviewers = None
        related_fields = audit_criteria.related_fields or []
```

- [ ] **Step 4: Count the AI stage when either mode is active**

Replace line 444 (`has_ai_agent = bool(ai_agent_id)`) with:

```python
    has_ai_agent = bool(ai_agent_id) or bool(ai_reviewers)
```

- [ ] **Step 5: Pass `ai_reviewers` into the review call**

In the AI-review stage, replace the `_run_ai_review_on_sessions(...)` call (lines 728-736)
with:

```python
                ai_review_results = _run_ai_review_on_sessions(
                    data_access=data_access,
                    session_ids=[s["id"] for s in sessions_created],
                    access_token=access_token,
                    opp_id=opp_id,
                    ai_agent_id=ai_agent_id,
                    auto_apply_actions=ai_auto_apply_actions,
                    ai_reviewers=ai_reviewers,
                    progress_callback=on_ai_review_progress,
                )
```

- [ ] **Step 6: Forward the new keys from the async view**

In `connect_labs/audit/views.py`, in `ExperimentAuditCreateAsyncAPIView.post`, after
line 933 (`ai_auto_apply_actions = data.get("ai_auto_apply_actions")`), add:

```python
            # New wizard: per-image-type reviewers + agent-less context fields.
            image_audits = data.get("image_audits")
            context_fields = data.get("context_fields")
```

Then in the `run_audit_creation.apply_async(kwargs={...})` block, after line 990
(`"ai_auto_apply_actions": ai_auto_apply_actions,`), add:

```python
                    "ai_auto_apply_actions": ai_auto_apply_actions,
                    "image_audits": image_audits,
                    "context_fields": context_fields,
```

- [ ] **Step 7: Run the full audit test module to confirm no regression**

Run: `pytest connect_labs/audit/ -v`
Expected: PASS (new + existing tests).

- [ ] **Step 8: Commit**

```bash
git add connect_labs/audit/tasks.py connect_labs/audit/views.py \
        connect_labs/audit/tests/test_run_audit_creation_wiring.py
git commit -m "feat(audit): wire image_audits/context_fields through create task + async view"
```

---

### Task 6: Merged wizard step — per-type reviewer, generic settings, context fields

**Files:**
- Modify: `connect_labs/templates/audit/audit_creation_wizard.html`
  - Step 5 body: lines 660-775 (replace the two sections)
  - Step 6 AI agent block: lines 812-870 (remove — moved into Step 5)
  - Alpine state: lines 1096, 1110-1113 (add per-type state)
  - `buildRelatedFieldsPayload`: lines 1292-1300 (replace)
  - `createAuditSessionAsync` payload: lines 1731, 1745-1753
  - JS helpers: lines 1986-1989 (init), 2006-2022 (agent helpers)

**Interfaces:**
- Consumes: `/audit/api/ai-agents/` now returns `config_fields` (Task 1);
  `/audit/api/opportunity/<id>/field-questions/` (Task 2); the async view accepts
  `image_audits` + `context_fields` (Task 5).
- Produces: POST body with `image_audits: [{image_path, reviewers:[{agent_id, config, auto_apply_actions}]}]`
  and `context_fields: [{image_path, field_path, label}]`. No longer sends `ai_agent_id`.

> **Note:** the wizard is server-rendered Alpine.js with no JS unit harness, so this task is
> build-then-verify in the browser. The backend behavior it drives is already covered by
> Tasks 1-5. Keep changes surgical against the anchor lines above.

- [ ] **Step 1: Add per-type Alpine state**

In `connect_labs/templates/audit/audit_creation_wizard.html`, replace the
`relatedFields: []` line inside `auditCriteria` (line 1096) — keep it for legacy/no-op but
add the new state. Replace the `// AI Review Agent state` block (lines 1110-1113) with:

```javascript
    // AI Review Agent state
    availableAIAgents: [],

    // Per-image-type reviewer config, keyed by image question path.
    //   imageReviewers[path] = { agentId: '', config: {}, autoApplyActions: [] }
    imageReviewers: {},

    // Form-field paths (non-image) for agent comparison-field pickers.
    availableFieldPaths: [],   // [{id, label, path}] unioned across selected opps
    fieldPathsLoading: false,
    fieldPathsLoadedKey: '',

    // Slim agent-less "context fields" shown to human reviewers.
    //   { imagePath: '', fieldPath: '', label: '' }
    contextFields: [],
```

- [ ] **Step 2: Ensure a reviewer slot exists when a type is checked**

Add a helper and call it when image types change. In the methods section (next to
`buildRelatedFieldsPayload`, line 1292), replace `buildRelatedFieldsPayload` (lines
1292-1300) with these methods:

```javascript
    // Ensure every checked image type has a reviewer slot; drop unchecked ones.
    syncImageReviewers() {
      const next = {};
      for (const path of this.selectedImagePaths) {
        next[path] = this.imageReviewers[path] || { agentId: '', config: {}, autoApplyActions: [] };
      }
      this.imageReviewers = next;
    },

    getAgentById(agentId) {
      return this.availableAIAgents.find(a => a.agent_id === agentId) || null;
    },

    getAgentConfigFields(agentId) {
      const agent = this.getAgentById(agentId);
      return agent && agent.config_fields ? agent.config_fields : [];
    },

    getAgentActions(agentId) {
      const agent = this.getAgentById(agentId);
      if (!agent || !agent.result_actions) return [];
      return Object.entries(agent.result_actions).map(([key, a]) => ({ key, ...a }));
    },

    // When a reviewer is (re)chosen for a type, reset its per-agent config + auto-apply.
    onReviewerChange(path) {
      const rev = this.imageReviewers[path];
      if (!rev) return;
      rev.config = {};
      rev.autoApplyActions = [];
    },

    // Block creation if any chosen agent has a required config field left blank.
    reviewerValidationError() {
      for (const path of this.selectedImagePaths) {
        const rev = this.imageReviewers[path];
        if (!rev || !rev.agentId) continue;
        for (const field of this.getAgentConfigFields(rev.agentId)) {
          if (field.required && !(rev.config && rev.config[field.key])) {
            return `Select a "${field.label}" for ${path}`;
          }
        }
      }
      return null;
    },

    // Build the image_audits payload (one reviewer per type; list-shaped for the future).
    buildImageAudits() {
      return this.selectedImagePaths.map(path => {
        const rev = this.imageReviewers[path];
        const reviewers = [];
        if (rev && rev.agentId) {
          reviewers.push({
            agent_id: rev.agentId,
            config: rev.config || {},
            auto_apply_actions: rev.autoApplyActions || [],
          });
        }
        return { image_path: path, reviewers };
      });
    },

    buildContextFields() {
      return this.contextFields
        .filter(c => c.imagePath && c.fieldPath)
        .map(c => ({ image_path: c.imagePath, field_path: c.fieldPath, label: c.label || '' }));
    },
```

- [ ] **Step 3: Load field paths alongside image types**

In `toggleOpportunitySelection` / `removeOpportunitySelection` / `clearAllSelections`
(lines 1175, 1183, 1242) the code already calls `this.loadImageTypes()`. After each
`this.loadImageTypes();` call in those three methods, add `this.loadFieldPaths();`.

Then add the loader next to `loadImageTypes` (after line 1288). It mirrors the
image-types union loader:

```javascript
    async loadFieldPaths() {
      const oppIds = this.selectedOpportunities.map(o => o.id);
      const key = [...oppIds].sort((a, b) => a - b).join(',');
      if (key === this.fieldPathsLoadedKey) return;
      if (oppIds.length === 0) {
        this.availableFieldPaths = [];
        this.fieldPathsLoadedKey = '';
        return;
      }
      this.fieldPathsLoading = true;
      try {
        const lists = await Promise.all(oppIds.map(async oppId => {
          const resp = await fetch(`/audit/api/opportunity/${oppId}/field-questions/`);
          if (!resp.ok) throw new Error(`opportunity ${oppId}: HTTP ${resp.status}`);
          return resp.json();
        }));
        const byId = new Map();
        lists.flat().forEach(t => { if (t && t.id && !byId.has(t.id)) byId.set(t.id, t); });
        this.availableFieldPaths = Array.from(byId.values())
          .sort((a, b) => (a.path || '').localeCompare(b.path || ''));
        this.fieldPathsLoadedKey = key;
      } catch (e) {
        // Degrade silently — the picker falls back to a free-text input.
        this.availableFieldPaths = [];
      } finally {
        this.fieldPathsLoading = false;
      }
    },
```

Also call `this.loadFieldPaths();` in `init()` next to line 1989 (`this.loadImageTypes();`).

- [ ] **Step 4: Replace the Step 5 body (image types + reviewers + context fields)**

Replace lines 660-775 (everything between the `<h2>Step 5...` heading at line 657 and the
closing `</div>` of the step at line 777) with the merged markup. Key change: each checked
image type reveals its reviewer dropdown, the agent's `config_fields`, and its auto-apply
list; the manual rules section is replaced by a collapsed "Context fields" control.

```html
    <!-- Section: image types + their AI reviewer -->
    <h3 class="text-md font-medium text-gray-800 mb-1">
      <i class="fa-solid fa-image mr-2 text-brand-indigo"></i>
      Select image types to audit, and an AI reviewer for each
    </h3>
    <p class="text-sm text-gray-600 mb-3">
      Auto-detected from recent submissions. Check a photo type to audit it; optionally pick an
      AI reviewer that runs on just that type. Leave all unchecked to audit every image.
    </p>
    <div x-show="imageTypesLoading" class="text-sm text-gray-500 mb-2">
      <i class="fa-solid fa-spinner fa-spin mr-2"></i>Detecting image types...
    </div>
    <div x-show="imageTypesError" class="text-sm text-red-600 mb-2" x-text="imageTypesError"></div>

    <div x-show="!imageTypesLoading && !imageTypesError && availableImageTypes.length > 0"
         class="flex flex-col gap-3 mb-2">
      <template x-for="t in availableImageTypes" :key="t.path">
        <div class="border rounded-md"
             :class="selectedImagePaths.includes(t.path) ? 'border-brand-indigo' : 'border-gray-200'">
          <!-- Type checkbox -->
          <label class="flex items-center gap-2 px-3 py-2 text-sm cursor-pointer"
                 :class="selectedImagePaths.includes(t.path) ? 'bg-brand-indigo/5' : 'bg-white'">
            <input type="checkbox" :value="t.path" x-model="selectedImagePaths"
                   @change="syncImageReviewers()"
                   class="h-4 w-4 text-brand-indigo focus:ring-brand-indigo border-gray-300 rounded">
            <span class="font-mono text-xs text-gray-700 break-all" x-text="t.path"></span>
          </label>

          <!-- Reviewer + its settings (only when this type is checked) -->
          <div x-show="selectedImagePaths.includes(t.path) && imageReviewers[t.path]"
               class="px-4 pb-3 pt-1 border-t border-gray-100 space-y-3">
            <div>
              <label class="block text-xs font-medium text-gray-600 mb-1">AI reviewer</label>
              <select x-model="imageReviewers[t.path].agentId" @change="onReviewerChange(t.path)"
                      class="w-full md:w-1/2 px-3 py-2 text-sm border border-gray-300 rounded-md focus:ring-brand-indigo focus:border-brand-indigo">
                <option value="">None — skip AI review</option>
                <template x-for="agent in availableAIAgents" :key="agent.agent_id">
                  <option :value="agent.agent_id" x-text="agent.name"></option>
                </template>
              </select>
            </div>

            <!-- Generic config_fields renderer (v1: form_field type) -->
            <template x-for="field in getAgentConfigFields(imageReviewers[t.path].agentId)" :key="field.key">
              <div>
                <label class="block text-xs font-medium text-gray-600 mb-1">
                  <span x-text="field.label"></span>
                  <span x-show="field.required" class="text-red-500">*</span>
                </label>
                <!-- type: form_field -> dropdown of field paths, free-text fallback -->
                <template x-if="field.type === 'form_field' && availableFieldPaths.length > 0">
                  <select x-model="imageReviewers[t.path].config[field.key]"
                          class="w-full md:w-1/2 px-3 py-2 text-sm border border-gray-300 rounded-md focus:ring-brand-indigo focus:border-brand-indigo">
                    <option value="">Select a field…</option>
                    <template x-for="fp in availableFieldPaths" :key="fp.path">
                      <option :value="fp.path" x-text="fp.path"></option>
                    </template>
                  </select>
                </template>
                <template x-if="field.type === 'form_field' && availableFieldPaths.length === 0">
                  <input type="text" x-model="imageReviewers[t.path].config[field.key]"
                         placeholder="form/field_path"
                         class="w-full md:w-1/2 px-3 py-2 text-sm border border-gray-300 rounded-md focus:ring-brand-indigo focus:border-brand-indigo">
                </template>
                <p x-show="field.help" class="text-xs text-gray-400 mt-1" x-text="field.help"></p>
              </div>
            </template>

            <!-- Auto-apply verdicts for this reviewer -->
            <div x-show="imageReviewers[t.path].agentId && getAgentActions(imageReviewers[t.path].agentId).length > 0">
              <p class="text-xs font-medium text-gray-700 mb-1">Auto-tag results before I review</p>
              <div class="space-y-1">
                <template x-for="action in getAgentActions(imageReviewers[t.path].agentId)" :key="action.key">
                  <label class="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                    <input type="checkbox" :value="action.key" x-model="imageReviewers[t.path].autoApplyActions"
                           class="h-4 w-4 text-brand-indigo focus:ring-brand-indigo border-gray-300 rounded">
                    <span>
                      Automatically apply <span class="font-medium" x-text="action.button_label"></span>
                      <span class="ml-1 font-semibold"
                            :class="action.human_result === 'fail' ? 'text-red-600' : 'text-green-600'"
                            x-text="'(' + action.human_result + ')'"></span>
                    </span>
                  </label>
                </template>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>
    <div x-show="!imageTypesLoading && !imageTypesError && availableImageTypes.length === 0"
         class="text-sm text-gray-500 mb-2">
      No image questions detected in the selected opportunities' recent submissions.
    </div>

    <!-- Collapsed: agent-less context fields shown to human reviewers -->
    <details class="mt-6 pt-6 border-t border-gray-200">
      <summary class="text-md font-medium text-gray-800 cursor-pointer">
        <i class="fa-solid fa-link mr-2 text-brand-indigo"></i>
        Context fields (optional)
      </summary>
      <p class="text-sm text-gray-600 my-3">
        Show extra form values next to an image during review — no AI agent involved.
      </p>
      <div class="space-y-3 mb-3">
        <template x-for="(c, index) in contextFields" :key="index">
          <div class="grid grid-cols-1 md:grid-cols-4 gap-3 items-end border border-gray-200 rounded-md p-3 bg-gray-50">
            <div>
              <label class="block text-xs font-medium text-gray-600 mb-1">Image path</label>
              <input type="text" x-model="c.imagePath" placeholder="form/photo_field"
                     class="w-full px-3 py-2 text-sm border border-gray-300 rounded-md">
            </div>
            <div>
              <label class="block text-xs font-medium text-gray-600 mb-1">Field path</label>
              <input type="text" x-model="c.fieldPath" placeholder="form/related_field"
                     class="w-full px-3 py-2 text-sm border border-gray-300 rounded-md">
            </div>
            <div>
              <label class="block text-xs font-medium text-gray-600 mb-1">Label</label>
              <input type="text" x-model="c.label" placeholder="Field Label"
                     class="w-full px-3 py-2 text-sm border border-gray-300 rounded-md">
            </div>
            <button @click="contextFields.splice(index, 1)" type="button"
                    class="text-red-500 hover:text-red-700 text-xs"><i class="fa-solid fa-times mr-1"></i>Remove</button>
          </div>
        </template>
      </div>
      <button @click="contextFields.push({ imagePath: '', fieldPath: '', label: '' })" type="button"
              class="text-sm text-brand-indigo hover:text-brand-deep-purple">
        <i class="fa-solid fa-plus mr-1"></i>Add context field
      </button>
    </details>
```

- [ ] **Step 5: Remove the now-duplicated AI Review block from Step 6**

Delete the entire `<!-- AI Review Agent Section -->` block in Step 6 — lines 812-870
(from `<div class="mb-6 pt-6 border-t border-gray-200">` containing the `<h3>...AI Review
Agent (Optional)` through its closing `</div>` just before `<div class="flex justify-end gap-4">`
at line 872). Reviewer selection now lives in Step 5.

- [ ] **Step 6: Send the new payload + block on validation**

In `createAuditSessionAsync` (lines 1703-1753): after the existing opportunity/criteria
validation at the top of the method (after line 1707), add the reviewer validation:

```javascript
        const reviewerError = this.reviewerValidationError();
        if (reviewerError) {
          alert(reviewerError);
          return;
        }
```

Replace the `relatedFields: this.buildRelatedFieldsPayload()` line in the `criteria` object
(line 1731) with `relatedFields: []` (criteria no longer carries the rules — the backend
builds them from `image_audits`). Then replace the `ai_agent_id` / `ai_auto_apply_actions`
lines in `payload` (lines 1750-1752) with:

```javascript
          image_audits: this.buildImageAudits(),
          context_fields: this.buildContextFields(),
```

- [ ] **Step 7: Build JS and run the dev server to verify it loads**

```bash
inv build-js
python manage.py runserver
```
Open `/audit/create/`, select an opportunity. Expected: image types load; checking one
reveals a reviewer dropdown; picking "Scale Image Validation" reveals a "Manual Scale Value"
picker; picking "MUAC OverZoom" reveals no extra field; the "Context fields" section is
collapsed at the bottom. No console errors.

- [ ] **Step 8: Commit**

```bash
git add connect_labs/templates/audit/audit_creation_wizard.html
git commit -m "feat(audit): merge image-type + AI-reviewer into one wizard step"
```

---

### Task 7: End-to-end verification on labs

**Files:** none (verification only)

- [ ] **Step 1: Run the full audit suite**

Run: `pytest connect_labs/audit/ -v`
Expected: PASS.

- [ ] **Step 2: Lint**

Run: `pre-commit run --all-files`
Expected: clean (or auto-fixed; re-stage and amend if hooks reformat).

- [ ] **Step 3: Browser verification (after deploy to labs)**

Per CLAUDE.md "Browser Verification", once merged + deployed, use `gstack browse` against
`labs.connect.dimagi.com/audit/create/`:
- Select a KMC opportunity with scale photos.
- Check the scale image type, pick Scale Image Validation, set Manual Scale Value to the
  weight field, tick an auto-apply verdict.
- Add a second image type with no reviewer.
- Create the audit; confirm the run completes and only the scale type got AI verdicts, with
  the chosen auto-apply applied.

- [ ] **Step 4: Final commit / PR**

Open a PR following `.github/PULL_REQUEST_TEMPLATE.md` (Product Description in plain English
for program staff; Technical Summary; Safety Assurance referencing the new tests + browser
check).

---

## Self-Review

**Spec coverage:**
- Agent declarative `config_fields` (Approach B) → Task 1. ✅
- `comparison_field` naming (runtime stays `reading`) → Task 1 + Global Constraints. ✅
- Merged wizard step, progressive disclosure → Task 6. ✅
- All agents listed per type (no affinity) → Task 6 reviewer dropdown. ✅
- Absorb reading into agent config; slim context fields → Task 6 (config_fields renderer +
  `<details>` context section). ✅
- `image_audits` / `context_fields` payload → Task 5 + Task 6. ✅
- Translate to internal `related_fields` + `ai_reviewers` map → Task 3. ✅
- Per-`question_id` run-loop lookup; one verdict per image → Task 4. ✅
- `field-questions` endpoint + `extract_field_paths` → Task 2. ✅
- Backward compatibility (legacy `ai_agent_id`) → Task 4 resolver + Task 5 branch. ✅
- Validation blocks blank required config → Task 6 `reviewerValidationError`. ✅
- Edge cases (no reviewer, zero images, unknown config type) → Task 4 skip + Task 6
  free-text fallback / `x-if` on known type. ✅
- Tests: agent endpoint, helper, translation, resolver, no-regression → Tasks 1-5. ✅

**Placeholder scan:** none — every code step shows full code.

**Type consistency:** `ai_reviewers` shape `{question_id: {"agent_id", "auto_apply_actions"}}`
is identical in Tasks 3, 4, 5. `config_fields` item keys (`key/label/type/required/help`)
match between Task 1 (declaration), Task 1 test, and Task 6 renderer. `image_audits` item
shape (`image_path`, `reviewers:[{agent_id, config, auto_apply_actions}]`) matches Task 3
test, Task 5, and Task 6 `buildImageAudits`. The `_run_ai_review_on_sessions` work-item
tuple is consistently 5-wide on collection and unpacked 8-wide from the worker
(`v_id, b_id, q_id, rdg, img_qid, ai_r, ai_n, skipped`) in Tasks 4 Steps 5-7.
