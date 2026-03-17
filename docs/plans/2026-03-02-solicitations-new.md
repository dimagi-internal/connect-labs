# Solicitations New — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a public-facing solicitations system (`solicitations`) where program managers post RFPs/EOIs, respondents submit as LLOEntities, and managers review — all backed by LabsRecord API with Django UI, JSON API, and MCP tool consumers on a shared data_access layer.

**Architecture:** Thin API Layer (Approach A). One `data_access.py` module contains all business logic and talks to LabsRecord API. Three consumers call into it: Django template views (server-rendered HTML), JSON API views (simple Django views returning JSON), and MCP tools. No local Django ORM storage.

**Tech Stack:** Django 4.x, LabsRecordAPIClient, Crispy Forms + Tailwind, Alpine.js (dynamic question builder), httpx, pytest + unittest.mock

**Design doc:** `docs/plans/2026-03-02-solicitations-new-design.md`

---

## Task 1: App Scaffolding & Registration

**Files:**
- Create: `commcare_connect/solicitations/__init__.py`
- Create: `commcare_connect/solicitations/apps.py`
- Create: `commcare_connect/solicitations/models.py` (empty placeholder)
- Create: `commcare_connect/solicitations/urls.py` (minimal)
- Create: `commcare_connect/solicitations/views.py` (minimal)
- Modify: `config/settings/base.py` (~line 155, LOCAL_APPS list)
- Modify: `config/urls.py` (~line 45, urlpatterns)
- Modify: `commcare_connect/labs/middleware.py` (~line 87, WHITELISTED_PREFIXES)

**Step 1: Create the app directory and files**

```python
# commcare_connect/solicitations/__init__.py
# (empty)
```

```python
# commcare_connect/solicitations/apps.py
from django.apps import AppConfig


class SolicitationsNewConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "commcare_connect.solicitations"
```

```python
# commcare_connect/solicitations/models.py
# Proxy models defined in Task 2
```

```python
# commcare_connect/solicitations/views.py
from django.http import JsonResponse


def health_check(request):
    return JsonResponse({"status": "ok", "app": "solicitations"})
```

```python
# commcare_connect/solicitations/urls.py
from django.urls import path

from . import views

app_name = "solicitations"

urlpatterns = [
    path("health/", views.health_check, name="health_check"),
]
```

**Step 2: Register the app in settings, URLs, and middleware**

In `config/settings/base.py`, add to `LOCAL_APPS` list (after `commcare_connect.solicitations`):
```python
"commcare_connect.solicitations",
```

In `config/urls.py`, add to `urlpatterns` (after the solicitations line):
```python
path("solicitations/", include("commcare_connect.solicitations.urls", namespace="solicitations")),
```

In `commcare_connect/labs/middleware.py`, add to `WHITELISTED_PREFIXES`:
```python
"/solicitations/",
```

**Step 3: Verify the app loads**

Run: `python manage.py check --deploy 2>&1 | head -5`
Expected: No errors related to solicitations

Run: `python manage.py shell -c "from commcare_connect.solicitations import views; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add commcare_connect/solicitations/ config/settings/base.py config/urls.py commcare_connect/labs/middleware.py
git commit -m "feat(solicitations): scaffold app, register in settings/urls/middleware"
```

---

## Task 2: Proxy Models

**Files:**
- Create: `commcare_connect/solicitations/models.py`
- Create: `commcare_connect/solicitations/tests/__init__.py`
- Create: `commcare_connect/solicitations/tests/test_models.py`

**Step 1: Write the failing tests**

```python
# commcare_connect/solicitations/tests/__init__.py
# (empty)
```

```python
# commcare_connect/solicitations/tests/test_models.py
import pytest
from commcare_connect.solicitations.models import (
    SolicitationRecord,
    ResponseRecord,
    ReviewRecord,
)


class TestSolicitationRecord:
    def _make(self, **overrides):
        defaults = {
            "id": 1,
            "experiment": "test_program",
            "type": "solicitation",
            "data": {
                "title": "Test Solicitation",
                "description": "A test",
                "scope_of_work": "Do the work",
                "solicitation_type": "rfp",
                "status": "active",
                "is_public": True,
                "questions": [{"id": "q1", "text": "Why?", "type": "text", "required": True}],
                "application_deadline": "2026-06-01",
                "expected_start_date": "2026-07-01",
                "expected_end_date": "2026-12-31",
                "estimated_scale": "1000 beneficiaries",
                "contact_email": "test@example.com",
                "created_by": "testuser",
                "program_name": "Test Program",
            },
        }
        defaults["data"].update(overrides.pop("data", {}))
        defaults.update(overrides)
        return SolicitationRecord(**defaults)

    def test_title(self):
        rec = self._make()
        assert rec.title == "Test Solicitation"

    def test_solicitation_type(self):
        rec = self._make()
        assert rec.solicitation_type == "rfp"

    def test_is_public(self):
        rec = self._make()
        assert rec.is_public is True

    def test_application_deadline_parses(self):
        rec = self._make()
        from datetime import date
        assert rec.application_deadline == date(2026, 6, 1)

    def test_application_deadline_none(self):
        rec = self._make(data={"application_deadline": None})
        assert rec.application_deadline is None

    def test_questions(self):
        rec = self._make()
        assert len(rec.questions) == 1
        assert rec.questions[0]["id"] == "q1"

    def test_can_accept_responses(self):
        rec = self._make(data={"status": "active"})
        assert rec.can_accept_responses() is True
        rec2 = self._make(data={"status": "closed"})
        assert rec2.can_accept_responses() is False


class TestResponseRecord:
    def _make(self, **overrides):
        defaults = {
            "id": 10,
            "experiment": "llo_entity_123",
            "type": "solicitation_response",
            "data": {
                "solicitation_id": 1,
                "llo_entity_id": "llo_entity_123",
                "llo_entity_name": "Test Org",
                "responses": {"q1": "Because"},
                "status": "submitted",
                "submitted_by_name": "Jane Doe",
                "submitted_by_email": "jane@example.com",
                "submission_date": "2026-05-15T10:00:00Z",
            },
        }
        defaults["data"].update(overrides.pop("data", {}))
        defaults.update(overrides)
        return ResponseRecord(**defaults)

    def test_solicitation_id(self):
        rec = self._make()
        assert rec.solicitation_id == 1

    def test_llo_entity_name(self):
        rec = self._make()
        assert rec.llo_entity_name == "Test Org"

    def test_responses_dict(self):
        rec = self._make()
        assert rec.responses == {"q1": "Because"}

    def test_status(self):
        rec = self._make()
        assert rec.status == "submitted"


class TestReviewRecord:
    def _make(self, **overrides):
        defaults = {
            "id": 20,
            "experiment": "llo_entity_123",
            "type": "solicitation_review",
            "data": {
                "response_id": 10,
                "score": 85,
                "recommendation": "approved",
                "notes": "Looks good",
                "tags": "experienced,local",
                "reviewer_username": "reviewer1",
                "review_date": "2026-05-20T14:00:00Z",
            },
        }
        defaults["data"].update(overrides.pop("data", {}))
        defaults.update(overrides)
        return ReviewRecord(**defaults)

    def test_score(self):
        rec = self._make()
        assert rec.score == 85

    def test_recommendation(self):
        rec = self._make()
        assert rec.recommendation == "approved"

    def test_reviewer_username(self):
        rec = self._make()
        assert rec.reviewer_username == "reviewer1"
```

**Step 2: Run tests to verify they fail**

Run: `pytest commcare_connect/solicitations/tests/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'SolicitationRecord'`

**Step 3: Implement the proxy models**

```python
# commcare_connect/solicitations/models.py
from datetime import date, datetime

from commcare_connect.labs.models import LocalLabsRecord


class SolicitationRecord(LocalLabsRecord):
    """Proxy model for solicitation records. Scoped by program_id."""

    @property
    def title(self):
        return self.data.get("title", "")

    @property
    def description(self):
        return self.data.get("description", "")

    @property
    def scope_of_work(self):
        return self.data.get("scope_of_work", "")

    @property
    def solicitation_type(self):
        return self.data.get("solicitation_type", "")

    @property
    def status(self):
        return self.data.get("status", "draft")

    @property
    def is_public(self):
        return self.data.get("is_public", False)

    @property
    def questions(self):
        return self.data.get("questions", [])

    @property
    def application_deadline(self):
        date_str = self.data.get("application_deadline")
        if date_str:
            try:
                return datetime.strptime(date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                return None
        return None

    @property
    def expected_start_date(self):
        date_str = self.data.get("expected_start_date")
        if date_str:
            try:
                return datetime.strptime(date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                return None
        return None

    @property
    def expected_end_date(self):
        date_str = self.data.get("expected_end_date")
        if date_str:
            try:
                return datetime.strptime(date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                return None
        return None

    @property
    def estimated_scale(self):
        return self.data.get("estimated_scale", "")

    @property
    def contact_email(self):
        return self.data.get("contact_email", "")

    @property
    def created_by(self):
        return self.data.get("created_by", "")

    @property
    def program_name(self):
        return self.data.get("program_name", "")

    def can_accept_responses(self):
        return self.status == "active"


class ResponseRecord(LocalLabsRecord):
    """Proxy model for response records. Scoped by llo_entity_id."""

    @property
    def solicitation_id(self):
        return self.data.get("solicitation_id")

    @property
    def llo_entity_id(self):
        return self.data.get("llo_entity_id", "")

    @property
    def llo_entity_name(self):
        return self.data.get("llo_entity_name", "")

    @property
    def responses(self):
        return self.data.get("responses", {})

    @property
    def status(self):
        return self.data.get("status", "draft")

    @property
    def submitted_by_name(self):
        return self.data.get("submitted_by_name", "")

    @property
    def submitted_by_email(self):
        return self.data.get("submitted_by_email", "")

    @property
    def submission_date(self):
        date_str = self.data.get("submission_date")
        if date_str:
            try:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None
        return None


class ReviewRecord(LocalLabsRecord):
    """Proxy model for review records."""

    @property
    def response_id(self):
        return self.data.get("response_id")

    @property
    def score(self):
        return self.data.get("score")

    @property
    def recommendation(self):
        return self.data.get("recommendation", "")

    @property
    def notes(self):
        return self.data.get("notes", "")

    @property
    def tags(self):
        return self.data.get("tags", "")

    @property
    def reviewer_username(self):
        return self.data.get("reviewer_username", "")

    @property
    def review_date(self):
        date_str = self.data.get("review_date")
        if date_str:
            try:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None
        return None
```

**Step 4: Run tests to verify they pass**

