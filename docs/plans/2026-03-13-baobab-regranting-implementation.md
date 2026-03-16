# Baobab Regranting Platform Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the funder_dashboard module (FundRecord CRUD + portfolio views) and enhance solicitations_new with fund linkage, award flow, and `org_id`/`reward_budget` fields.

**Architecture:** Two Labs modules — `funder_dashboard` (new) and `solicitations_new` (enhanced) — both using the LabsRecord API pattern. FundRecord is a new LabsRecord type scoped by `org_id`. Solicitations gain a `fund_id` foreign key. Award flow updates response status and captures `reward_budget` + `org_id` per grantee.

**Tech Stack:** Django 4.2, LabsRecordAPIClient (httpx), Tailwind CSS, Alpine.js, pytest with mocked API client

---

### Task 1: Create funder_dashboard app skeleton

**Files:**
- Create: `commcare_connect/funder_dashboard/__init__.py`
- Create: `commcare_connect/funder_dashboard/apps.py`
- Create: `commcare_connect/funder_dashboard/models.py`
- Create: `commcare_connect/funder_dashboard/data_access.py`
- Create: `commcare_connect/funder_dashboard/views.py`
- Create: `commcare_connect/funder_dashboard/urls.py`
- Create: `commcare_connect/funder_dashboard/forms.py`
- Create: `commcare_connect/funder_dashboard/api_views.py`
- Create: `commcare_connect/funder_dashboard/mcp_tools.py`
- Create: `commcare_connect/funder_dashboard/tests/__init__.py`
- Create: `commcare_connect/funder_dashboard/tests/conftest.py`
- Modify: `config/urls.py`

**Step 1: Create the app directory and files**

`commcare_connect/funder_dashboard/__init__.py` — empty

`commcare_connect/funder_dashboard/apps.py`:
```python
from django.apps import AppConfig


class FunderDashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "commcare_connect.funder_dashboard"
```

`commcare_connect/funder_dashboard/models.py`:
```python
"""
Proxy models for funder_dashboard.

FundRecord extends LocalLabsRecord with typed @property access
to JSON data stored via the LabsRecord API. Cannot be .save()d locally.
"""
from commcare_connect.labs.models import LocalLabsRecord


class FundRecord(LocalLabsRecord):
    """Proxy model for fund records. Scoped by org_id."""

    @property
    def name(self):
        return self.data.get("name", "")

    @property
    def description(self):
        return self.data.get("description", "")

    @property
    def total_budget(self):
        return self.data.get("total_budget")

    @property
    def currency(self):
        return self.data.get("currency", "USD")

    @property
    def org_id(self):
        return self.data.get("org_id", "")

    @property
    def program_ids(self):
        return self.data.get("program_ids", [])

    @property
    def delivery_types(self):
        return self.data.get("delivery_types", [])

    @property
    def status(self):
        return self.data.get("status", "active")
```

`commcare_connect/funder_dashboard/data_access.py`:
```python
"""
Data Access Layer for funder_dashboard.

Uses LabsRecordAPIClient to interact with production LabsRecord API.
FundRecords are scoped by org_id (used as experiment).

Type constant: type="fund"
"""
from django.http import HttpRequest

from commcare_connect.funder_dashboard.models import FundRecord
from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient

FUND_TYPE = "fund"


class FunderDashboardDataAccess:
    """
    Data access layer for funder_dashboard.

    Funds are scoped by org_id (used as experiment).
    """

    def __init__(
        self,
        org_id: str | None = None,
        access_token: str | None = None,
        request: HttpRequest | None = None,
    ):
        self.org_id = org_id

        if request and hasattr(request, "labs_context"):
            labs_context = request.labs_context
            if not org_id and "organization_id" in labs_context:
                self.org_id = str(labs_context["organization_id"])

        if not access_token and request:
            from django.utils import timezone

            labs_oauth = request.session.get("labs_oauth", {})
            expires_at = labs_oauth.get("expires_at", 0)
            if timezone.now().timestamp() < expires_at:
                access_token = labs_oauth.get("access_token")

        if not access_token:
            raise ValueError("OAuth access token required for funder_dashboard data access")

        self.access_token = access_token
        self.labs_api = LabsRecordAPIClient(
            access_token,
            organization_id=int(self.org_id) if self.org_id else None,
        )

    def get_funds(self, status: str | None = None) -> list[FundRecord]:
        kwargs = {}
        if status:
            kwargs["status"] = status
        return self.labs_api.get_records(
            experiment=self.org_id,
            type=FUND_TYPE,
            model_class=FundRecord,
            **kwargs,
        )

    def get_fund_by_id(self, fund_id: int) -> FundRecord | None:
        return self.labs_api.get_record_by_id(
            record_id=fund_id,
            experiment=self.org_id,
            type=FUND_TYPE,
            model_class=FundRecord,
        )

    def create_fund(self, data: dict) -> FundRecord:
        record = self.labs_api.create_record(
            experiment=self.org_id,
            type=FUND_TYPE,
            data=data,
            organization_id=int(self.org_id) if self.org_id else None,
        )
        return FundRecord(record.to_api_dict())

    def update_fund(self, fund_id: int, data: dict) -> FundRecord:
        record = self.labs_api.update_record(
            record_id=fund_id,
            experiment=self.org_id,
            type=FUND_TYPE,
            data=data,
        )
        return FundRecord(record.to_api_dict())
```

`commcare_connect/funder_dashboard/urls.py`:
```python
from django.urls import path

from . import api_views, views

app_name = "funder_dashboard"

urlpatterns = [
    # Dashboard views
    path("", views.PortfolioDashboardView.as_view(), name="portfolio"),
    path("fund/<int:pk>/", views.FundDetailView.as_view(), name="fund_detail"),
    # Fund CRUD
    path("fund/create/", views.FundCreateView.as_view(), name="fund_create"),
    path("fund/<int:pk>/edit/", views.FundEditView.as_view(), name="fund_edit"),
    # JSON API
    path("api/funds/", api_views.api_funds_list, name="api_funds_list"),
    path("api/funds/<int:pk>/", api_views.api_fund_detail, name="api_fund_detail"),
]
```

