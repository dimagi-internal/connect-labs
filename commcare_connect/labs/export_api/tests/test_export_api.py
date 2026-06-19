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
    assert set(body.keys()) == {"results", "next"}
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
    assert body == {"results": [], "next": None}


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
    assert set(body.keys()) == {"results", "next"}
    ids = [r["id"] for r in body["results"]]
    assert ids == [10001]


# --------------------------------------------------------------------------- #
# app_structure — parity with real Connect's {learn_app, deliver_app} wrapper
# --------------------------------------------------------------------------- #
_WRAPPER = {"learn_app": {"modules": ["L"]}, "deliver_app": {"modules": ["D"]}}


@pytest.mark.django_db
def test_app_structure_default_both_returns_wrapper(monkeypatch):
    _install(monkeypatch, {"folder-a": {"app_structure.json": _WRAPPER}})
    _make_opp()
    resp = _client_for(_user()).get(APP_STRUCTURE_URL)
    assert resp.status_code == 200
    assert resp.json() == _WRAPPER


@pytest.mark.django_db
def test_app_structure_app_type_learn_nulls_deliver(monkeypatch):
    _install(monkeypatch, {"folder-a": {"app_structure.json": _WRAPPER}})
    _make_opp()
    body = _client_for(_user()).get(APP_STRUCTURE_URL + "?app_type=learn").json()
    assert body == {"learn_app": {"modules": ["L"]}, "deliver_app": None}


@pytest.mark.django_db
def test_app_structure_app_type_deliver_nulls_learn(monkeypatch):
    _install(monkeypatch, {"folder-a": {"app_structure.json": _WRAPPER}})
    _make_opp()
    body = _client_for(_user()).get(APP_STRUCTURE_URL + "?app_type=deliver").json()
    assert body == {"learn_app": None, "deliver_app": {"modules": ["D"]}}


@pytest.mark.django_db
def test_app_structure_absent_returns_200_with_nulls(monkeypatch):
    _install(monkeypatch, {"folder-a": {"user_visits.json": []}})
    _make_opp()
    resp = _client_for(_user()).get(APP_STRUCTURE_URL)
    assert resp.status_code == 200
    assert resp.json() == {"learn_app": None, "deliver_app": None}


@pytest.mark.django_db
def test_app_structure_invalid_app_type_returns_400(monkeypatch):
    _install(monkeypatch, {"folder-a": {"app_structure.json": _WRAPPER}})
    _make_opp()
    resp = _client_for(_user()).get(APP_STRUCTURE_URL + "?app_type=bogus")
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Keyset pagination — exact production envelope {next, results}
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_keyset_envelope_has_no_count(monkeypatch):
    _install(monkeypatch, {"folder-a": {"user_visits.json": [{"id": 1}]}})
    _make_opp()
    body = _client_for(_user()).get(VISITS_URL).json()
    assert set(body.keys()) == {"next", "results"}


@pytest.mark.django_db
def test_keyset_last_id_advances_by_real_id(monkeypatch):
    rows = [{"id": 10}, {"id": 25}, {"id": 30}]
    _install(monkeypatch, {"folder-a": {"user_visits.json": rows}})
    _make_opp()
    client = _client_for(_user())
    body = client.get(VISITS_URL + "?page_size=2").json()
    assert [r["id"] for r in body["results"]] == [10, 25]
    parts = urlsplit(body["next"])
    assert "last_id=25" in parts.query
    body2 = client.get(f"{parts.path}?{parts.query}").json()
    assert [r["id"] for r in body2["results"]] == [30]
    assert body2["next"] is None


@pytest.mark.django_db
def test_keyset_index_mode_for_id_less_rows(monkeypatch):
    # completed_works rows have no "id" — keyset falls back to positional index.
    rows = [{"entity": f"e{i}"} for i in range(5)]
    _install(monkeypatch, {"folder-a": {"completed_works.json": rows}})
    _make_opp()
    client = _client_for(_user())
    url = "/api/export/opportunity/10001/completed_works/?page_size=2"
    seen = []
    while url is not None:
        body = client.get(url).json()
        seen.extend(r["entity"] for r in body["results"])
        if body["next"] is None:
            break
        parts = urlsplit(body["next"])
        url = f"{parts.path}?{parts.query}"
    assert seen == [f"e{i}" for i in range(5)]
    assert len(set(seen)) == 5


@pytest.mark.django_db
def test_keyset_reverse_order(monkeypatch):
    rows = [{"id": 1}, {"id": 2}, {"id": 3}]
    _install(monkeypatch, {"folder-a": {"user_visits.json": rows}})
    _make_opp()
    body = _client_for(_user()).get(VISITS_URL + "?cursor_order=reverse&page_size=2").json()
    assert [r["id"] for r in body["results"]] == [3, 2]
    parts = urlsplit(body["next"])
    assert "last_id=2" in parts.query
    assert "cursor_order=reverse" in parts.query


@pytest.mark.django_db
def test_keyset_page_size_clamped_to_max(monkeypatch):
    rows = [{"id": i} for i in range(10)]
    _install(monkeypatch, {"folder-a": {"user_visits.json": rows}})
    _make_opp()
    # page_size above max (5000) is clamped, not rejected; all 10 rows fit one page.
    body = _client_for(_user()).get(VISITS_URL + "?page_size=999999").json()
    assert len(body["results"]) == 10
    assert body["next"] is None