Run: `pytest commcare_connect/solicitations/tests/test_models.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add commcare_connect/solicitations/models.py commcare_connect/solicitations/tests/
git commit -m "feat(solicitations): add proxy models for solicitation, response, review"
```

---

## Task 3: Data Access Layer

**Files:**
- Create: `commcare_connect/solicitations/data_access.py`
- Create: `commcare_connect/solicitations/tests/test_data_access.py`

**Reference:** Follow `commcare_connect/tasks/data_access.py` pattern for constructor, and `commcare_connect/solicitations/data_access.py` for CRUD methods.

**Step 1: Write the failing tests**

```python
# commcare_connect/solicitations/tests/test_data_access.py
from unittest.mock import MagicMock, patch

import pytest

from commcare_connect.solicitations.data_access import SolicitationsDataAccess
from commcare_connect.solicitations.models import (
    ResponseRecord,
    ReviewRecord,
    SolicitationRecord,
)


@pytest.fixture
def mock_request():
    req = MagicMock()
    req.labs_context = {"program_id": "prog_1", "organization_id": "org_1"}
    req.session = {"labs_oauth": {"access_token": "test_token", "expires_at": 9999999999}}
    return req


@pytest.fixture
def mock_api_client():
    with patch(
        "commcare_connect.solicitations.data_access.LabsRecordAPIClient"
    ) as MockClient:
        client = MockClient.return_value
        yield client


class TestSolicitationCRUD:
    def test_get_solicitations(self, mock_request, mock_api_client):
        mock_api_client.get_records.return_value = [
            {"id": 1, "experiment": "prog_1", "type": "solicitation_new",
             "data": {"title": "Test", "status": "active", "is_public": True}},
        ]
        da = SolicitationsDataAccess(request=mock_request)
        results = da.get_solicitations()
        assert len(results) == 1
        assert isinstance(results[0], SolicitationRecord)
        assert results[0].title == "Test"

    def test_get_solicitation_by_id(self, mock_request, mock_api_client):
        mock_api_client.get_record_by_id.return_value = {
            "id": 1, "experiment": "prog_1", "type": "solicitation_new",
            "data": {"title": "Detail Test"},
        }
        da = SolicitationsDataAccess(request=mock_request)
        result = da.get_solicitation_by_id(1)
        assert isinstance(result, SolicitationRecord)
        assert result.title == "Detail Test"

    def test_get_public_solicitations(self, mock_request, mock_api_client):
        mock_api_client.get_records.return_value = [
            {"id": 1, "experiment": "prog_1", "type": "solicitation_new",
             "data": {"title": "Public", "status": "active", "is_public": True}},
            {"id": 2, "experiment": "prog_1", "type": "solicitation_new",
             "data": {"title": "Private", "status": "active", "is_public": False}},
        ]
        da = SolicitationsDataAccess(request=mock_request)
        results = da.get_public_solicitations()
        assert len(results) == 1
        assert results[0].title == "Public"

    def test_create_solicitation(self, mock_request, mock_api_client):
        mock_api_client.create_record.return_value = {
            "id": 5, "experiment": "prog_1", "type": "solicitation_new",
            "data": {"title": "New One"},
        }
        da = SolicitationsDataAccess(request=mock_request)
        result = da.create_solicitation({"title": "New One"})
        assert isinstance(result, SolicitationRecord)
        mock_api_client.create_record.assert_called_once()

    def test_update_solicitation(self, mock_request, mock_api_client):
        mock_api_client.update_record.return_value = {
            "id": 1, "experiment": "prog_1", "type": "solicitation_new",
            "data": {"title": "Updated"},
        }
        da = SolicitationsDataAccess(request=mock_request)
        result = da.update_solicitation(1, {"title": "Updated"})
        assert result.title == "Updated"


class TestResponseCRUD:
    def test_get_responses_for_solicitation(self, mock_request, mock_api_client):
        mock_api_client.get_records.return_value = [
            {"id": 10, "experiment": "llo_1", "type": "solicitation_new_response",
             "data": {"solicitation_id": 1, "status": "submitted"}},
        ]
        da = SolicitationsDataAccess(request=mock_request)
        results = da.get_responses_for_solicitation(1)
        assert len(results) == 1
        assert isinstance(results[0], ResponseRecord)

    def test_create_response(self, mock_request, mock_api_client):
        mock_api_client.create_record.return_value = {
            "id": 11, "experiment": "llo_1", "type": "solicitation_new_response",
            "data": {"solicitation_id": 1, "llo_entity_id": "llo_1"},
        }
        da = SolicitationsDataAccess(request=mock_request)
        result = da.create_response(
            solicitation_id=1,
            llo_entity_id="llo_1",
            data={"responses": {"q1": "answer"}},
        )
        assert isinstance(result, ResponseRecord)


class TestReviewCRUD:
    def test_create_review(self, mock_request, mock_api_client):
        mock_api_client.create_record.return_value = {
            "id": 20, "experiment": "llo_1", "type": "solicitation_new_review",
            "data": {"response_id": 10, "score": 90},
        }
        da = SolicitationsDataAccess(request=mock_request)
        result = da.create_review(response_id=10, data={"score": 90})
        assert isinstance(result, ReviewRecord)

    def test_update_review(self, mock_request, mock_api_client):
        mock_api_client.update_record.return_value = {
            "id": 20, "experiment": "llo_1", "type": "solicitation_new_review",
            "data": {"response_id": 10, "score": 95},
        }
        da = SolicitationsDataAccess(request=mock_request)
        result = da.update_review(20, {"score": 95})
        assert result.score == 95
```

**Step 2: Run tests to verify they fail**

Run: `pytest commcare_connect/solicitations/tests/test_data_access.py -v`
Expected: FAIL — `ImportError: cannot import name 'SolicitationsDataAccess'`

**Step 3: Implement the data access layer**

```python
# commcare_connect/solicitations/data_access.py
import logging

from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
from commcare_connect.solicitations.models import (
    ResponseRecord,
    ReviewRecord,
    SolicitationRecord,
)

logger = logging.getLogger(__name__)

# LabsRecord type constants
TYPE_SOLICITATION = "solicitation_new"
TYPE_RESPONSE = "solicitation_new_response"
TYPE_REVIEW = "solicitation_new_review"


class SolicitationsDataAccess:
    """Data access layer for solicitations. All CRUD via LabsRecord API."""

    def __init__(self, program_id=None, access_token=None, request=None):
        self.program_id = program_id
        self.access_token = access_token

        if request and hasattr(request, "labs_context"):
            labs_context = request.labs_context
            if not self.program_id and "program_id" in labs_context:
                self.program_id = labs_context["program_id"]

        if not self.access_token and request:
            labs_oauth = getattr(request, "session", {}).get("labs_oauth", {})
            self.access_token = labs_oauth.get("access_token")

        self.labs_api = LabsRecordAPIClient(
            access_token=self.access_token,
        )

    # ── Solicitation CRUD ──────────────────────────────────────

    def get_solicitations(self, status=None, solicitation_type=None):
        """Get solicitations for current program."""
        records = self.labs_api.get_records(
            experiment=self.program_id,
            record_type=TYPE_SOLICITATION,
        )
        results = [SolicitationRecord(**r) for r in records]
        if status:
            results = [r for r in results if r.status == status]
        if solicitation_type:
            results = [r for r in results if r.solicitation_type == solicitation_type]
        return results

    def get_public_solicitations(self, solicitation_type=None):
        """Get all public, active solicitations across all programs."""
        records = self.labs_api.get_records(
            record_type=TYPE_SOLICITATION,
        )
        results = [SolicitationRecord(**r) for r in records]
        results = [r for r in results if r.is_public and r.status == "active"]
        if solicitation_type:
            results = [r for r in results if r.solicitation_type == solicitation_type]
        return results

    def get_solicitation_by_id(self, solicitation_id):
        """Get a single solicitation by ID."""
        record = self.labs_api.get_record_by_id(solicitation_id)
        if record:
            return SolicitationRecord(**record)
        return None

    def create_solicitation(self, data):
        """Create a new solicitation under the current program."""
        record = self.labs_api.create_record(
            experiment=self.program_id,
            record_type=TYPE_SOLICITATION,
            data=data,
        )
        return SolicitationRecord(**record)

    def update_solicitation(self, solicitation_id, data):
        """Update an existing solicitation."""
        record = self.labs_api.update_record(
            record_id=solicitation_id,
            data=data,
        )
        return SolicitationRecord(**record)

    # ── Response CRUD ──────────────────────────────────────────

    def get_responses_for_solicitation(self, solicitation_id):
        """Get all responses for a solicitation."""
        records = self.labs_api.get_records(
            record_type=TYPE_RESPONSE,
        )
        results = [ResponseRecord(**r) for r in records]
        return [r for r in results if r.solicitation_id == solicitation_id]

    def get_response_by_id(self, response_id):
        """Get a single response by ID."""
        record = self.labs_api.get_record_by_id(response_id)
        if record:
            return ResponseRecord(**record)
        return None

    def create_response(self, solicitation_id, llo_entity_id, data):
        """Create a new response."""
        data["solicitation_id"] = solicitation_id
        data["llo_entity_id"] = llo_entity_id
        record = self.labs_api.create_record(
            experiment=llo_entity_id,
            record_type=TYPE_RESPONSE,
            data=data,
        )
        return ResponseRecord(**record)

    def update_response(self, response_id, data):
        """Update an existing response."""
        record = self.labs_api.update_record(
            record_id=response_id,
            data=data,
        )
        return ResponseRecord(**record)

    # ── Review CRUD ────────────────────────────────────────────

    def get_reviews_for_response(self, response_id):
        """Get all reviews for a response."""
        records = self.labs_api.get_records(
            record_type=TYPE_REVIEW,
        )
        results = [ReviewRecord(**r) for r in records]
        return [r for r in results if r.response_id == response_id]

    def get_review_by_id(self, review_id):
        """Get a single review by ID."""
        record = self.labs_api.get_record_by_id(review_id)
        if record:
            return ReviewRecord(**record)
        return None

    def create_review(self, response_id, data):
        """Create a new review for a response."""
        data["response_id"] = response_id
        # Get the response to find the llo_entity_id for scoping
        response = self.get_response_by_id(response_id)
        experiment = response.llo_entity_id if response else ""
        record = self.labs_api.create_record(
            experiment=experiment,
            record_type=TYPE_REVIEW,
            data=data,
        )
        return ReviewRecord(**record)

    def update_review(self, review_id, data):
        """Update an existing review."""
        record = self.labs_api.update_record(
            record_id=review_id,
            data=data,
        )
        return ReviewRecord(**record)
```

**Step 4: Run tests to verify they pass**