`commcare_connect/funder_dashboard/views.py` — stub:
```python
from django.views.generic import TemplateView


class PortfolioDashboardView(TemplateView):
    template_name = "funder_dashboard/portfolio.html"


class FundDetailView(TemplateView):
    template_name = "funder_dashboard/fund_detail.html"


class FundCreateView(TemplateView):
    template_name = "funder_dashboard/fund_form.html"


class FundEditView(TemplateView):
    template_name = "funder_dashboard/fund_form.html"
```

`commcare_connect/funder_dashboard/forms.py` — stub:
```python
from django import forms


class FundForm(forms.Form):
    pass
```

`commcare_connect/funder_dashboard/api_views.py` — stub:
```python
from django.http import JsonResponse


def api_funds_list(request):
    return JsonResponse({"funds": []})


def api_fund_detail(request, pk):
    return JsonResponse({"fund": None})
```

`commcare_connect/funder_dashboard/mcp_tools.py` — stub:
```python
"""MCP tools for funder_dashboard. To be implemented."""
```

`commcare_connect/funder_dashboard/tests/__init__.py` — empty

`commcare_connect/funder_dashboard/tests/conftest.py`:
```python
"""Override autouse fixtures from root conftest.

Proxy model tests are pure Python and don't need a database.
"""
import pytest


@pytest.fixture(autouse=True)
def media_storage():
    pass


@pytest.fixture(autouse=True)
def ensure_currency_country_data():
    pass
```

**Step 2: Register URL in config/urls.py**

Add after the `solicitations_new` line:
```python
path("funder/", include("commcare_connect.funder_dashboard.urls", namespace="funder_dashboard")),
```

**Step 3: Verify app loads**

Run: `cd "C:/Users/Jonathan Jackson/Projects/connect-labs" && python -c "from commcare_connect.funder_dashboard.models import FundRecord; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add commcare_connect/funder_dashboard/ config/urls.py
git commit -m "feat: create funder_dashboard app skeleton"
```

---

### Task 2: FundRecord model tests and data access tests

**Files:**
- Create: `commcare_connect/funder_dashboard/tests/test_models.py`
- Create: `commcare_connect/funder_dashboard/tests/test_data_access.py`

**Step 1: Write model tests**

`commcare_connect/funder_dashboard/tests/test_models.py`:
```python
"""Tests for FundRecord proxy model."""
from commcare_connect.funder_dashboard.models import FundRecord


class TestFundRecord:
    def _make_fund(self, **data_overrides):
        data = {
            "name": "Bloomberg Neonatal Fund",
            "description": "Emergency care for newborns",
            "total_budget": 3000000,
            "currency": "USD",
            "org_id": "org_42",
            "program_ids": [1, 2, 3],
            "delivery_types": ["kmc", "transport"],
            "status": "active",
        }
        data.update(data_overrides)
        return FundRecord(
            {"id": 1, "experiment": "org_42", "type": "fund", "data": data, "opportunity_id": 0}
        )

    def test_name(self):
        assert self._make_fund().name == "Bloomberg Neonatal Fund"

    def test_description(self):
        assert self._make_fund().description == "Emergency care for newborns"

    def test_total_budget(self):
        assert self._make_fund().total_budget == 3000000

    def test_currency(self):
        assert self._make_fund().currency == "USD"

    def test_currency_default(self):
        assert self._make_fund(currency=None).currency == "USD"

    def test_org_id(self):
        assert self._make_fund().org_id == "org_42"

    def test_program_ids(self):
        assert self._make_fund().program_ids == [1, 2, 3]

    def test_program_ids_default(self):
        fund = FundRecord({"id": 1, "experiment": "x", "type": "fund", "data": {}, "opportunity_id": 0})
        assert fund.program_ids == []

    def test_delivery_types(self):
        assert self._make_fund().delivery_types == ["kmc", "transport"]

    def test_status(self):
        assert self._make_fund().status == "active"

    def test_status_default(self):
        fund = FundRecord({"id": 1, "experiment": "x", "type": "fund", "data": {}, "opportunity_id": 0})
        assert fund.status == "active"
```

**Step 2: Run model tests to verify they pass**

Run: `cd "C:/Users/Jonathan Jackson/Projects/connect-labs" && pytest commcare_connect/funder_dashboard/tests/test_models.py -v`
Expected: All PASS

**Step 3: Write data access tests**