# --------------------------------------------------------------------------- #
# payment / invoice / assessment endpoints (#650 gap 2)
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.parametrize(
    "endpoint,filename",
    [
        ("payment", "payment.json"),
        ("invoice", "invoice.json"),
        ("assessment", "assessment.json"),
    ],
)
def test_new_endpoints_serve_their_fixture(monkeypatch, endpoint, filename):
    rows = [{"id": 3, "endpoint": endpoint}]
    _install(monkeypatch, {"folder-a": {filename: rows}})
    _make_opp()
    url = f"/api/export/opportunity/10001/{endpoint}/"
    body = _client_for(_user()).get(url).json()
    assert body["results"] == rows
    assert set(body.keys()) == {"next", "results"}


@pytest.mark.django_db
@pytest.mark.parametrize("endpoint", ["payment", "invoice", "assessment"])
def test_new_endpoints_empty_when_fixture_absent(monkeypatch, endpoint):
    # Opp exists but folder has no payment/invoice/assessment file -> empty page.
    _install(monkeypatch, {"folder-a": {"user_visits.json": []}})
    _make_opp()
    url = f"/api/export/opportunity/10001/{endpoint}/"
    body = _client_for(_user()).get(url).json()
    assert body == {"next": None, "results": []}


# --------------------------------------------------------------------------- #
# opp_org_program_list (#650 gap 1) — purely synthetic org/program/opp tree
# --------------------------------------------------------------------------- #
OPP_ORG_PROGRAM_URL = "/api/export/opp_org_program_list/"


@pytest.mark.django_db
def test_opp_org_program_list_shape_and_fields(monkeypatch):
    _install(
        monkeypatch,
        {"folder-a": {"opportunity.json": {"id": 10001, "name": "Baobab Delivery", "end_date": "2026-12-31"}}},
    )
    _make_opp(org_name="Baobab Institute", program_name="Nutrition", program_id=10050, visit_count=42)
    body = _client_for(_user()).get(OPP_ORG_PROGRAM_URL).json()
    assert set(body.keys()) == {"organizations", "opportunities", "programs"}

    assert body["organizations"] == [
        {
            "id": "labs-synthetic-baobab-institute",
            "slug": "labs-synthetic-baobab-institute",
            "name": "Baobab Institute",
        }
    ]
    assert body["programs"] == [
        {
            "id": 10050,
            "name": "Nutrition",
            "delivery_type": None,
            "currency": None,
            "organization": "labs-synthetic-baobab-institute",
        }
    ]
    [opp] = body["opportunities"]
    assert opp["id"] == 10001
    assert opp["name"] == "Baobab Delivery"
    assert opp["organization"] == "labs-synthetic-baobab-institute"
    assert opp["program"] == 10050
    assert opp["is_active"] is True
    assert opp["end_date"] == "2026-12-31"
    assert opp["visit_count"] == 42
    assert "date_created" in opp


@pytest.mark.django_db
def test_opp_org_program_list_only_visible(monkeypatch):
    _install(
        monkeypatch,
        {
            "folder-a": {"opportunity.json": {"id": 10001, "name": "Mine"}},
            "folder-b": {"opportunity.json": {"id": 10002, "name": "Theirs"}},
        },
    )
    _make_opp(opportunity_id=10001, gdrive_folder_id="folder-a", allowed_domains=["@dimagi.com"])
    _make_opp(opportunity_id=10002, gdrive_folder_id="folder-b", allowed_domains=["@example.org"])
    body = _client_for(_user(email="me@dimagi.com")).get(OPP_ORG_PROGRAM_URL).json()
    assert [o["id"] for o in body["opportunities"]] == [10001]
    assert [o["slug"] for o in body["organizations"]] == ["labs-synthetic-labs-synthetic"]


@pytest.mark.django_db
def test_opp_org_program_list_links_are_consistent(monkeypatch):
    _install(
        monkeypatch,
        {
            "folder-a": {"opportunity.json": {"id": 10001, "name": "A"}},
            "folder-b": {"opportunity.json": {"id": 10002, "name": "B"}},
        },
    )
    # Two opps sharing one program under one org -> collapse to 1 org + 1 program.
    _make_opp(opportunity_id=10001, gdrive_folder_id="folder-a", org_name="Org", program_name="P", program_id=10050)
    _make_opp(opportunity_id=10002, gdrive_folder_id="folder-b", org_name="Org", program_name="P", program_id=10050)
    body = _client_for(_user()).get(OPP_ORG_PROGRAM_URL).json()

    org_slugs = {o["slug"] for o in body["organizations"]}
    program_ids = {p["id"] for p in body["programs"]}
    assert len(body["organizations"]) == 1
    assert len(body["programs"]) == 1
    assert len(body["opportunities"]) == 2
    for opp in body["opportunities"]:
        assert opp["organization"] in org_slugs
        assert opp["program"] in program_ids
    for program in body["programs"]:
        assert program["organization"] in org_slugs


@pytest.mark.django_db
def test_opp_org_program_list_requires_auth(monkeypatch):
    _install(monkeypatch, {})
    resp = APIClient().get(OPP_ORG_PROGRAM_URL)
    assert resp.status_code == 401