Run: `pytest commcare_connect/solicitations/tests/test_data_access.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add commcare_connect/solicitations/data_access.py commcare_connect/solicitations/tests/test_data_access.py
git commit -m "feat(solicitations): add data access layer with solicitation/response/review CRUD"
```

---

## Task 4: Forms

**Files:**
- Create: `commcare_connect/solicitations/forms.py`
- Create: `commcare_connect/solicitations/tests/test_forms.py`

**Step 1: Write the failing tests**

```python
# commcare_connect/solicitations/tests/test_forms.py
import pytest

from commcare_connect.solicitations.forms import (
    SolicitationForm,
    SolicitationResponseForm,
    ReviewForm,
)


class TestSolicitationForm:
    def test_valid_minimal(self):
        form = SolicitationForm(data={
            "title": "Test RFP",
            "description": "A description",
            "solicitation_type": "rfp",
            "status": "draft",
            "is_public": True,
            "contact_email": "test@example.com",
        })
        assert form.is_valid(), form.errors

    def test_missing_title(self):
        form = SolicitationForm(data={
            "description": "A description",
            "solicitation_type": "rfp",
            "status": "draft",
        })
        assert not form.is_valid()
        assert "title" in form.errors


class TestSolicitationResponseForm:
    def _questions(self):
        return [
            {"id": "q1", "text": "Why apply?", "type": "textarea", "required": True},
            {"id": "q2", "text": "Team size?", "type": "number", "required": False},
        ]

    def test_valid_with_required_question(self):
        form = SolicitationResponseForm(
            questions=self._questions(),
            data={"question_q1": "We are qualified"},
        )
        assert form.is_valid(), form.errors

    def test_missing_required_question(self):
        form = SolicitationResponseForm(
            questions=self._questions(),
            data={},
        )
        assert not form.is_valid()
        assert "question_q1" in form.errors


class TestReviewForm:
    def test_valid(self):
        form = ReviewForm(data={
            "score": 85,
            "recommendation": "approved",
            "notes": "Good application",
        })
        assert form.is_valid(), form.errors

    def test_score_out_of_range(self):
        form = ReviewForm(data={
            "score": 150,
            "recommendation": "approved",
        })
        assert not form.is_valid()
```

**Step 2: Run tests to verify they fail**

Run: `pytest commcare_connect/solicitations/tests/test_forms.py -v`
Expected: FAIL — `ImportError`

**Step 3: Implement the forms**

```python
# commcare_connect/solicitations/forms.py
from django import forms


class SolicitationForm(forms.Form):
    """Form for creating/editing a solicitation."""

    title = forms.CharField(max_length=255)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    scope_of_work = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}), required=False)
    solicitation_type = forms.ChoiceField(choices=[("eoi", "Expression of Interest"), ("rfp", "Request for Proposals")])
    status = forms.ChoiceField(choices=[("draft", "Draft"), ("active", "Active"), ("closed", "Closed")])
    is_public = forms.BooleanField(required=False, initial=True)
    application_deadline = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    expected_start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    expected_end_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    estimated_scale = forms.CharField(max_length=255, required=False)
    contact_email = forms.EmailField(required=False)
    # questions handled via Alpine.js, submitted as hidden JSON field
    questions_json = forms.CharField(widget=forms.HiddenInput(), required=False)

    def to_data_dict(self):
        """Convert cleaned form data to dict for data_access layer."""
        d = self.cleaned_data.copy()
        import json
        d["questions"] = json.loads(d.pop("questions_json", "[]") or "[]")
        # Convert dates to strings
        for key in ("application_deadline", "expected_start_date", "expected_end_date"):
            val = d.get(key)
            d[key] = val.isoformat() if val else None
        return d


class SolicitationResponseForm(forms.Form):
    """Dynamic form built from solicitation questions."""

    def __init__(self, questions=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for q in (questions or []):
            q_id = q["id"]
            field_name = f"question_{q_id}"
            q_type = q.get("type", "text")
            required = q.get("required", False)
            label = q.get("text", "")

            if q_type == "textarea":
                self.fields[field_name] = forms.CharField(
                    label=label, required=required,
                    widget=forms.Textarea(attrs={"rows": 3}),
                )
            elif q_type == "number":
                self.fields[field_name] = forms.IntegerField(
                    label=label, required=required,
                )
            elif q_type == "multiple_choice":
                choices = [(o, o) for o in q.get("options", [])]
                self.fields[field_name] = forms.ChoiceField(
                    label=label, required=required, choices=choices,
                )
            else:
                self.fields[field_name] = forms.CharField(
                    label=label, required=required,
                )

    def get_responses_dict(self):
        """Return {question_id: answer} dict."""
        result = {}
        for key, val in self.cleaned_data.items():
            if key.startswith("question_"):
                q_id = key[len("question_"):]
                result[q_id] = val
        return result


class ReviewForm(forms.Form):
    """Form for reviewing a response."""

    score = forms.IntegerField(min_value=1, max_value=100)
    recommendation = forms.ChoiceField(choices=[
        ("under_review", "Under Review"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("needs_revision", "Needs Revision"),
    ])
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), required=False)
    tags = forms.CharField(max_length=255, required=False)
```

**Step 4: Run tests to verify they pass**

Run: `pytest commcare_connect/solicitations/tests/test_forms.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add commcare_connect/solicitations/forms.py commcare_connect/solicitations/tests/test_forms.py
git commit -m "feat(solicitations): add solicitation, response, and review forms"
```

---

## Task 5: Public Views (No Login Required)

**Files:**
- Modify: `commcare_connect/solicitations/views.py`
- Modify: `commcare_connect/solicitations/urls.py`
- Create: `commcare_connect/templates/solicitations/public_list.html`
- Create: `commcare_connect/templates/solicitations/public_detail.html`

**Step 1: Write the public views**

Replace `commcare_connect/solicitations/views.py`:

```python
# commcare_connect/solicitations/views.py
import json
import logging

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from commcare_connect.solicitations.data_access import SolicitationsDataAccess
from commcare_connect.solicitations.forms import (
    ReviewForm,
    SolicitationForm,
    SolicitationResponseForm,
)

logger = logging.getLogger(__name__)


# ── Permission Mixins ──────────────────────────────────────────

class LabsLoginRequiredMixin(LoginRequiredMixin):
    """Redirect to labs login."""
    login_url = "/labs/login/"


class ManagerRequiredMixin(LabsLoginRequiredMixin, UserPassesTestMixin):
    """Require authenticated labs user (manager access)."""
    def test_func(self):
        return getattr(self.request.user, "is_labs_user", False)


# ── Helpers ────────────────────────────────────────────────────

def _get_data_access(request):
    """Create data access from request. Works for public (no token) and authed."""
    return SolicitationsDataAccess(request=request)


def _get_public_data_access():
    """Create data access for public endpoints (no auth token)."""
    return SolicitationsDataAccess()


# ── Public Views (no login) ───────────────────────────────────

class PublicSolicitationListView(TemplateView):
    template_name = "solicitations/public_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        solicitation_type = self.request.GET.get("type")
        try:
            da = _get_data_access(self.request)
            ctx["solicitations"] = da.get_public_solicitations(
                solicitation_type=solicitation_type,
            )
        except Exception:
            logger.exception("Failed to load public solicitations")
            ctx["solicitations"] = []
        ctx["selected_type"] = solicitation_type or ""
        return ctx


class PublicSolicitationDetailView(TemplateView):
    template_name = "solicitations/public_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        try:
            da = _get_data_access(self.request)
            solicitation = da.get_solicitation_by_id(pk)
            if not solicitation or not solicitation.is_public:
                raise Http404("Solicitation not found")
            ctx["solicitation"] = solicitation
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load solicitation %s", pk)
            raise Http404("Solicitation not found")
        return ctx


# ── Manager Views ─────────────────────────────────────────────
# (Implemented in Task 6)


# ── Response Views ────────────────────────────────────────────
# (Implemented in Task 7)


# ── Review Views ──────────────────────────────────────────────
# (Implemented in Task 8)
```

**Step 2: Update URLs**

```python
# commcare_connect/solicitations/urls.py
from django.urls import path

from . import views

app_name = "solicitations"

urlpatterns = [
    # Public (no login required)
    path("", views.PublicSolicitationListView.as_view(), name="public_list"),
    path("<int:pk>/", views.PublicSolicitationDetailView.as_view(), name="public_detail"),
]
```

**Step 3: Create the public list template**

```html
{# commcare_connect/templates/solicitations/public_list.html #}
{% extends "base.html" %}

{% block content %}
<div class="max-w-6xl mx-auto px-4 py-8">
    <div class="mb-8">
        <h1 class="text-3xl font-bold text-gray-900">Open Solicitations</h1>
        <p class="mt-2 text-gray-600">Browse active requests for proposals and expressions of interest.</p>
    </div>

    {# Type filter #}
    <div class="flex gap-2 mb-6">
        <a href="?type="
           class="px-4 py-2 rounded-lg text-sm font-medium {% if not selected_type %}bg-indigo-600 text-white{% else %}bg-gray-100 text-gray-700 hover:bg-gray-200{% endif %}">
            All
        </a>
        <a href="?type=rfp"
           class="px-4 py-2 rounded-lg text-sm font-medium {% if selected_type == 'rfp' %}bg-indigo-600 text-white{% else %}bg-gray-100 text-gray-700 hover:bg-gray-200{% endif %}">
            RFP
        </a>
        <a href="?type=eoi"
           class="px-4 py-2 rounded-lg text-sm font-medium {% if selected_type == 'eoi' %}bg-indigo-600 text-white{% else %}bg-gray-100 text-gray-700 hover:bg-gray-200{% endif %}">
            EOI
        </a>
    </div>

    {# Solicitation cards #}
    {% if solicitations %}
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {% for s in solicitations %}
        <a href="{% url 'solicitations:public_detail' pk=s.pk %}"
           class="block bg-white rounded-xl shadow-sm border border-gray-200 p-6 hover:shadow-md transition-shadow">
            <div class="flex items-center gap-2 mb-3">
                <span class="px-2 py-0.5 text-xs font-semibold rounded-full
                    {% if s.solicitation_type == 'rfp' %}bg-blue-100 text-blue-700{% else %}bg-green-100 text-green-700{% endif %}">
                    {{ s.solicitation_type|upper }}
                </span>
                {% if s.program_name %}
                <span class="text-xs text-gray-500">{{ s.program_name }}</span>
                {% endif %}
            </div>
            <h2 class="text-lg font-semibold text-gray-900 mb-2">{{ s.title }}</h2>
            <p class="text-sm text-gray-600 mb-4 line-clamp-3">{{ s.description }}</p>
            <div class="flex items-center justify-between text-xs text-gray-500">
                {% if s.application_deadline %}
                <span><i class="fa-regular fa-calendar mr-1"></i>Deadline: {{ s.application_deadline|date:"M d, Y" }}</span>
                {% endif %}
                {% if s.estimated_scale %}
                <span>{{ s.estimated_scale }}</span>
                {% endif %}
            </div>
        </a>
        {% endfor %}
    </div>
    {% else %}
    <div class="text-center py-16 bg-white rounded-xl border border-gray-200">
        <i class="fa-regular fa-folder-open text-4xl text-gray-300 mb-4"></i>
        <p class="text-gray-500">No open solicitations at this time.</p>
    </div>
    {% endif %}
</div>
{% endblock %}
```