`commcare_connect/funder_dashboard/tests/test_data_access.py`:
```python
"""Tests for funder_dashboard data access layer.

All tests mock LabsRecordAPIClient to avoid real API calls.
"""
from unittest.mock import MagicMock, patch

import pytest

from commcare_connect.funder_dashboard.data_access import FUND_TYPE, FunderDashboardDataAccess
from commcare_connect.funder_dashboard.models import FundRecord
from commcare_connect.labs.models import LocalLabsRecord


def _make_fund_record(**overrides):
    data = {
        "name": "Test Fund",
        "description": "A test fund",
        "total_budget": 1000000,
        "currency": "USD",
        "org_id": "org_42",
        "program_ids": [1],
        "delivery_types": ["kmc"],
        "status": "active",
    }
    data.update(overrides.pop("data", {}))
    defaults = {
        "id": 1,
        "experiment": "org_42",
        "type": FUND_TYPE,
        "data": data,
        "opportunity_id": 0,
    }
    defaults.update(overrides)
    return FundRecord(defaults)


@pytest.fixture
def mock_api_client():
    with patch("commcare_connect.funder_dashboard.data_access.LabsRecordAPIClient") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def data_access(mock_api_client):
    da = FunderDashboardDataAccess(org_id="42", access_token="test-token")
    da.labs_api = mock_api_client
    return da


class TestConstructor:
    def test_requires_access_token(self):
        with pytest.raises(ValueError, match="OAuth access token required"):
            FunderDashboardDataAccess(org_id="42")

    @patch("commcare_connect.funder_dashboard.data_access.LabsRecordAPIClient")
    def test_stores_org_id(self, MockClient):
        da = FunderDashboardDataAccess(org_id="42", access_token="tok")
        assert da.org_id == "42"

    @patch("commcare_connect.funder_dashboard.data_access.LabsRecordAPIClient")
    def test_creates_api_client_with_token(self, MockClient):
        FunderDashboardDataAccess(org_id="42", access_token="tok")
        MockClient.assert_called_once_with("tok", organization_id=42)


class TestGetFunds:
    def test_returns_fund_records(self, data_access, mock_api_client):
        records = [_make_fund_record(id=1), _make_fund_record(id=2)]
        mock_api_client.get_records.return_value = records
        result = data_access.get_funds()
        assert result == records
        mock_api_client.get_records.assert_called_once_with(
            experiment="42", type=FUND_TYPE, model_class=FundRecord,
        )

    def test_filters_by_status(self, data_access, mock_api_client):
        mock_api_client.get_records.return_value = []
        data_access.get_funds(status="active")
        mock_api_client.get_records.assert_called_once_with(
            experiment="42", type=FUND_TYPE, model_class=FundRecord, status="active",
        )


class TestGetFundById:
    def test_returns_record(self, data_access, mock_api_client):
        record = _make_fund_record(id=5)
        mock_api_client.get_record_by_id.return_value = record
        result = data_access.get_fund_by_id(5)
        assert result is record
        mock_api_client.get_record_by_id.assert_called_once_with(
            record_id=5, experiment="42", type=FUND_TYPE, model_class=FundRecord,
        )

    def test_returns_none_when_not_found(self, data_access, mock_api_client):
        mock_api_client.get_record_by_id.return_value = None
        assert data_access.get_fund_by_id(999) is None


class TestCreateFund:
    def test_creates_record(self, data_access, mock_api_client):
        input_data = {"name": "New Fund", "status": "active"}
        api_return = LocalLabsRecord(
            {"id": 100, "experiment": "42", "type": FUND_TYPE, "data": input_data, "opportunity_id": 0}
        )
        mock_api_client.create_record.return_value = api_return
        result = data_access.create_fund(input_data)
        assert isinstance(result, FundRecord)
        assert result.id == 100
        mock_api_client.create_record.assert_called_once_with(
            experiment="42", type=FUND_TYPE, data=input_data, organization_id=42,
        )


class TestUpdateFund:
    def test_updates_record(self, data_access, mock_api_client):
        updated_data = {"name": "Updated Fund", "status": "closed"}
        api_return = LocalLabsRecord(
            {"id": 5, "experiment": "42", "type": FUND_TYPE, "data": updated_data, "opportunity_id": 0}
        )
        mock_api_client.update_record.return_value = api_return
        result = data_access.update_fund(5, updated_data)
        assert isinstance(result, FundRecord)
        assert result.id == 5
        mock_api_client.update_record.assert_called_once_with(
            record_id=5, experiment="42", type=FUND_TYPE, data=updated_data,
        )
```

**Step 4: Run data access tests**

Run: `cd "C:/Users/Jonathan Jackson/Projects/connect-labs" && pytest commcare_connect/funder_dashboard/tests/test_data_access.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add commcare_connect/funder_dashboard/tests/
git commit -m "test: add FundRecord model and data access tests"
```

---

### Task 3: FundForm and Fund CRUD views

**Files:**
- Modify: `commcare_connect/funder_dashboard/forms.py`
- Modify: `commcare_connect/funder_dashboard/views.py`
- Create: `commcare_connect/templates/funder_dashboard/fund_form.html`
- Create: `commcare_connect/templates/funder_dashboard/portfolio.html`
- Create: `commcare_connect/templates/funder_dashboard/fund_detail.html`

**Step 1: Implement FundForm**

Replace `commcare_connect/funder_dashboard/forms.py`:
```python
"""Forms for funder_dashboard. Produces dicts via to_data_dict() for the data access layer."""
from django import forms

STATUS_CHOICES = [
    ("active", "Active"),
    ("closed", "Closed"),
]


class FundForm(forms.Form):
    name = forms.CharField(
        max_length=255,
        required=True,
        label="Fund Name",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Bloomberg Neonatal Emergency Care Fund"}),
    )
    description = forms.CharField(
        required=False,
        label="Description",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Describe the fund..."}),
    )
    total_budget = forms.IntegerField(
        required=False,
        label="Total Budget (smallest currency unit)",
        widget=forms.NumberInput(attrs={"placeholder": "e.g. 3000000"}),
    )
    currency = forms.CharField(
        max_length=3,
        required=False,
        initial="USD",
        label="Currency Code",
        widget=forms.TextInput(attrs={"placeholder": "USD"}),
    )
    status = forms.ChoiceField(
        choices=STATUS_CHOICES,
        required=True,
        label="Status",
    )
    program_ids_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        label="Program IDs (JSON)",
    )
    delivery_types_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        label="Delivery Types (JSON)",
    )

    def to_data_dict(self) -> dict:
        import json

        data = {
            "name": self.cleaned_data["name"],
            "description": self.cleaned_data.get("description", ""),
            "total_budget": self.cleaned_data.get("total_budget"),
            "currency": self.cleaned_data.get("currency", "USD"),
            "status": self.cleaned_data["status"],
        }
        raw_programs = self.cleaned_data.get("program_ids_json", "")
        if raw_programs:
            try:
                data["program_ids"] = json.loads(raw_programs)
            except (json.JSONDecodeError, TypeError):
                data["program_ids"] = []
        else:
            data["program_ids"] = []

        raw_types = self.cleaned_data.get("delivery_types_json", "")
        if raw_types:
            try:
                data["delivery_types"] = json.loads(raw_types)
            except (json.JSONDecodeError, TypeError):
                data["delivery_types"] = []
        else:
            data["delivery_types"] = []

        return data
```

