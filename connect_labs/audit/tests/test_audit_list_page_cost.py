"""The /audit/ list must not re-download every session on every page (#2512).

On 2026-10-13 a crawl paging /audit/?page=N coincided with 18 web-worker OOM
kills: each page fetched all 623 sessions in scope, visit_images included.
These tests pin the cost: paging is one fetch per user and scope per TTL, and
the cached rows render the list exactly as the full records did.
"""

from unittest import mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import RequestFactory

from connect_labs.audit.models import AuditSessionRecord
from connect_labs.audit.tables import AuditTable
from connect_labs.audit.views import ExperimentAuditListView, _slim_session_list_row


def _session(pk, usernames=("flw_a",), storage_opp=1982, subject_opp=1982):
    visit_images = {
        str(v): [{"username": u, "blob_id": f"b{v}", "assessment": "x" * 500}] for v, u in enumerate(usernames)
    }
    return AuditSessionRecord(
        {
            "id": pk,
            "experiment": "audit",
            "type": "AuditSession",
            "username": "auditor",
            "opportunity_id": storage_opp,
            "data": {
                "title": f"Audit {pk}",
                "tag": "t",
                "description": "d",
                "status": "completed",
                "overall_result": "pass",
                "completed_at": "2026-10-01T10:00:00Z",
                "opportunity_id": subject_opp,
                "opportunity_name": "Opp",
                "visit_ids": list(range(len(usernames))),
                "visit_images": visit_images,
            },
        }
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _get(user, page=None, opp=1982):
    params = {"page": page} if page else {}
    request = RequestFactory().get("/audit/", params)
    request.user = user
    request.labs_context = {"opportunity_id": opp}
    request.session = {}
    view = ExperimentAuditListView()
    view.setup(request)
    return view, view.get_queryset()


@pytest.fixture
def dao():
    sessions = [_session(pk) for pk in range(1, 101)]
    with mock.patch("connect_labs.audit.views.AuditDataAccess") as cls:
        instance = cls.return_value
        instance.get_audit_sessions.return_value = sessions
        instance.get_flw_names.return_value = {"flw_a": "Alice"}
        yield instance


def _user(pk):
    user = mock.Mock(spec=["pk", "is_authenticated"])
    user.pk = pk
    user.is_authenticated = True
    return user


def test_paging_fetches_once_not_per_page(dao):
    user = _user(1)
    _get(user)  # page 1
    for page in range(2, 30):  # includes pages far past the end
        _get(user, page=str(page))
    assert dao.get_audit_sessions.call_count == 1
    assert dao.get_flw_names.call_count == 1


def test_page_one_always_refetches(dao):
    user = _user(1)
    _get(user)
    _get(user, page="2")
    _get(user, page="1")
    _get(user)
    assert dao.get_audit_sessions.call_count == 3


def test_cache_is_per_user_and_per_scope(dao):
    _get(_user(1), page="2")
    _get(_user(2), page="2")
    _get(_user(1), page="2", opp=2000)
    assert dao.get_audit_sessions.call_count == 3


def test_cold_cache_on_a_later_page_still_fetches(dao):
    _, rows = _get(_user(1), page="3")
    assert dao.get_audit_sessions.call_count == 1
    assert len(rows) == 100


def test_cached_rows_render_like_full_records(dao):
    full = _session(7, usernames=("flw_b", "flw_a", "flw_b"), storage_opp=50, subject_opp=60)
    slim = AuditSessionRecord(_slim_session_list_row(full))

    assert slim.pk == full.pk
    assert slim.opportunity_id == full.opportunity_id == 60
    assert slim.storage_opportunity_id == full.storage_opportunity_id == 50
    assert slim.flw_username == full.flw_username == "flw_b"
    for attr in ("title", "tag", "description", "status", "overall_result", "completed_at", "visit_ids"):
        assert getattr(slim, attr) == getattr(full, attr), attr

    table = AuditTable([])
    for method in ("render_actions", "render_select", "render_visit_count"):
        kwargs = {"record": full}
        if method == "render_visit_count":
            kwargs["value"] = full.visit_ids
        expected = getattr(table, method)(**kwargs)
        kwargs["record"] = slim
        assert getattr(table, method)(**kwargs) == expected, method


def test_cached_row_drops_visit_image_payload():
    row = _slim_session_list_row(_session(1, usernames=("a", "b", "c")))
    assert row["data"]["visit_images"] == {"_list": [{"username": "a"}, {"username": "b"}, {"username": "c"}]}


def test_no_scope_does_not_fetch(dao):
    request = RequestFactory().get("/audit/")
    request.user = AnonymousUser()
    request.labs_context = {}
    view = ExperimentAuditListView()
    view.setup(request)
    assert view.get_queryset() == []
    assert dao.get_audit_sessions.call_count == 0