**Step 4: Create the public detail template**

```html
{# commcare_connect/templates/solicitations/public_detail.html #}
{% extends "base.html" %}

{% block content %}
<div class="max-w-4xl mx-auto px-4 py-8">
    {# Breadcrumb #}
    <nav class="mb-6 text-sm text-gray-500">
        <a href="{% url 'solicitations:public_list' %}" class="hover:text-indigo-600">Solicitations</a>
        <span class="mx-2">/</span>
        <span class="text-gray-900">{{ solicitation.title }}</span>
    </nav>

    {# Header #}
    <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-8 mb-6">
        <div class="flex items-center gap-3 mb-4">
            <span class="px-3 py-1 text-sm font-semibold rounded-full
                {% if solicitation.solicitation_type == 'rfp' %}bg-blue-100 text-blue-700{% else %}bg-green-100 text-green-700{% endif %}">
                {{ solicitation.solicitation_type|upper }}
            </span>
            {% if solicitation.program_name %}
            <span class="text-sm text-gray-500">{{ solicitation.program_name }}</span>
            {% endif %}
        </div>
        <h1 class="text-2xl font-bold text-gray-900 mb-4">{{ solicitation.title }}</h1>
        <p class="text-gray-700 whitespace-pre-line">{{ solicitation.description }}</p>
    </div>

    {# Details grid #}
    <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
        {% if solicitation.scope_of_work %}
        <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-6 md:col-span-2">
            <h2 class="text-lg font-semibold text-gray-900 mb-3">Scope of Work</h2>
            <p class="text-gray-700 whitespace-pre-line">{{ solicitation.scope_of_work }}</p>
        </div>
        {% endif %}

        <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
            <h2 class="text-lg font-semibold text-gray-900 mb-3">Key Dates</h2>
            <dl class="space-y-2 text-sm">
                {% if solicitation.application_deadline %}
                <div class="flex justify-between">
                    <dt class="text-gray-500">Application Deadline</dt>
                    <dd class="font-medium">{{ solicitation.application_deadline|date:"M d, Y" }}</dd>
                </div>
                {% endif %}
                {% if solicitation.expected_start_date %}
                <div class="flex justify-between">
                    <dt class="text-gray-500">Expected Start</dt>
                    <dd class="font-medium">{{ solicitation.expected_start_date|date:"M d, Y" }}</dd>
                </div>
                {% endif %}
                {% if solicitation.expected_end_date %}
                <div class="flex justify-between">
                    <dt class="text-gray-500">Expected End</dt>
                    <dd class="font-medium">{{ solicitation.expected_end_date|date:"M d, Y" }}</dd>
                </div>
                {% endif %}
            </dl>
        </div>

        <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
            <h2 class="text-lg font-semibold text-gray-900 mb-3">Details</h2>
            <dl class="space-y-2 text-sm">
                {% if solicitation.estimated_scale %}
                <div class="flex justify-between">
                    <dt class="text-gray-500">Estimated Scale</dt>
                    <dd class="font-medium">{{ solicitation.estimated_scale }}</dd>
                </div>
                {% endif %}
                {% if solicitation.contact_email %}
                <div class="flex justify-between">
                    <dt class="text-gray-500">Contact</dt>
                    <dd class="font-medium">{{ solicitation.contact_email }}</dd>
                </div>
                {% endif %}
            </dl>
        </div>
    </div>

    {# Questions preview #}
    {% if solicitation.questions %}
    <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-6 mb-6">
        <h2 class="text-lg font-semibold text-gray-900 mb-3">Response Questions</h2>
        <ol class="list-decimal list-inside space-y-2 text-sm text-gray-700">
            {% for q in solicitation.questions %}
            <li>
                {{ q.text }}
                {% if q.required %}<span class="text-red-500 text-xs ml-1">(Required)</span>{% endif %}
            </li>
            {% endfor %}
        </ol>
    </div>
    {% endif %}

    {# CTA #}
    {% if solicitation.can_accept_responses %}
    <div class="text-center">
        <a href="{% url 'solicitations:respond' pk=solicitation.pk %}"
           class="inline-flex items-center px-8 py-3 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors font-semibold text-lg">
            <i class="fa-solid fa-paper-plane mr-2"></i>
            Respond to this Solicitation
        </a>
    </div>
    {% else %}
    <div class="text-center py-4">
        <span class="text-gray-500">This solicitation is no longer accepting responses.</span>
    </div>
    {% endif %}
</div>
{% endblock %}
```

**Step 5: Verify templates load (manual check)**

Run: `python manage.py shell -c "from django.template.loader import get_template; get_template('solicitations/public_list.html'); print('OK')"`
Expected: `OK`

**Step 6: Commit**

```bash
git add commcare_connect/solicitations/views.py commcare_connect/solicitations/urls.py commcare_connect/templates/solicitations/
git commit -m "feat(solicitations): add public list and detail views with templates"
```

---

## Task 6: Manager Views & Templates

**Files:**
- Modify: `commcare_connect/solicitations/views.py` (add manager views)
- Modify: `commcare_connect/solicitations/urls.py` (add manager URLs)
- Create: `commcare_connect/templates/solicitations/manage_list.html`
- Create: `commcare_connect/templates/solicitations/solicitation_form.html`
- Create: `commcare_connect/templates/solicitations/responses_list.html`

**Step 1: Add manager views to views.py**

Append after the public views section:

```python
# ── Manager Views ─────────────────────────────────────────────

class ManageSolicitationsView(ManagerRequiredMixin, TemplateView):
    template_name = "solicitations/manage_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        try:
            da = _get_data_access(self.request)
            solicitations = da.get_solicitations()
            # Add response count to each solicitation
            for s in solicitations:
                responses = da.get_responses_for_solicitation(s.pk)
                s._response_count = len(responses)
            ctx["solicitations"] = solicitations
        except Exception:
            logger.exception("Failed to load managed solicitations")
            ctx["solicitations"] = []
        return ctx


class SolicitationCreateView(ManagerRequiredMixin, TemplateView):
    template_name = "solicitations/solicitation_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = SolicitationForm()
        ctx["is_create"] = True
        return ctx

    def post(self, request, *args, **kwargs):
        form = SolicitationForm(request.POST)
        if form.is_valid():
            data = form.to_data_dict()
            data["created_by"] = getattr(request.user, "username", "")
            program_name = request.labs_context.get("program_name", "") if hasattr(request, "labs_context") else ""
            data["program_name"] = program_name
            try:
                da = _get_data_access(request)
                da.create_solicitation(data)
                return redirect("solicitations:manage_list")
            except Exception:
                logger.exception("Failed to create solicitation")
                form.add_error(None, "Failed to create solicitation. Please try again.")
        return render(request, self.template_name, {"form": form, "is_create": True})


class SolicitationEditView(ManagerRequiredMixin, TemplateView):
    template_name = "solicitations/solicitation_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        da = _get_data_access(self.request)
        solicitation = da.get_solicitation_by_id(pk)
        if not solicitation:
            raise Http404
        import json
        initial = {
            "title": solicitation.title,
            "description": solicitation.description,
            "scope_of_work": solicitation.scope_of_work,
            "solicitation_type": solicitation.solicitation_type,
            "status": solicitation.status,
            "is_public": solicitation.is_public,
            "application_deadline": solicitation.application_deadline,
            "expected_start_date": solicitation.expected_start_date,
            "expected_end_date": solicitation.expected_end_date,
            "estimated_scale": solicitation.estimated_scale,
            "contact_email": solicitation.contact_email,
            "questions_json": json.dumps(solicitation.questions),
        }
        ctx["form"] = SolicitationForm(initial=initial)
        ctx["solicitation"] = solicitation
        ctx["is_create"] = False
        ctx["existing_questions_json"] = json.dumps(solicitation.questions)
        return ctx

    def post(self, request, *args, **kwargs):
        pk = kwargs["pk"]
        form = SolicitationForm(request.POST)
        if form.is_valid():
            data = form.to_data_dict()
            try:
                da = _get_data_access(request)
                da.update_solicitation(pk, data)
                return redirect("solicitations:manage_list")
            except Exception:
                logger.exception("Failed to update solicitation %s", pk)
                form.add_error(None, "Failed to update solicitation.")
        return render(request, self.template_name, {"form": form, "is_create": False})


class ResponsesListView(ManagerRequiredMixin, TemplateView):
    template_name = "solicitations/responses_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        try:
            da = _get_data_access(self.request)
            solicitation = da.get_solicitation_by_id(pk)
            if not solicitation:
                raise Http404
            responses = da.get_responses_for_solicitation(pk)
            # Attach reviews to each response
            for resp in responses:
                resp._reviews = da.get_reviews_for_response(resp.pk)
                resp._latest_review = resp._reviews[-1] if resp._reviews else None
            ctx["solicitation"] = solicitation
            ctx["responses"] = responses
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load responses for solicitation %s", pk)
            ctx["solicitation"] = None
            ctx["responses"] = []
        return ctx
```

**Step 2: Add manager URLs**

```python
# Add to urlpatterns in urls.py:
    # Manager (login required)
    path("manage/", views.ManageSolicitationsView.as_view(), name="manage_list"),
    path("create/", views.SolicitationCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.SolicitationEditView.as_view(), name="edit"),
    path("<int:pk>/responses/", views.ResponsesListView.as_view(), name="responses_list"),
```

**Step 3: Create manager templates**