**Step 2: Implement views**

Replace `commcare_connect/funder_dashboard/views.py`:
```python
import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404
from django.shortcuts import redirect
from django.views.generic import TemplateView

from commcare_connect.funder_dashboard.data_access import FunderDashboardDataAccess
from commcare_connect.funder_dashboard.forms import FundForm

logger = logging.getLogger(__name__)


class LabsLoginRequiredMixin(LoginRequiredMixin):
    login_url = "/labs/login/"


class ManagerRequiredMixin(LabsLoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated


def _has_org_context(request):
    labs_context = getattr(request, "labs_context", {})
    return bool(labs_context.get("organization_id"))


def _get_data_access(request):
    return FunderDashboardDataAccess(request=request)


class PortfolioDashboardView(ManagerRequiredMixin, TemplateView):
    template_name = "funder_dashboard/portfolio.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["has_context"] = _has_org_context(self.request)
        if not ctx["has_context"]:
            ctx["funds"] = []
            return ctx
        try:
            da = _get_data_access(self.request)
            ctx["funds"] = da.get_funds()
        except Exception:
            logger.exception("Failed to load funds for portfolio")
            ctx["funds"] = []
        return ctx


class FundDetailView(ManagerRequiredMixin, TemplateView):
    template_name = "funder_dashboard/fund_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        try:
            da = _get_data_access(self.request)
            fund = da.get_fund_by_id(pk)
            if not fund:
                raise Http404("Fund not found")
            ctx["fund"] = fund
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load fund %s", pk)
            raise Http404("Fund not found")
        return ctx


class FundCreateView(ManagerRequiredMixin, TemplateView):
    template_name = "funder_dashboard/fund_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["has_context"] = _has_org_context(self.request)
        ctx["form"] = FundForm()
        ctx["is_create"] = True
        return ctx

    def post(self, request, *args, **kwargs):
        if not _has_org_context(request):
            ctx = self.get_context_data(**kwargs)
            ctx["error"] = "Please select an organization from the context selector."
            return self.render_to_response(ctx)

        form = FundForm(request.POST)
        if form.is_valid():
            data = form.to_data_dict()
            data["org_id"] = str(request.labs_context.get("organization_id", ""))
            try:
                da = _get_data_access(request)
                da.create_fund(data)
                return redirect("funder_dashboard:portfolio")
            except Exception:
                logger.exception("Failed to create fund")
                ctx = self.get_context_data(**kwargs)
                ctx["form"] = form
                ctx["error"] = "Failed to create fund. Please try again."
                return self.render_to_response(ctx)
        else:
            ctx = self.get_context_data(**kwargs)
            ctx["form"] = form
            return self.render_to_response(ctx)


class FundEditView(ManagerRequiredMixin, TemplateView):
    template_name = "funder_dashboard/fund_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["has_context"] = _has_org_context(self.request)
        pk = kwargs["pk"]
        try:
            da = _get_data_access(self.request)
            fund = da.get_fund_by_id(pk)
            if not fund:
                raise Http404("Fund not found")
            ctx["fund"] = fund
            initial = {
                "name": fund.name,
                "description": fund.description,
                "total_budget": fund.total_budget,
                "currency": fund.currency,
                "status": fund.status,
                "program_ids_json": json.dumps(fund.program_ids),
                "delivery_types_json": json.dumps(fund.delivery_types),
            }
            ctx["form"] = FundForm(initial=initial)
            ctx["is_create"] = False
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load fund %s for editing", pk)
            raise Http404("Fund not found")
        return ctx

    def post(self, request, *args, **kwargs):
        pk = kwargs["pk"]
        form = FundForm(request.POST)
        if form.is_valid():
            data = form.to_data_dict()
            try:
                da = _get_data_access(request)
                da.update_fund(pk, data)
                return redirect("funder_dashboard:portfolio")
            except Exception:
                logger.exception("Failed to update fund %s", pk)
                ctx = self.get_context_data(**kwargs)
                ctx["form"] = form
                ctx["error"] = "Failed to update fund. Please try again."
                return self.render_to_response(ctx)
        else:
            ctx = self.get_context_data(**kwargs)
            ctx["form"] = form
            return self.render_to_response(ctx)
```

**Step 3: Create templates**

`commcare_connect/templates/funder_dashboard/portfolio.html`:
```html
{% extends "base.html" %}

{% block content %}
<div class="max-w-screen-xl mx-auto px-4 py-6">

    <div class="bg-white rounded-lg shadow-sm p-6 mb-6">
        <div class="flex items-center justify-between">
            <div>
                <h1 class="text-2xl font-semibold text-brand-deep-purple mb-2">Funder Dashboard</h1>
                <p class="text-sm text-gray-600">Manage your funds, programs, and grantees.</p>
            </div>
            <a href="{% url 'funder_dashboard:fund_create' %}"
               class="inline-flex items-center px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 transition">
                <i class="fa-solid fa-plus mr-2"></i>
                Create Fund
            </a>
        </div>
    </div>

    {% if not has_context %}
        <div class="bg-blue-50 border border-blue-200 rounded-lg shadow-sm p-12 text-center">
            <i class="fa-solid fa-building text-5xl text-blue-400 mb-4"></i>
            <h3 class="text-xl font-semibold text-blue-900 mb-2">No organization selected</h3>
            <p class="text-blue-700 mb-6">Please select an organization from the context selector above.</p>
        </div>
    {% elif funds %}
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {% for fund in funds %}
            <a href="{% url 'funder_dashboard:fund_detail' pk=fund.pk %}" class="block">
                <div class="bg-white rounded-lg shadow-sm p-6 hover:shadow-md transition border border-gray-200">
                    <div class="flex items-center justify-between mb-4">
                        <h3 class="text-lg font-semibold text-gray-900">{{ fund.name }}</h3>
                        {% if fund.status == 'active' %}
                            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">Active</span>
                        {% else %}
                            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800">Closed</span>
                        {% endif %}
                    </div>
                    <p class="text-sm text-gray-600 mb-4">{{ fund.description|truncatewords:20 }}</p>
                    <div class="flex items-center justify-between text-sm text-gray-500">
                        {% if fund.total_budget %}
                            <span class="font-medium text-gray-900">${{ fund.total_budget|floatformat:0 }}</span>
                        {% endif %}
                        <span>{{ fund.program_ids|length }} program{{ fund.program_ids|length|pluralize }}</span>
                    </div>
                </div>
            </a>
            {% endfor %}
        </div>
    {% else %}
        <div class="bg-white rounded-lg shadow-sm p-12 text-center">
            <i class="fa-solid fa-coins text-5xl text-gray-300 mb-4"></i>
            <h3 class="text-xl font-semibold text-gray-900 mb-2">No funds yet</h3>
            <p class="text-gray-600 mb-6">Create your first fund to get started.</p>
            <a href="{% url 'funder_dashboard:fund_create' %}"
               class="inline-flex items-center px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 transition">
                <i class="fa-solid fa-plus mr-2"></i>
                Create Fund
            </a>
        </div>
    {% endif %}

</div>
{% endblock %}
```

