"""Tests for the HTTP /api/export/ synthetic-opportunity API (issue #637).

These exercise the full stack: MCP-PAT auth, is_visible_to authorization, the
production-shaped pagination envelope, single-object endpoints, and the
conditionally-present app_structure endpoint — all against an in-memory fake of
the Drive-backed FixtureStore so no network is involved.
"""
import json
from urllib.parse import urlsplit

import pytest
from rest_framework.test import APIClient

from commcare_connect.labs.synthetic import registry
from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.mcp.models import MCPAccessToken
from commcare_connect.users.models import User


@pytest.fixture(autouse=True)
def _reset_singletons():
    from commcare_connect.labs.integrations.connect import factory

    registry.invalidate_cache()
    factory.reset_fixture_store_singleton()
    yield
    registry.invalidate_cache()
    factory.reset_fixture_store_singleton()


class FakeDrive:
    """Folder-aware in-memory Drive stand-in.

    Built from ``{folder_id: {filename: python_obj}}``. file_id is encoded as
    ``"<folder_id>::<filename>"`` so downloads stay folder-scoped (two opps with
    the same filename serve different content).
    """

    def __init__(self, by_folder):
        self._by_folder = by_folder

    def list_folder(self, folder_id):
        files = self._by_folder.get(folder_id, {})
        return {name: f"{folder_id}::{name}" for name in files}

    def download_file(self, file_id):
        folder_id, name = file_id.split("::", 1)
        return json.dumps(self._by_folder[folder_id][name]).encode("utf-8")


def _install(monkeypatch, by_folder):
    from commcare_connect.labs.integrations.connect import factory

    monkeypatch.setattr(factory, "_build_drive_client", lambda: FakeDrive(by_folder))


def _make_opp(**kw):
    defaults = dict(
        opportunity_id=10001,
        gdrive_folder_id="folder-a",
        enabled=True,
        labs_only=True,
        allowed_domains=[],
    )
    defaults.update(kw)
    opp = SyntheticOpportunity.objects.create(**defaults)
    registry.invalidate_cache()
    return opp


def _user(email="flw@dimagi.com", view=True):
    return User.objects.create(username=email, email=email, view_synthetic_opps=view)


def _client_for(user):
    _, raw = MCPAccessToken.create_token(user, name="test")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


VISITS_URL = "/api/export/opportunity/10001/user_visits/"
DETAIL_URL = "/api/export/opportunity/10001/"
APP_STRUCTURE_URL = "/api/export/opportunity/10001/app_structure/"
OPPS_URL = "/api/export/opportunities/"


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_missing_token_returns_401(monkeypatch):
    _install(monkeypatch, {"folder-a": {"user_visits.json": []}})
    _make_opp()
    resp = APIClient().get(VISITS_URL)
    assert resp.status_code == 401


@pytest.mark.django_db
def test_invalid_token_returns_401(monkeypatch):
    _install(monkeypatch, {"folder-a": {"user_visits.json": []}})
    _make_opp()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    resp = client.get(VISITS_URL)
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Authorization / visibility
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_user_without_flag_gets_404(monkeypatch):
    _install(monkeypatch, {"folder-a": {"user_visits.json": [{"id": 1}]}})
    _make_opp()
    resp = _client_for(_user(view=False)).get(VISITS_URL)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_domain_mismatch_gets_404(monkeypatch):
    _install(monkeypatch, {"folder-a": {"user_visits.json": [{"id": 1}]}})
    _make_opp(allowed_domains=["@dimagi.com"])
    resp = _client_for(_user(email="someone@example.org")).get(VISITS_URL)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_non_labs_only_opp_gets_404(monkeypatch):
    _install(monkeypatch, {"folder-a": {"user_visits.json": [{"id": 1}]}})
    _make_opp(labs_only=False)
    resp = _client_for(_user()).get(VISITS_URL)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_unregistered_opp_gets_404(monkeypatch):
    _install(monkeypatch, {})
    resp = _client_for(_user()).get("/api/export/opportunity/99999/user_visits/")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Paginated endpoints — shape, parity, pagination
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_visible_opp_returns_envelope(monkeypatch):
    rows = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    _install(monkeypatch, {"folder-a": {"user_visits.json": rows}})
    _make_opp()
    resp = _client_for(_user()).get(VISITS_URL)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"results", "next", "count"}
    assert body["count"] == 2
    assert body["next"] is None
    assert body["results"] == rows


@pytest.mark.django_db
def test_shape_parity_rows_preserved_verbatim(monkeypatch):
    rows = [{"id": 1, "nested": {"x": [1, 2]}, "flag": True, "score": 3.5, "blank": None}]
    _install(monkeypatch, {"folder-a": {"user_visits.json": rows}})
    _make_opp()
    body = _client_for(_user()).get(VISITS_URL).json()
    assert body["results"] == rows