Create `commcare_connect/templates/solicitations/manage_list.html`:
```html
{% extends "base.html" %}

{% block content %}
<div class="max-w-6xl mx-auto px-4 py-8">
    <div class="flex items-center justify-between mb-8">
        <h1 class="text-2xl font-bold text-gray-900">Manage Solicitations</h1>
        <a href="{% url 'solicitations:create' %}"
           class="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors font-medium">
            <i class="fa-solid fa-plus mr-1"></i> Create Solicitation
        </a>
    </div>

    {% if solicitations %}
    <div class="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
        <table class="w-full">
            <thead class="bg-gray-50 border-b border-gray-200">
                <tr>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Title</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Deadline</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Responses</th>
                    <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
                </tr>
            </thead>
            <tbody class="divide-y divide-gray-200">
                {% for s in solicitations %}
                <tr class="hover:bg-gray-50">
                    <td class="px-6 py-4 text-sm font-medium text-gray-900">{{ s.title }}</td>
                    <td class="px-6 py-4">
                        <span class="px-2 py-0.5 text-xs font-semibold rounded-full
                            {% if s.solicitation_type == 'rfp' %}bg-blue-100 text-blue-700{% else %}bg-green-100 text-green-700{% endif %}">
                            {{ s.solicitation_type|upper }}
                        </span>
                    </td>
                    <td class="px-6 py-4">
                        <span class="px-2 py-0.5 text-xs font-semibold rounded-full
                            {% if s.status == 'active' %}bg-green-100 text-green-700
                            {% elif s.status == 'draft' %}bg-yellow-100 text-yellow-700
                            {% else %}bg-gray-100 text-gray-700{% endif %}">
                            {{ s.status|title }}
                        </span>
                    </td>
                    <td class="px-6 py-4 text-sm text-gray-500">
                        {{ s.application_deadline|date:"M d, Y"|default:"—" }}
                    </td>
                    <td class="px-6 py-4 text-sm text-gray-500">{{ s._response_count }}</td>
                    <td class="px-6 py-4 text-right text-sm space-x-3">
                        <a href="{% url 'solicitations:edit' pk=s.pk %}" class="text-indigo-600 hover:text-indigo-800">Edit</a>
                        <a href="{% url 'solicitations:responses_list' pk=s.pk %}" class="text-indigo-600 hover:text-indigo-800">Responses</a>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
    <div class="text-center py-16 bg-white rounded-xl border border-gray-200">
        <i class="fa-regular fa-folder-open text-4xl text-gray-300 mb-4"></i>
        <p class="text-gray-500 mb-4">No solicitations yet.</p>
        <a href="{% url 'solicitations:create' %}" class="text-indigo-600 hover:text-indigo-800 font-medium">Create your first solicitation</a>
    </div>
    {% endif %}
</div>
{% endblock %}
```

Create `commcare_connect/templates/solicitations/solicitation_form.html`:
```html
{% extends "base.html" %}
{% load crispy_forms_tags %}

{% block content %}
<div class="max-w-4xl mx-auto px-4 py-8">
    <nav class="mb-6 text-sm text-gray-500">
        <a href="{% url 'solicitations:manage_list' %}" class="hover:text-indigo-600">Manage</a>
        <span class="mx-2">/</span>
        <span class="text-gray-900">{% if is_create %}Create{% else %}Edit{% endif %} Solicitation</span>
    </nav>

    <h1 class="text-2xl font-bold text-gray-900 mb-6">
        {% if is_create %}Create Solicitation{% else %}Edit Solicitation{% endif %}
    </h1>

    {% if form.non_field_errors %}
    <div class="bg-red-50 border-l-4 border-red-400 p-4 mb-6">
        {% for error in form.non_field_errors %}
        <p class="text-red-700">{{ error }}</p>
        {% endfor %}
    </div>
    {% endif %}

    <form method="post" class="space-y-6" id="solicitation-form">
        {% csrf_token %}

        <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-6 space-y-4">
            <h2 class="text-lg font-semibold text-gray-900">Basic Information</h2>
            {{ form.title|as_crispy_field }}
            {{ form.description|as_crispy_field }}
            {{ form.scope_of_work|as_crispy_field }}
            <div class="grid grid-cols-2 gap-4">
                {{ form.solicitation_type|as_crispy_field }}
                {{ form.status|as_crispy_field }}
            </div>
            <div>{{ form.is_public|as_crispy_field }}</div>
        </div>

        <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-6 space-y-4">
            <h2 class="text-lg font-semibold text-gray-900">Dates & Details</h2>
            <div class="grid grid-cols-3 gap-4">
                {{ form.application_deadline|as_crispy_field }}
                {{ form.expected_start_date|as_crispy_field }}
                {{ form.expected_end_date|as_crispy_field }}
            </div>
            {{ form.estimated_scale|as_crispy_field }}
            {{ form.contact_email|as_crispy_field }}
        </div>

        {# Dynamic question builder (Alpine.js) #}
        <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-6"
             x-data="questionBuilder()">
            <div class="flex items-center justify-between mb-4">
                <h2 class="text-lg font-semibold text-gray-900">Questions</h2>
                <button type="button" @click="addQuestion()"
                        class="px-3 py-1 text-sm bg-indigo-50 text-indigo-700 rounded-lg hover:bg-indigo-100">
                    <i class="fa-solid fa-plus mr-1"></i> Add Question
                </button>
            </div>

            <template x-for="(q, idx) in questions" :key="q.id">
                <div class="border border-gray-200 rounded-lg p-4 mb-3">
                    <div class="flex items-start gap-3">
                        <span class="text-sm font-medium text-gray-400 mt-2" x-text="idx + 1 + '.'"></span>
                        <div class="flex-1 space-y-3">
                            <input type="text" x-model="q.text" placeholder="Question text"
                                   class="w-full border-gray-300 rounded-lg text-sm">
                            <div class="flex gap-3">
                                <select x-model="q.type" class="border-gray-300 rounded-lg text-sm">
                                    <option value="text">Short Text</option>
                                    <option value="textarea">Long Text</option>
                                    <option value="number">Number</option>
                                    <option value="multiple_choice">Multiple Choice</option>
                                </select>
                                <label class="flex items-center gap-1 text-sm text-gray-600">
                                    <input type="checkbox" x-model="q.required"> Required
                                </label>
                            </div>
                            <template x-if="q.type === 'multiple_choice'">
                                <input type="text" x-model="q.options_str" placeholder="Comma-separated options"
                                       class="w-full border-gray-300 rounded-lg text-sm">
                            </template>
                        </div>
                        <button type="button" @click="removeQuestion(idx)"
                                class="text-red-400 hover:text-red-600 mt-2">
                            <i class="fa-solid fa-trash-can"></i>
                        </button>
                    </div>
                </div>
            </template>

            <p x-show="questions.length === 0" class="text-sm text-gray-400 text-center py-4">
                No questions added yet. Click "Add Question" to get started.
            </p>

            {{ form.questions_json }}
        </div>

        <div class="flex justify-end gap-4">
            <a href="{% url 'solicitations:manage_list' %}"
               class="px-6 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50">Cancel</a>
            <button type="submit"
                    class="px-6 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 font-medium">
                {% if is_create %}Create{% else %}Update{% endif %} Solicitation
            </button>
        </div>
    </form>
</div>

<script>
function questionBuilder() {
    var existing = {{ existing_questions_json|default:"[]" }};
    var counter = existing.length;
    return {
        questions: existing.map(function(q) {
            q.options_str = (q.options || []).join(", ");
            return q;
        }),
        addQuestion: function() {
            counter++;
            this.questions.push({
                id: "q" + counter,
                text: "",
                type: "text",
                required: false,
                options_str: "",
            });
        },
        removeQuestion: function(idx) {
            this.questions.splice(idx, 1);
        },
        serializeQuestions: function() {
            return JSON.stringify(this.questions.map(function(q) {
                var out = {id: q.id, text: q.text, type: q.type, required: q.required};
                if (q.type === "multiple_choice") {
                    out.options = q.options_str.split(",").map(function(s) { return s.trim(); }).filter(Boolean);
                }
                return out;
            }));
        },
    };
}

document.getElementById("solicitation-form").addEventListener("submit", function() {
    var el = document.querySelector("[x-data]").__x.$data;
    if (el && el.serializeQuestions) {
        document.getElementById("id_questions_json").value = el.serializeQuestions();
    }
});
</script>
{% endblock %}
```

Create `commcare_connect/templates/solicitations/responses_list.html`:
```html
{% extends "base.html" %}

{% block content %}
<div class="max-w-6xl mx-auto px-4 py-8">
    <nav class="mb-6 text-sm text-gray-500">
        <a href="{% url 'solicitations:manage_list' %}" class="hover:text-indigo-600">Manage</a>
        <span class="mx-2">/</span>
        <span class="text-gray-900">Responses: {{ solicitation.title }}</span>
    </nav>

    <h1 class="text-2xl font-bold text-gray-900 mb-6">Responses to: {{ solicitation.title }}</h1>

    {% if responses %}
    <div class="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
        <table class="w-full">
            <thead class="bg-gray-50 border-b border-gray-200">
                <tr>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Organization</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Submitted By</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Date</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Recommendation</th>
                    <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Score</th>
                    <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
                </tr>
            </thead>
            <tbody class="divide-y divide-gray-200">
                {% for r in responses %}
                <tr class="hover:bg-gray-50">
                    <td class="px-6 py-4 text-sm font-medium text-gray-900">{{ r.llo_entity_name }}</td>
                    <td class="px-6 py-4 text-sm text-gray-500">{{ r.submitted_by_name }}</td>
                    <td class="px-6 py-4">
                        <span class="px-2 py-0.5 text-xs font-semibold rounded-full
                            {% if r.status == 'submitted' %}bg-green-100 text-green-700{% else %}bg-yellow-100 text-yellow-700{% endif %}">
                            {{ r.status|title }}
                        </span>
                    </td>
                    <td class="px-6 py-4 text-sm text-gray-500">{{ r.submission_date|date:"M d, Y"|default:"—" }}</td>
                    <td class="px-6 py-4 text-sm text-gray-500">
                        {% if r._latest_review %}{{ r._latest_review.recommendation|title }}{% else %}—{% endif %}
                    </td>
                    <td class="px-6 py-4 text-sm text-gray-500">
                        {% if r._latest_review %}{{ r._latest_review.score }}{% else %}—{% endif %}
                    </td>
                    <td class="px-6 py-4 text-right text-sm space-x-3">
                        <a href="{% url 'solicitations:response_detail' pk=r.pk %}" class="text-indigo-600 hover:text-indigo-800">View</a>
                        <a href="{% url 'solicitations:review' pk=r.pk %}" class="text-indigo-600 hover:text-indigo-800">Review</a>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
    <div class="text-center py-16 bg-white rounded-xl border border-gray-200">
        <p class="text-gray-500">No responses yet.</p>
    </div>
    {% endif %}
</div>
{% endblock %}
```

**Step 4: Commit**

```bash
git add commcare_connect/solicitations/views.py commcare_connect/solicitations/urls.py commcare_connect/templates/solicitations/
git commit -m "feat(solicitations): add manager views — manage list, create, edit, responses list"
```

---

## Task 7: Response Views & Templates

**Files:**
- Modify: `commcare_connect/solicitations/views.py`
- Modify: `commcare_connect/solicitations/urls.py`
- Create: `commcare_connect/templates/solicitations/respond.html`
- Create: `commcare_connect/templates/solicitations/response_detail.html`