`commcare_connect/templates/funder_dashboard/fund_detail.html`:
```html
{% extends "base.html" %}

{% block content %}
<div class="max-w-screen-xl mx-auto px-4 py-6">

    <div class="bg-white rounded-lg shadow-sm p-6 mb-6">
        <div class="flex items-center justify-between">
            <div>
                <a href="{% url 'funder_dashboard:portfolio' %}" class="text-sm text-indigo-600 hover:text-indigo-800 mb-2 inline-block">&larr; Back to Portfolio</a>
                <h1 class="text-2xl font-semibold text-brand-deep-purple mb-2">{{ fund.name }}</h1>
                <p class="text-sm text-gray-600">{{ fund.description }}</p>
            </div>
            <div class="flex gap-3">
                <a href="{% url 'funder_dashboard:fund_edit' pk=fund.pk %}"
                   class="inline-flex items-center px-4 py-2 bg-white border border-gray-300 text-sm font-medium rounded-lg hover:bg-gray-50 transition">
                    <i class="fa-solid fa-edit mr-2"></i> Edit Fund
                </a>
            </div>
        </div>
    </div>

    <!-- Fund KPIs -->
    <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <div class="bg-white rounded-lg shadow-sm p-4 border border-gray-200">
            <div class="text-sm text-gray-500">Total Budget</div>
            <div class="text-2xl font-bold text-gray-900">
                {% if fund.total_budget %}${{ fund.total_budget|floatformat:0 }}{% else %}&mdash;{% endif %}
            </div>
        </div>
        <div class="bg-white rounded-lg shadow-sm p-4 border border-gray-200">
            <div class="text-sm text-gray-500">Programs</div>
            <div class="text-2xl font-bold text-gray-900">{{ fund.program_ids|length }}</div>
        </div>
        <div class="bg-white rounded-lg shadow-sm p-4 border border-gray-200">
            <div class="text-sm text-gray-500">Currency</div>
            <div class="text-2xl font-bold text-gray-900">{{ fund.currency }}</div>
        </div>
        <div class="bg-white rounded-lg shadow-sm p-4 border border-gray-200">
            <div class="text-sm text-gray-500">Status</div>
            <div class="text-2xl font-bold {% if fund.status == 'active' %}text-green-600{% else %}text-gray-600{% endif %}">{{ fund.status|title }}</div>
        </div>
    </div>

    <!-- Programs table (placeholder — will be populated from Connect API in future) -->
    <div class="bg-white rounded-lg shadow-sm p-6 mb-6">
        <h2 class="text-lg font-semibold text-gray-900 mb-4">Programs</h2>
        {% if fund.program_ids %}
            <p class="text-sm text-gray-600">Program IDs: {{ fund.program_ids|join:", " }}</p>
            <p class="text-xs text-gray-400 mt-2">Full program details will be loaded from Connect API.</p>
        {% else %}
            <p class="text-sm text-gray-500">No programs linked to this fund yet.</p>
        {% endif %}
    </div>

    <!-- Delivery Types -->
    {% if fund.delivery_types %}
    <div class="bg-white rounded-lg shadow-sm p-6">
        <h2 class="text-lg font-semibold text-gray-900 mb-4">Delivery Types</h2>
        <div class="flex flex-wrap gap-2">
            {% for dt in fund.delivery_types %}
                <span class="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-indigo-50 text-indigo-700">{{ dt }}</span>
            {% endfor %}
        </div>
    </div>
    {% endif %}

</div>
{% endblock %}
```

`commcare_connect/templates/funder_dashboard/fund_form.html`:
```html
{% extends "base.html" %}

{% block content %}
<div class="max-w-screen-lg mx-auto px-4 py-6">

    <div class="bg-white rounded-lg shadow-sm p-6">
        <a href="{% url 'funder_dashboard:portfolio' %}" class="text-sm text-indigo-600 hover:text-indigo-800 mb-4 inline-block">&larr; Back to Portfolio</a>
        <h1 class="text-2xl font-semibold text-brand-deep-purple mb-6">
            {% if is_create %}Create Fund{% else %}Edit Fund{% endif %}
        </h1>

        {% if error %}
            <div class="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
                <p class="text-sm text-red-700">{{ error }}</p>
            </div>
        {% endif %}

        {% if not has_context %}
            <div class="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
                <p class="text-sm text-blue-700">Please select an organization from the context selector above.</p>
            </div>
        {% else %}
            <form method="post" class="space-y-6">
                {% csrf_token %}

                {% for field in form %}
                    {% if not field.is_hidden %}
                    <div>
                        <label for="{{ field.id_for_label }}" class="block text-sm font-medium text-gray-700 mb-1">
                            {{ field.label }}
                        </label>
                        {{ field }}
                        {% if field.help_text %}
                            <p class="mt-1 text-xs text-gray-500">{{ field.help_text }}</p>
                        {% endif %}
                        {% for error in field.errors %}
                            <p class="mt-1 text-xs text-red-600">{{ error }}</p>
                        {% endfor %}
                    </div>
                    {% else %}
                        {{ field }}
                    {% endif %}
                {% endfor %}

                <div class="flex justify-end gap-3 pt-4">
                    <a href="{% url 'funder_dashboard:portfolio' %}"
                       class="px-4 py-2 bg-white border border-gray-300 text-sm font-medium rounded-lg hover:bg-gray-50">
                        Cancel
                    </a>
                    <button type="submit"
                            class="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700">
                        {% if is_create %}Create Fund{% else %}Save Changes{% endif %}
                    </button>
                </div>
            </form>
        {% endif %}
    </div>

</div>
{% endblock %}
```