@pytest.mark.django_db
def test_empty_fixture_returns_empty_envelope(monkeypatch):
    _install(monkeypatch, {"folder-a": {"user_visits.json": []}})
    _make_opp()
    body = _client_for(_user()).get(VISITS_URL).json()
    assert body == {"results": [], "next": None, "count": 0}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "endpoint,filename",
    [
        ("user_visits", "user_visits.json"),
        ("user_data", "user_data.json"),
        ("completed_works", "completed_works.json"),
        ("completed_module", "completed_module.json"),
    ],
)
def test_all_paginated_endpoints_serve_their_fixture(monkeypatch, endpoint, filename):
    rows = [{"id": 7, "endpoint": endpoint}]
    _install(monkeypatch, {"folder-a": {filename: rows}})
    _make_opp()
    url = f"/api/export/opportunity/10001/{endpoint}/"
    body = _client_for(_user()).get(url).json()
    assert body["results"] == rows
    assert body["count"] == 1


@pytest.mark.django_db
def test_next_paginates_to_exhaustion_no_dupes(monkeypatch):
    rows = [{"id": i} for i in range(7)]
    _install(monkeypatch, {"folder-a": {"user_visits.json": rows}})
    _make_opp()
    client = _client_for(_user())

    seen = []
    url = VISITS_URL + "?page_size=3"
    pages = 0
    while url is not None:
        body = client.get(url).json()
        assert body["count"] == 7
        seen.extend(r["id"] for r in body["results"])
        pages += 1
        if body["next"] is None:
            break
        parts = urlsplit(body["next"])
        url = f"{parts.path}?{parts.query}"

    assert pages == 3  # 3 + 3 + 1
    assert seen == list(range(7))
    assert len(set(seen)) == 7


# --------------------------------------------------------------------------- #
# Single-object endpoints
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_detail_returns_bare_dict(monkeypatch):
    opp = {"id": 10001, "name": "Synthetic Opp", "deliver_app": {"name": "X"}}
    _install(monkeypatch, {"folder-a": {"opportunity.json": opp}})
    _make_opp()
    resp = _client_for(_user()).get(DETAIL_URL)
    assert resp.status_code == 200
    assert resp.json() == opp  # bare dict, not enveloped


@pytest.mark.django_db
def test_opportunities_lists_only_visible(monkeypatch):
    _install(
        monkeypatch,
        {
            "folder-a": {"opportunity.json": {"id": 10001, "name": "Mine"}},
            "folder-b": {"opportunity.json": {"id": 10002, "name": "Theirs"}},
        },
    )
    _make_opp(opportunity_id=10001, gdrive_folder_id="folder-a", allowed_domains=["@dimagi.com"])
    _make_opp(opportunity_id=10002, gdrive_folder_id="folder-b", allowed_domains=["@example.org"])
    body = _client_for(_user(email="me@dimagi.com")).get(OPPS_URL).json()
    assert set(body.keys()) == {"results", "next", "count"}
    ids = [r["id"] for r in body["results"]]
    assert ids == [10001]


# --------------------------------------------------------------------------- #
# app_structure — conditionally present, app_type aware
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_app_structure_present_returns_bare_dict(monkeypatch):
    structure = {"modules": [{"name": "m1"}], "app_id": "abc"}
    _install(monkeypatch, {"folder-a": {"app_structure.json": structure}})
    _make_opp()
    resp = _client_for(_user()).get(APP_STRUCTURE_URL)
    assert resp.status_code == 200
    assert resp.json() == structure


@pytest.mark.django_db
def test_app_structure_absent_returns_404(monkeypatch):
    _install(monkeypatch, {"folder-a": {"user_visits.json": []}})
    _make_opp()
    resp = _client_for(_user()).get(APP_STRUCTURE_URL)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_app_structure_learn_app_type(monkeypatch):
    learn = {"modules": [{"name": "learn-m1"}]}
    _install(monkeypatch, {"folder-a": {"app_structure_learn.json": learn}})
    _make_opp()
    client = _client_for(_user())
    # learn fixture present -> served for app_type=learn
    assert client.get(APP_STRUCTURE_URL + "?app_type=learn").json() == learn
    # default app_type=deliver has no fixture here -> 404
    assert client.get(APP_STRUCTURE_URL).status_code == 404


@pytest.mark.django_db
def test_app_structure_invalid_app_type_returns_404(monkeypatch):
    _install(monkeypatch, {"folder-a": {"app_structure.json": {"x": 1}}})
    _make_opp()
    resp = _client_for(_user()).get(APP_STRUCTURE_URL + "?app_type=bogus")
    assert resp.status_code == 404