**Step 1: Add response views to views.py**

Append after manager views:

```python
# ── Response Views ────────────────────────────────────────────

class RespondView(LabsLoginRequiredMixin, TemplateView):
    template_name = "solicitations/respond.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        da = _get_data_access(self.request)
        solicitation = da.get_solicitation_by_id(pk)
        if not solicitation or not solicitation.can_accept_responses():
            raise Http404
        ctx["solicitation"] = solicitation
        ctx["form"] = SolicitationResponseForm(questions=solicitation.questions)
        # Get user's LLO entities from session
        user = self.request.user
        ctx["llo_entities"] = getattr(user, "organizations", []) if hasattr(user, "organizations") else []
        return ctx

    def post(self, request, *args, **kwargs):
        pk = kwargs["pk"]
        da = _get_data_access(request)
        solicitation = da.get_solicitation_by_id(pk)
        if not solicitation or not solicitation.can_accept_responses():
            raise Http404

        form = SolicitationResponseForm(questions=solicitation.questions, data=request.POST)
        llo_entity_id = request.POST.get("llo_entity_id", "")
        llo_entity_name = request.POST.get("llo_entity_name", "")
        create_new = request.POST.get("create_new_entity") == "on"

        if create_new:
            new_name = request.POST.get("new_entity_name", "").strip()
            new_short = request.POST.get("new_entity_short_name", "").strip()
            if not new_name:
                form.add_error(None, "New entity name is required.")
                return render(request, self.template_name, {
                    "solicitation": solicitation, "form": form, "llo_entities": [],
                })
            llo_entity_id = f"new_{new_short or new_name}".lower().replace(" ", "_")
            llo_entity_name = new_name

        if form.is_valid() and llo_entity_id:
            is_draft = request.POST.get("action") == "save_draft"
            data = {
                "responses": form.get_responses_dict(),
                "llo_entity_name": llo_entity_name,
                "status": "draft" if is_draft else "submitted",
                "submitted_by_name": getattr(request.user, "name", ""),
                "submitted_by_email": getattr(request.user, "email", ""),
            }
            if not is_draft:
                data["submission_date"] = timezone.now().isoformat()
            try:
                da.create_response(
                    solicitation_id=pk,
                    llo_entity_id=llo_entity_id,
                    data=data,
                )
                return redirect("solicitations:public_detail", pk=pk)
            except Exception:
                logger.exception("Failed to create response")
                form.add_error(None, "Failed to submit response.")

        return render(request, self.template_name, {
            "solicitation": solicitation, "form": form, "llo_entities": [],
        })


class ResponseDetailView(LabsLoginRequiredMixin, TemplateView):
    template_name = "solicitations/response_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        da = _get_data_access(self.request)
        response = da.get_response_by_id(pk)
        if not response:
            raise Http404
        solicitation = da.get_solicitation_by_id(response.solicitation_id)
        reviews = da.get_reviews_for_response(pk)
        ctx["response"] = response
        ctx["solicitation"] = solicitation
        ctx["reviews"] = reviews
        # Build Q&A pairs
        qa_pairs = []
        if solicitation:
            q_map = {q["id"]: q for q in solicitation.questions}
            for q_id, answer in response.responses.items():
                q = q_map.get(q_id, {"text": q_id})
                qa_pairs.append({"question": q.get("text", q_id), "answer": answer})
        ctx["qa_pairs"] = qa_pairs
        return ctx
```

**Step 2: Add response URLs**

```python
# Add to urlpatterns:
    path("<int:pk>/respond/", views.RespondView.as_view(), name="respond"),
    path("response/<int:pk>/", views.ResponseDetailView.as_view(), name="response_detail"),
```

**Step 3: Create the respond template**

Create `commcare_connect/templates/solicitations/respond.html`:
```html
{% extends "base.html" %}
{% load crispy_forms_tags %}

{% block content %}
<div class="max-w-3xl mx-auto px-4 py-8">
    <nav class="mb-6 text-sm text-gray-500">
        <a href="{% url 'solicitations:public_list' %}" class="hover:text-indigo-600">Solicitations</a>
        <span class="mx-2">/</span>
        <a href="{% url 'solicitations:public_detail' pk=solicitation.pk %}" class="hover:text-indigo-600">{{ solicitation.title }}</a>
        <span class="mx-2">/</span>
        <span class="text-gray-900">Respond</span>
    </nav>

    <h1 class="text-2xl font-bold text-gray-900 mb-2">Respond to: {{ solicitation.title }}</h1>
    <p class="text-gray-500 mb-8">{{ solicitation.solicitation_type|upper }} — Deadline: {{ solicitation.application_deadline|date:"M d, Y"|default:"None" }}</p>

    {% if form.non_field_errors %}
    <div class="bg-red-50 border-l-4 border-red-400 p-4 mb-6">
        {% for error in form.non_field_errors %}
        <p class="text-red-700">{{ error }}</p>
        {% endfor %}
    </div>
    {% endif %}

    <form method="post" class="space-y-6" x-data="{ createNew: false }">
        {% csrf_token %}

        {# LLO Entity selection #}
        <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
            <h2 class="text-lg font-semibold text-gray-900 mb-4">Responding Organization</h2>

            {% if llo_entities %}
            <div class="space-y-3" x-show="!createNew">
                <label class="block text-sm font-medium text-gray-700">Select your organization</label>
                <select name="llo_entity_id" class="w-full border-gray-300 rounded-lg">
                    <option value="">-- Select --</option>
                    {% for org in llo_entities %}
                    <option value="{{ org.slug }}" data-name="{{ org.name }}">{{ org.name }}</option>
                    {% endfor %}
                </select>
                <input type="hidden" name="llo_entity_name" id="llo_entity_name_hidden">
            </div>
            {% endif %}

            <div class="mt-3">
                <label class="flex items-center gap-2 text-sm text-indigo-600 cursor-pointer">
                    <input type="checkbox" name="create_new_entity" x-model="createNew"> Create new entity
                </label>
            </div>

            <div x-show="createNew" class="mt-4 space-y-3 border-t border-gray-200 pt-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700">Entity Name</label>
                    <input type="text" name="new_entity_name" class="w-full border-gray-300 rounded-lg mt-1">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700">Short Name</label>
                    <input type="text" name="new_entity_short_name" class="w-full border-gray-300 rounded-lg mt-1">
                </div>
            </div>
        </div>

        {# Questions #}
        <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-6 space-y-4">
            <h2 class="text-lg font-semibold text-gray-900 mb-4">Questions</h2>
            {% for field in form %}
                {{ field|as_crispy_field }}
            {% endfor %}
        </div>

        {# Actions #}
        <div class="flex justify-end gap-4">
            <button type="submit" name="action" value="save_draft"
                    class="px-6 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50">
                Save Draft
            </button>
            <button type="submit" name="action" value="submit"
                    class="px-6 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 font-medium">
                Submit Response
            </button>
        </div>
    </form>
</div>

<script>
// Sync llo_entity_name hidden field with select
document.querySelector('[name="llo_entity_id"]')?.addEventListener("change", function() {
    var opt = this.options[this.selectedIndex];
    document.getElementById("llo_entity_name_hidden").value = opt.dataset.name || "";
});
</script>
{% endblock %}
```

Create `commcare_connect/templates/solicitations/response_detail.html`:
```html
{% extends "base.html" %}

{% block content %}
<div class="max-w-4xl mx-auto px-4 py-8">
    <nav class="mb-6 text-sm text-gray-500">
        {% if solicitation %}
        <a href="{% url 'solicitations:responses_list' pk=solicitation.pk %}" class="hover:text-indigo-600">Responses</a>
        <span class="mx-2">/</span>
        {% endif %}
        <span class="text-gray-900">{{ response.llo_entity_name }}</span>
    </nav>

    <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-8 mb-6">
        <div class="flex items-center justify-between mb-4">
            <h1 class="text-2xl font-bold text-gray-900">{{ response.llo_entity_name }}</h1>
            <span class="px-3 py-1 text-sm font-semibold rounded-full
                {% if response.status == 'submitted' %}bg-green-100 text-green-700{% else %}bg-yellow-100 text-yellow-700{% endif %}">
                {{ response.status|title }}
            </span>
        </div>
        <dl class="grid grid-cols-2 gap-4 text-sm">
            <div>
                <dt class="text-gray-500">Submitted By</dt>
                <dd class="font-medium">{{ response.submitted_by_name }} ({{ response.submitted_by_email }})</dd>
            </div>
            <div>
                <dt class="text-gray-500">Submission Date</dt>
                <dd class="font-medium">{{ response.submission_date|date:"M d, Y H:i"|default:"—" }}</dd>
            </div>
        </dl>
    </div>

    {# Q&A pairs #}
    <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-8 mb-6">
        <h2 class="text-lg font-semibold text-gray-900 mb-4">Responses</h2>
        {% for qa in qa_pairs %}
        <div class="{% if not forloop.last %}mb-4 pb-4 border-b border-gray-100{% endif %}">
            <p class="text-sm font-medium text-gray-700 mb-1">{{ qa.question }}</p>
            <p class="text-sm text-gray-900">{{ qa.answer }}</p>
        </div>
        {% endfor %}
    </div>

    {# Reviews #}
    {% if reviews %}
    <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-8 mb-6">
        <h2 class="text-lg font-semibold text-gray-900 mb-4">Reviews</h2>
        {% for rev in reviews %}
        <div class="{% if not forloop.last %}mb-4 pb-4 border-b border-gray-100{% endif %}">
            <div class="flex items-center justify-between mb-2">
                <span class="text-sm font-medium text-gray-700">{{ rev.reviewer_username }}</span>
                <span class="text-sm text-gray-500">Score: {{ rev.score }}/100</span>
            </div>
            <span class="px-2 py-0.5 text-xs font-semibold rounded-full bg-gray-100 text-gray-700">{{ rev.recommendation|title }}</span>
            {% if rev.notes %}<p class="mt-2 text-sm text-gray-600">{{ rev.notes }}</p>{% endif %}
        </div>
        {% endfor %}
    </div>
    {% endif %}

    {# Review action #}
    <div class="text-center">
        <a href="{% url 'solicitations:review' pk=response.pk %}"
           class="inline-flex items-center px-6 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 font-medium">
            <i class="fa-solid fa-clipboard-check mr-2"></i> Add Review
        </a>
    </div>
</div>
{% endblock %}
```

**Step 4: Commit**

```bash
git add commcare_connect/solicitations/views.py commcare_connect/solicitations/urls.py commcare_connect/templates/solicitations/
git commit -m "feat(solicitations): add respond and response detail views with templates"
```