**Step 4: Run all funder_dashboard tests**

Run: `cd "C:/Users/Jonathan Jackson/Projects/connect-labs" && pytest commcare_connect/funder_dashboard/ -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add commcare_connect/funder_dashboard/forms.py commcare_connect/funder_dashboard/views.py commcare_connect/templates/funder_dashboard/
git commit -m "feat: implement Fund CRUD views, forms, and templates"
```

---

### Task 4: Fund API endpoints

**Files:**
- Modify: `commcare_connect/funder_dashboard/api_views.py`
- Create: `commcare_connect/funder_dashboard/tests/test_api_views.py`

**Step 1: Write API view tests**

`commcare_connect/funder_dashboard/tests/test_api_views.py`:
```python
"""Tests for funder_dashboard API views."""
import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from commcare_connect.funder_dashboard.api_views import api_fund_detail, api_funds_list


def _mock_request(method="GET", body=None, user_authenticated=True):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get("/funder/api/funds/")
    else:
        request = factory.post(
            "/funder/api/funds/",
            data=json.dumps(body or {}),
            content_type="application/json",
        )
    request.user = MagicMock(is_authenticated=user_authenticated, username="testuser")
    request.labs_context = {"organization_id": 42}
    request.session = {"labs_oauth": {"access_token": "tok", "expires_at": 9999999999}}
    return request


@patch("commcare_connect.funder_dashboard.api_views.FunderDashboardDataAccess")
class TestApiFundsList:
    def test_get_returns_funds(self, MockDA):
        mock_fund = MagicMock()
        mock_fund.id = 1
        mock_fund.data = {"name": "Test Fund", "status": "active"}
        MockDA.return_value.get_funds.return_value = [mock_fund]

        request = _mock_request("GET")
        response = api_funds_list(request)
        data = json.loads(response.content)

        assert response.status_code == 200
        assert len(data["funds"]) == 1

    def test_post_creates_fund(self, MockDA):
        mock_record = MagicMock()
        mock_record.id = 100
        mock_record.data = {"name": "New Fund"}
        MockDA.return_value.create_fund.return_value = mock_record

        request = _mock_request("POST", body={"name": "New Fund", "status": "active"})
        response = api_funds_list(request)

        assert response.status_code == 201
```

**Step 2: Implement API views**

Replace `commcare_connect/funder_dashboard/api_views.py`:
```python
"""JSON API endpoints for funder_dashboard."""
import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from commcare_connect.funder_dashboard.data_access import FunderDashboardDataAccess

logger = logging.getLogger(__name__)


def _serialize_fund(fund):
    return {"id": fund.id, "data": fund.data}


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_funds_list(request):
    try:
        da = FunderDashboardDataAccess(request=request)
    except ValueError:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    if request.method == "GET":
        status = request.GET.get("status")
        funds = da.get_funds(status=status)
        return JsonResponse({"funds": [_serialize_fund(f) for f in funds]})
    else:
        data = json.loads(request.body)
        org_id = str(request.labs_context.get("organization_id", ""))
        data["org_id"] = org_id
        fund = da.create_fund(data)
        return JsonResponse({"fund": _serialize_fund(fund)}, status=201)


@csrf_exempt
@require_http_methods(["GET", "PUT"])
def api_fund_detail(request, pk):
    try:
        da = FunderDashboardDataAccess(request=request)
    except ValueError:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    if request.method == "GET":
        fund = da.get_fund_by_id(pk)
        if not fund:
            return JsonResponse({"error": "Fund not found"}, status=404)
        return JsonResponse({"fund": _serialize_fund(fund)})
    else:
        data = json.loads(request.body)
        fund = da.update_fund(pk, data)
        return JsonResponse({"fund": _serialize_fund(fund)})
```

**Step 3: Run API tests**

Run: `cd "C:/Users/Jonathan Jackson/Projects/connect-labs" && pytest commcare_connect/funder_dashboard/tests/test_api_views.py -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add commcare_connect/funder_dashboard/api_views.py commcare_connect/funder_dashboard/tests/test_api_views.py
git commit -m "feat: implement fund API endpoints with tests"
```

---

### Task 5: Enhance solicitations_new — add fund_id, org_id, reward_budget fields

**Files:**
- Modify: `commcare_connect/solicitations_new/models.py`
- Modify: `commcare_connect/solicitations_new/forms.py`
- Modify: `commcare_connect/solicitations_new/tests/test_models.py`

**Step 1: Add fund_id to SolicitationRecord**

In `commcare_connect/solicitations_new/models.py`, add to `SolicitationRecord`:
```python
@property
def fund_id(self):
    return self.data.get("fund_id")
```

**Step 2: Add org_id, org_name to ResponseRecord**

In `commcare_connect/solicitations_new/models.py`, add to `ResponseRecord`:
```python
@property
def org_id(self):
    return self.data.get("org_id", "")

@property
def org_name(self):
    return self.data.get("org_name", "")
```

**Step 3: Add reward_budget to ReviewRecord**