---

## Task 8: Review View & Template

**Files:**
- Modify: `commcare_connect/solicitations/views.py`
- Modify: `commcare_connect/solicitations/urls.py`
- Create: `commcare_connect/templates/solicitations/review_form.html`

**Step 1: Add review view to views.py**

Append after response views:

```python
# ── Review Views ──────────────────────────────────────────────

class ReviewView(ManagerRequiredMixin, TemplateView):
    template_name = "solicitations/review_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]  # response_id
        da = _get_data_access(self.request)
        response = da.get_response_by_id(pk)
        if not response:
            raise Http404
        solicitation = da.get_solicitation_by_id(response.solicitation_id)
        # Build Q&A pairs
        qa_pairs = []
        if solicitation:
            q_map = {q["id"]: q for q in solicitation.questions}
            for q_id, answer in response.responses.items():
                q = q_map.get(q_id, {"text": q_id})
                qa_pairs.append({"question": q.get("text", q_id), "answer": answer})
        # Check for existing review by this user
        reviews = da.get_reviews_for_response(pk)
        username = getattr(self.request.user, "username", "")
        existing = next((r for r in reviews if r.reviewer_username == username), None)
        initial = {}
        if existing:
            initial = {
                "score": existing.score,
                "recommendation": existing.recommendation,
                "notes": existing.notes,
                "tags": existing.tags,
            }
        ctx["form"] = ReviewForm(initial=initial)
        ctx["response"] = response
        ctx["solicitation"] = solicitation
        ctx["qa_pairs"] = qa_pairs
        ctx["existing_review"] = existing
        return ctx

    def post(self, request, *args, **kwargs):
        pk = kwargs["pk"]  # response_id
        form = ReviewForm(request.POST)
        if form.is_valid():
            da = _get_data_access(request)
            data = form.cleaned_data.copy()
            data["reviewer_username"] = getattr(request.user, "username", "")
            data["review_date"] = timezone.now().isoformat()
            # Check for existing review to update
            reviews = da.get_reviews_for_response(pk)
            username = data["reviewer_username"]
            existing = next((r for r in reviews if r.reviewer_username == username), None)
            try:
                if existing:
                    da.update_review(existing.pk, data)
                else:
                    da.create_review(response_id=pk, data=data)
                return redirect("solicitations:response_detail", pk=pk)
            except Exception:
                logger.exception("Failed to save review")
                form.add_error(None, "Failed to save review.")

        da = _get_data_access(request)
        response = da.get_response_by_id(pk)
        solicitation = da.get_solicitation_by_id(response.solicitation_id) if response else None
        return render(request, self.template_name, {
            "form": form, "response": response, "solicitation": solicitation,
            "qa_pairs": [], "existing_review": None,
        })
```

**Step 2: Add review URL**

```python
# Add to urlpatterns:
    path("response/<int:pk>/review/", views.ReviewView.as_view(), name="review"),
```

**Step 3: Create the review template**

Create `commcare_connect/templates/solicitations/review_form.html`:
```html
{% extends "base.html" %}
{% load crispy_forms_tags %}

{% block content %}
<div class="max-w-4xl mx-auto px-4 py-8">
    <nav class="mb-6 text-sm text-gray-500">
        <a href="{% url 'solicitations:response_detail' pk=response.pk %}" class="hover:text-indigo-600">Response</a>
        <span class="mx-2">/</span>
        <span class="text-gray-900">{% if existing_review %}Update{% else %}Add{% endif %} Review</span>
    </nav>

    <h1 class="text-2xl font-bold text-gray-900 mb-6">
        Review: {{ response.llo_entity_name }}
    </h1>

    {# Q&A summary #}
    <div class="bg-gray-50 rounded-xl border border-gray-200 p-6 mb-6">
        <h2 class="text-lg font-semibold text-gray-900 mb-3">Response Summary</h2>
        {% for qa in qa_pairs %}
        <div class="{% if not forloop.last %}mb-3 pb-3 border-b border-gray-200{% endif %}">
            <p class="text-sm font-medium text-gray-700">{{ qa.question }}</p>
            <p class="text-sm text-gray-900 mt-1">{{ qa.answer }}</p>
        </div>
        {% endfor %}
    </div>

    {# Review form #}
    <form method="post" class="bg-white rounded-xl shadow-sm border border-gray-200 p-6 space-y-4">
        {% csrf_token %}

        {% if form.non_field_errors %}
        <div class="bg-red-50 border-l-4 border-red-400 p-4">
            {% for error in form.non_field_errors %}
            <p class="text-red-700">{{ error }}</p>
            {% endfor %}
        </div>
        {% endif %}

        {{ form.score|as_crispy_field }}
        {{ form.recommendation|as_crispy_field }}
        {{ form.notes|as_crispy_field }}
        {{ form.tags|as_crispy_field }}

        <div class="flex justify-end gap-4 pt-4 border-t border-gray-200">
            <a href="{% url 'solicitations:response_detail' pk=response.pk %}"
               class="px-6 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50">Cancel</a>
            <button type="submit"
                    class="px-6 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 font-medium">
                {% if existing_review %}Update{% else %}Submit{% endif %} Review
            </button>
        </div>
    </form>
</div>
{% endblock %}
```

**Step 4: Commit**

```bash
git add commcare_connect/solicitations/views.py commcare_connect/solicitations/urls.py commcare_connect/templates/solicitations/review_form.html
git commit -m "feat(solicitations): add review view and template"
```

---

## Task 9: JSON API Views

**Files:**
- Create: `commcare_connect/solicitations/api_views.py`
- Create: `commcare_connect/solicitations/tests/test_api_views.py`
- Modify: `commcare_connect/solicitations/urls.py`

**Step 1: Write the failing tests**

```python
# commcare_connect/solicitations/tests/test_api_views.py
import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from commcare_connect.solicitations.api_views import (
    api_solicitations_list,
    api_solicitation_detail,
)


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture
def mock_da():
    with patch("commcare_connect.solicitations.api_views._get_data_access") as mock:
        da = MagicMock()
        mock.return_value = da
        yield da


class TestAPISolicitationsList:
    def test_get_returns_json(self, rf, mock_da):
        mock_da.get_public_solicitations.return_value = []
        request = rf.get("/solicitations/api/solicitations/")
        response = api_solicitations_list(request)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert "solicitations" in data
```

**Step 2: Run tests to verify they fail**

Run: `pytest commcare_connect/solicitations/tests/test_api_views.py -v`
Expected: FAIL — `ImportError`

**Step 3: Implement the API views**

```python
# commcare_connect/solicitations/api_views.py
import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from commcare_connect.solicitations.data_access import SolicitationsDataAccess

logger = logging.getLogger(__name__)


def _get_data_access(request):
    return SolicitationsDataAccess(request=request)


def _serialize_solicitation(s):
    return {
        "id": s.pk,
        "title": s.title,
        "description": s.description,
        "scope_of_work": s.scope_of_work,
        "solicitation_type": s.solicitation_type,
        "status": s.status,
        "is_public": s.is_public,
        "questions": s.questions,
        "application_deadline": s.application_deadline.isoformat() if s.application_deadline else None,
        "expected_start_date": s.expected_start_date.isoformat() if s.expected_start_date else None,
        "expected_end_date": s.expected_end_date.isoformat() if s.expected_end_date else None,
        "estimated_scale": s.estimated_scale,
        "contact_email": s.contact_email,
        "created_by": s.created_by,
        "program_name": s.program_name,
    }


def _serialize_response(r):
    return {
        "id": r.pk,
        "solicitation_id": r.solicitation_id,
        "llo_entity_id": r.llo_entity_id,
        "llo_entity_name": r.llo_entity_name,
        "responses": r.responses,
        "status": r.status,
        "submitted_by_name": r.submitted_by_name,
        "submitted_by_email": r.submitted_by_email,
        "submission_date": r.submission_date.isoformat() if r.submission_date else None,
    }


def _serialize_review(r):
    return {
        "id": r.pk,
        "response_id": r.response_id,
        "score": r.score,
        "recommendation": r.recommendation,
        "notes": r.notes,
        "tags": r.tags,
        "reviewer_username": r.reviewer_username,
        "review_date": r.review_date.isoformat() if r.review_date else None,
    }


# ── Solicitations ─────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_solicitations_list(request):
    da = _get_data_access(request)
    if request.method == "GET":
        status = request.GET.get("status")
        sol_type = request.GET.get("type")
        is_public = request.GET.get("is_public")
        if is_public == "true":
            results = da.get_public_solicitations(solicitation_type=sol_type)
        else:
            results = da.get_solicitations(status=status, solicitation_type=sol_type)
        return JsonResponse({"solicitations": [_serialize_solicitation(s) for s in results]})

    # POST — create
    try:
        body = json.loads(request.body)
        result = da.create_solicitation(body)
        return JsonResponse({"solicitation": _serialize_solicitation(result)}, status=201)
    except Exception as e:
        logger.exception("API create solicitation failed")
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
@require_http_methods(["GET", "PUT"])
def api_solicitation_detail(request, pk):
    da = _get_data_access(request)
    if request.method == "GET":
        result = da.get_solicitation_by_id(pk)
        if not result:
            return JsonResponse({"error": "Not found"}, status=404)
        return JsonResponse({"solicitation": _serialize_solicitation(result)})

    # PUT — update
    try:
        body = json.loads(request.body)
        result = da.update_solicitation(pk, body)
        return JsonResponse({"solicitation": _serialize_solicitation(result)})
    except Exception as e:
        logger.exception("API update solicitation %s failed", pk)
        return JsonResponse({"error": str(e)}, status=400)


# ── Responses ─────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_responses_list(request):
    da = _get_data_access(request)
    if request.method == "GET":
        solicitation_id = request.GET.get("solicitation_id")
        if not solicitation_id:
            return JsonResponse({"error": "solicitation_id required"}, status=400)
        results = da.get_responses_for_solicitation(int(solicitation_id))
        return JsonResponse({"responses": [_serialize_response(r) for r in results]})

    # POST — create
    try:
        body = json.loads(request.body)
        result = da.create_response(
            solicitation_id=body.pop("solicitation_id"),
            llo_entity_id=body.pop("llo_entity_id"),
            data=body,
        )
        return JsonResponse({"response": _serialize_response(result)}, status=201)
    except Exception as e:
        logger.exception("API create response failed")
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
@require_http_methods(["GET", "PUT"])
def api_response_detail(request, pk):
    da = _get_data_access(request)
    if request.method == "GET":
        result = da.get_response_by_id(pk)
        if not result:
            return JsonResponse({"error": "Not found"}, status=404)
        return JsonResponse({"response": _serialize_response(result)})

    try:
        body = json.loads(request.body)
        result = da.update_response(pk, body)
        return JsonResponse({"response": _serialize_response(result)})
    except Exception as e:
        logger.exception("API update response %s failed", pk)
        return JsonResponse({"error": str(e)}, status=400)


# ── Reviews ───────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def api_reviews_create(request):
    da = _get_data_access(request)
    try:
        body = json.loads(request.body)
        result = da.create_review(
            response_id=body.pop("response_id"),
            data=body,
        )
        return JsonResponse({"review": _serialize_review(result)}, status=201)
    except Exception as e:
        logger.exception("API create review failed")
        return JsonResponse({"error": str(e)}, status=400)


@csrf_exempt
@require_http_methods(["GET", "PUT"])
def api_review_detail(request, pk):
    da = _get_data_access(request)
    if request.method == "GET":
        result = da.get_review_by_id(pk)
        if not result:
            return JsonResponse({"error": "Not found"}, status=404)
        return JsonResponse({"review": _serialize_review(result)})

    try:
        body = json.loads(request.body)
        result = da.update_review(pk, body)
        return JsonResponse({"review": _serialize_review(result)})
    except Exception as e:
        logger.exception("API update review %s failed", pk)
        return JsonResponse({"error": str(e)}, status=400)
```