In `commcare_connect/solicitations_new/models.py`, add to `ReviewRecord`:
```python
@property
def reward_budget(self):
    return self.data.get("reward_budget")
```

**Step 4: Add "awarded" status to forms.py**

In `commcare_connect/solicitations_new/forms.py`, update `STATUS_CHOICES`:
```python
STATUS_CHOICES = [
    ("draft", "Draft"),
    ("active", "Active"),
    ("closed", "Closed"),
    ("awarded", "Awarded"),
]
```

Add `reward_budget` to `ReviewForm`:
```python
reward_budget = forms.IntegerField(
    label="Award Budget",
    required=False,
    help_text="Budget to award this grantee (smallest currency unit)",
    widget=forms.NumberInput(attrs={"placeholder": "e.g. 500000"}),
)
```

**Step 5: Add model tests for new fields**

In `commcare_connect/solicitations_new/tests/test_models.py`, add:
```python
class TestSolicitationRecordFundId:
    def test_fund_id(self):
        record = SolicitationRecord(
            {"id": 1, "experiment": "p", "type": "solicitation_new",
             "data": {"fund_id": 42}, "opportunity_id": 0}
        )
        assert record.fund_id == 42

    def test_fund_id_default_none(self):
        record = SolicitationRecord(
            {"id": 1, "experiment": "p", "type": "solicitation_new",
             "data": {}, "opportunity_id": 0}
        )
        assert record.fund_id is None


class TestResponseRecordOrgFields:
    def test_org_id(self):
        record = ResponseRecord(
            {"id": 1, "experiment": "e", "type": "solicitation_new_response",
             "data": {"org_id": "org_42"}, "opportunity_id": 0}
        )
        assert record.org_id == "org_42"

    def test_org_name(self):
        record = ResponseRecord(
            {"id": 1, "experiment": "e", "type": "solicitation_new_response",
             "data": {"org_name": "Test Org"}, "opportunity_id": 0}
        )
        assert record.org_name == "Test Org"


class TestReviewRecordRewardBudget:
    def test_reward_budget(self):
        record = ReviewRecord(
            {"id": 1, "experiment": "e", "type": "solicitation_new_review",
             "data": {"reward_budget": 500000}, "opportunity_id": 0}
        )
        assert record.reward_budget == 500000

    def test_reward_budget_default_none(self):
        record = ReviewRecord(
            {"id": 1, "experiment": "e", "type": "solicitation_new_review",
             "data": {}, "opportunity_id": 0}
        )
        assert record.reward_budget is None
```

**Step 6: Run tests**

Run: `cd "C:/Users/Jonathan Jackson/Projects/connect-labs" && pytest commcare_connect/solicitations_new/tests/ -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add commcare_connect/solicitations_new/
git commit -m "feat: add fund_id, org_id, org_name, reward_budget fields to solicitation models"
```

---

### Task 6: Award flow — view and data access

**Files:**
- Modify: `commcare_connect/solicitations_new/data_access.py`
- Modify: `commcare_connect/solicitations_new/views.py`
- Modify: `commcare_connect/solicitations_new/urls.py`
- Create: `commcare_connect/templates/solicitations_new/award.html`

**Step 1: Add award method to data access**

In `commcare_connect/solicitations_new/data_access.py`, add after `update_response`:
```python
def award_response(self, response_id: int, reward_budget: int, org_id: str) -> ResponseRecord:
    """
    Mark a response as awarded with budget and org_id.

    Args:
        response_id: ID of the response to award
        reward_budget: Budget allocated to this grantee
        org_id: Connect org ID of the awarded grantee

    Returns:
        Updated ResponseRecord instance
    """
    current = self.get_response_by_id(response_id)
    if not current:
        raise ValueError(f"Response {response_id} not found")

    data = dict(current.data)
    data["status"] = "awarded"
    data["reward_budget"] = reward_budget
    data["org_id"] = org_id
    return self.update_response(response_id, data)
```

**Step 2: Add AwardView to views.py**

In `commcare_connect/solicitations_new/views.py`, add:
```python
class AwardView(ManagerRequiredMixin, TemplateView):
    """Award a response — mark as awarded with budget and org_id."""

    template_name = "solicitations_new/award.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        try:
            da = _get_data_access(self.request)
            response = da.get_response_by_id(pk)
            if not response:
                raise Http404("Response not found")
            ctx["response"] = response

            solicitation = da.get_solicitation_by_id(response.solicitation_id)
            ctx["solicitation"] = solicitation
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load response %s for award", pk)
            raise Http404("Response not found")
        return ctx

    def post(self, request, *args, **kwargs):
        pk = kwargs["pk"]
        try:
            da = _get_data_access(request)
            reward_budget = int(request.POST.get("reward_budget", 0))
            org_id = request.POST.get("org_id", "")
            da.award_response(pk, reward_budget=reward_budget, org_id=org_id)
            # Redirect back to the responses list for the parent solicitation
            response = da.get_response_by_id(pk)
            if response:
                return redirect("solicitations_new:responses_list", pk=response.solicitation_id)
            return redirect("solicitations_new:manage_list")
        except Exception:
            logger.exception("Failed to award response %s", pk)
            ctx = self.get_context_data(**kwargs)
            ctx["error"] = "Failed to award response. Please try again."
            return self.render_to_response(ctx)
```

**Step 3: Add URL**

In `commcare_connect/solicitations_new/urls.py`, add before JSON API paths:
```python
# Award
path("response/<int:pk>/award/", views.AwardView.as_view(), name="award"),
```

**Step 4: Create award template**