**Step 4: Add API URLs**

Add to `urls.py` urlpatterns:
```python
from . import api_views

    # JSON API
    path("api/solicitations/", api_views.api_solicitations_list, name="api_solicitations_list"),
    path("api/solicitations/<int:pk>/", api_views.api_solicitation_detail, name="api_solicitation_detail"),
    path("api/responses/", api_views.api_responses_list, name="api_responses_list"),
    path("api/responses/<int:pk>/", api_views.api_response_detail, name="api_response_detail"),
    path("api/reviews/", api_views.api_reviews_create, name="api_reviews_create"),
    path("api/reviews/<int:pk>/", api_views.api_review_detail, name="api_review_detail"),
```

**Step 5: Run tests**

Run: `pytest commcare_connect/solicitations/tests/test_api_views.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add commcare_connect/solicitations/api_views.py commcare_connect/solicitations/urls.py commcare_connect/solicitations/tests/test_api_views.py
git commit -m "feat(solicitations): add JSON API views for solicitations, responses, reviews"
```

---

## Task 10: MCP Tools

**Files:**
- Create: `commcare_connect/solicitations/mcp_tools.py`

**Step 1: Implement MCP tools**

```python
# commcare_connect/solicitations/mcp_tools.py
"""MCP tool definitions for solicitations.

These functions call data_access directly and are registered
with the MCP server for AI agent access.
"""

from commcare_connect.solicitations.data_access import SolicitationsDataAccess


def _serialize_solicitation(s):
    return {
        "id": s.pk,
        "title": s.title,
        "description": s.description,
        "solicitation_type": s.solicitation_type,
        "status": s.status,
        "is_public": s.is_public,
        "application_deadline": s.application_deadline.isoformat() if s.application_deadline else None,
        "estimated_scale": s.estimated_scale,
        "program_name": s.program_name,
    }


def _serialize_response(r):
    return {
        "id": r.pk,
        "solicitation_id": r.solicitation_id,
        "llo_entity_id": r.llo_entity_id,
        "llo_entity_name": r.llo_entity_name,
        "status": r.status,
        "submitted_by_name": r.submitted_by_name,
    }


def _serialize_review(r):
    return {
        "id": r.pk,
        "response_id": r.response_id,
        "score": r.score,
        "recommendation": r.recommendation,
        "reviewer_username": r.reviewer_username,
    }


def list_solicitations(access_token, program_id=None, status=None,
                       solicitation_type=None, is_public=None):
    """List solicitations, optionally filtered."""
    da = SolicitationsDataAccess(program_id=program_id, access_token=access_token)
    if is_public:
        results = da.get_public_solicitations(solicitation_type=solicitation_type)
    else:
        results = da.get_solicitations(status=status, solicitation_type=solicitation_type)
    return [_serialize_solicitation(s) for s in results]


def get_solicitation(access_token, solicitation_id):
    """Get a single solicitation by ID."""
    da = SolicitationsDataAccess(access_token=access_token)
    result = da.get_solicitation_by_id(solicitation_id)
    return _serialize_solicitation(result) if result else None


def create_solicitation(access_token, program_id, data):
    """Create a new solicitation."""
    da = SolicitationsDataAccess(program_id=program_id, access_token=access_token)
    result = da.create_solicitation(data)
    return _serialize_solicitation(result)


def update_solicitation(access_token, solicitation_id, data):
    """Update an existing solicitation."""
    da = SolicitationsDataAccess(access_token=access_token)
    result = da.update_solicitation(solicitation_id, data)
    return _serialize_solicitation(result)


def list_responses(access_token, solicitation_id, status=None):
    """List responses for a solicitation."""
    da = SolicitationsDataAccess(access_token=access_token)
    results = da.get_responses_for_solicitation(solicitation_id)
    if status:
        results = [r for r in results if r.status == status]
    return [_serialize_response(r) for r in results]


def get_response(access_token, response_id):
    """Get a single response by ID."""
    da = SolicitationsDataAccess(access_token=access_token)
    result = da.get_response_by_id(response_id)
    return _serialize_response(result) if result else None


def create_response(access_token, solicitation_id, llo_entity_id, data):
    """Create a new response to a solicitation."""
    da = SolicitationsDataAccess(access_token=access_token)
    result = da.create_response(solicitation_id, llo_entity_id, data)
    return _serialize_response(result)


def create_review(access_token, response_id, data):
    """Create a review for a response."""
    da = SolicitationsDataAccess(access_token=access_token)
    result = da.create_review(response_id, data)
    return _serialize_review(result)


def update_review(access_token, review_id, data):
    """Update an existing review."""
    da = SolicitationsDataAccess(access_token=access_token)
    result = da.update_review(review_id, data)
    return _serialize_review(result)
```

**Step 2: Commit**

```bash
git add commcare_connect/solicitations/mcp_tools.py
git commit -m "feat(solicitations): add MCP tool definitions for AI agent access"
```

---

## Task 11: Final URL Assembly & Integration Test

**Files:**
- Modify: `commcare_connect/solicitations/urls.py` (ensure all routes assembled)
- Create: `commcare_connect/solicitations/tests/test_urls.py`

**Step 1: Write the complete urls.py**

```python
# commcare_connect/solicitations/urls.py
from django.urls import path

from . import api_views, views

app_name = "solicitations"

urlpatterns = [
    # Public (no login required)
    path("", views.PublicSolicitationListView.as_view(), name="public_list"),
    path("<int:pk>/", views.PublicSolicitationDetailView.as_view(), name="public_detail"),
    # Manager (login required)
    path("manage/", views.ManageSolicitationsView.as_view(), name="manage_list"),
    path("create/", views.SolicitationCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.SolicitationEditView.as_view(), name="edit"),
    path("<int:pk>/responses/", views.ResponsesListView.as_view(), name="responses_list"),
    # Response (login required)
    path("<int:pk>/respond/", views.RespondView.as_view(), name="respond"),
    path("response/<int:pk>/", views.ResponseDetailView.as_view(), name="response_detail"),
    # Review (manager required)
    path("response/<int:pk>/review/", views.ReviewView.as_view(), name="review"),
    # JSON API
    path("api/solicitations/", api_views.api_solicitations_list, name="api_solicitations_list"),
    path("api/solicitations/<int:pk>/", api_views.api_solicitation_detail, name="api_solicitation_detail"),
    path("api/responses/", api_views.api_responses_list, name="api_responses_list"),
    path("api/responses/<int:pk>/", api_views.api_response_detail, name="api_response_detail"),
    path("api/reviews/", api_views.api_reviews_create, name="api_reviews_create"),
    path("api/reviews/<int:pk>/", api_views.api_review_detail, name="api_review_detail"),
]
```

**Step 2: Write URL resolution tests**

```python
# commcare_connect/solicitations/tests/test_urls.py
import pytest
from django.urls import resolve, reverse


class TestURLResolution:
    def test_public_list(self):
        url = reverse("solicitations:public_list")
        assert url == "/solicitations/"

    def test_public_detail(self):
        url = reverse("solicitations:public_detail", kwargs={"pk": 1})
        assert url == "/solicitations/1/"

    def test_manage_list(self):
        url = reverse("solicitations:manage_list")
        assert url == "/solicitations/manage/"

    def test_create(self):
        url = reverse("solicitations:create")
        assert url == "/solicitations/create/"

    def test_edit(self):
        url = reverse("solicitations:edit", kwargs={"pk": 1})
        assert url == "/solicitations/1/edit/"

    def test_responses_list(self):
        url = reverse("solicitations:responses_list", kwargs={"pk": 1})
        assert url == "/solicitations/1/responses/"

    def test_respond(self):
        url = reverse("solicitations:respond", kwargs={"pk": 1})
        assert url == "/solicitations/1/respond/"

    def test_response_detail(self):
        url = reverse("solicitations:response_detail", kwargs={"pk": 1})
        assert url == "/solicitations/response/1/"

    def test_review(self):
        url = reverse("solicitations:review", kwargs={"pk": 1})
        assert url == "/solicitations/response/1/review/"

    def test_api_solicitations_list(self):
        url = reverse("solicitations:api_solicitations_list")
        assert url == "/solicitations/api/solicitations/"

    def test_api_solicitation_detail(self):
        url = reverse("solicitations:api_solicitation_detail", kwargs={"pk": 1})
        assert url == "/solicitations/api/solicitations/1/"

    def test_api_responses_list(self):
        url = reverse("solicitations:api_responses_list")
        assert url == "/solicitations/api/responses/"

    def test_api_reviews_create(self):
        url = reverse("solicitations:api_reviews_create")
        assert url == "/solicitations/api/reviews/"
```

**Step 3: Run all tests**

Run: `pytest commcare_connect/solicitations/ -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add commcare_connect/solicitations/
git commit -m "feat(solicitations): finalize URL routing and add URL resolution tests"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | App scaffolding & registration | apps.py, settings, urls, middleware |
| 2 | Proxy models | models.py + tests |
| 3 | Data access layer | data_access.py + tests |
| 4 | Forms | forms.py + tests |
| 5 | Public views & templates | views.py, 2 templates |
| 6 | Manager views & templates | views.py, 3 templates |
| 7 | Response views & templates | views.py, 2 templates |
| 8 | Review view & template | views.py, 1 template |
| 9 | JSON API views | api_views.py + tests |
| 10 | MCP tools | mcp_tools.py |
| 11 | Final URL assembly & integration test | urls.py + tests |