`commcare_connect/templates/solicitations_new/award.html`:
```html
{% extends "base.html" %}

{% block content %}
<div class="max-w-screen-lg mx-auto px-4 py-6">

    <div class="bg-white rounded-lg shadow-sm p-6">
        <a href="{% url 'solicitations_new:response_detail' pk=response.pk %}" class="text-sm text-indigo-600 hover:text-indigo-800 mb-4 inline-block">&larr; Back to Response</a>
        <h1 class="text-2xl font-semibold text-brand-deep-purple mb-2">Award Response</h1>
        <p class="text-sm text-gray-600 mb-6">
            Awarding <strong>{{ response.submitted_by_name }}</strong>
            {% if solicitation %}for <strong>{{ solicitation.title }}</strong>{% endif %}
        </p>

        {% if error %}
            <div class="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
                <p class="text-sm text-red-700">{{ error }}</p>
            </div>
        {% endif %}

        <form method="post" class="space-y-6">
            {% csrf_token %}

            <div>
                <label for="org_id" class="block text-sm font-medium text-gray-700 mb-1">Organization ID</label>
                <input type="text" name="org_id" id="org_id" required
                       value="{{ response.org_id }}"
                       placeholder="Connect org ID for the grantee"
                       class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm">
                <p class="mt-1 text-xs text-gray-500">The Connect organization ID of the awarded grantee.</p>
            </div>

            <div>
                <label for="reward_budget" class="block text-sm font-medium text-gray-700 mb-1">Award Budget</label>
                <input type="number" name="reward_budget" id="reward_budget" required
                       placeholder="e.g. 500000"
                       class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm">
                <p class="mt-1 text-xs text-gray-500">Budget allocated to this grantee (smallest currency unit).</p>
            </div>

            <div class="flex justify-end gap-3 pt-4">
                <a href="{% url 'solicitations_new:response_detail' pk=response.pk %}"
                   class="px-4 py-2 bg-white border border-gray-300 text-sm font-medium rounded-lg hover:bg-gray-50">
                    Cancel
                </a>
                <button type="submit"
                        class="px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700">
                    Award Grantee
                </button>
            </div>
        </form>
    </div>

</div>
{% endblock %}
```

**Step 5: Add award_response test to test_data_access.py**

In `commcare_connect/solicitations_new/tests/test_data_access.py`, add:
```python
class TestAwardResponse:
    def test_awards_response(self, data_access, mock_api_client):
        current_record = _make_response_record(id=10)
        mock_api_client.get_record_by_id.return_value = current_record

        updated_data = dict(current_record.data)
        updated_data["status"] = "awarded"
        updated_data["reward_budget"] = 500000
        updated_data["org_id"] = "org_99"
        api_return = LocalLabsRecord(
            {"id": 10, "experiment": "llo_entity_123", "type": RESPONSE_TYPE,
             "data": updated_data, "opportunity_id": 0}
        )
        mock_api_client.update_record.return_value = api_return

        result = data_access.award_response(10, reward_budget=500000, org_id="org_99")

        assert isinstance(result, ResponseRecord)
        mock_api_client.update_record.assert_called_once()
        call_data = mock_api_client.update_record.call_args[1]["data"]
        assert call_data["status"] == "awarded"
        assert call_data["reward_budget"] == 500000
        assert call_data["org_id"] == "org_99"

    def test_raises_for_missing_response(self, data_access, mock_api_client):
        mock_api_client.get_record_by_id.return_value = None
        with pytest.raises(ValueError, match="Response 999 not found"):
            data_access.award_response(999, reward_budget=500000, org_id="org_99")
```

**Step 6: Run all solicitation tests**

Run: `cd "C:/Users/Jonathan Jackson/Projects/connect-labs" && pytest commcare_connect/solicitations_new/tests/ -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add commcare_connect/solicitations_new/ commcare_connect/templates/solicitations_new/award.html
git commit -m "feat: add award flow — view, data access, template, and tests"
```

---

### Task 7: Add "Award" button to response detail and responses list

**Files:**
- Modify: `commcare_connect/templates/solicitations_new/response_detail.html`
- Modify: `commcare_connect/templates/solicitations_new/responses_list.html`

**Step 1: Read current templates and add award buttons**

Add an "Award" button to `response_detail.html` (visible when status is not "awarded"):
```html
{% if response.status != 'awarded' %}
    <a href="{% url 'solicitations_new:award' pk=response.pk %}"
       class="inline-flex items-center px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 transition">
        <i class="fa-solid fa-trophy mr-2"></i> Award
    </a>
{% else %}
    <span class="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-green-100 text-green-800">
        <i class="fa-solid fa-check mr-1"></i> Awarded
    </span>
{% endif %}
```

Add "Award" link to each row in `responses_list.html` actions column.

**Step 2: Read the actual templates, make the edits**

(Implementation agent should read the current templates and add the buttons in the appropriate location.)

**Step 3: Commit**

```bash
git add commcare_connect/templates/solicitations_new/
git commit -m "feat: add Award button to response detail and responses list"
```

---

### Task 8: Run full test suite and lint

**Step 1: Run all tests**

Run: `cd "C:/Users/Jonathan Jackson/Projects/connect-labs" && pytest commcare_connect/funder_dashboard/ commcare_connect/solicitations_new/tests/ -v`
Expected: All PASS

**Step 2: Run linter**

Run: `cd "C:/Users/Jonathan Jackson/Projects/connect-labs" && pre-commit run -a`
Expected: All checks pass (fix any formatting issues)

**Step 3: Commit any lint fixes**

```bash
git add -u
git commit -m "style: fix linting issues"
```

---

## Summary

| Task | What | Estimated Steps |
|------|------|----------------|
| 1 | funder_dashboard app skeleton + URL registration | 4 |
| 2 | FundRecord model + data access tests | 5 |
| 3 | FundForm + Fund CRUD views + templates | 5 |
| 4 | Fund API endpoints + tests | 4 |
| 5 | solicitations_new field enhancements (fund_id, org_id, reward_budget) | 7 |
| 6 | Award flow (view, data access, template, tests) | 7 |
| 7 | Award buttons in existing templates | 3 |
| 8 | Full test suite + lint | 3 |

**Total: 8 tasks, ~38 steps**

All tasks follow existing Labs patterns (proxy models, LabsRecordAPIClient, mocked tests). No production Connect changes required.
