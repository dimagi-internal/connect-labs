"""The Labs redirect to a worker's Tasks screen in Connect (workflow/connect_links.py)."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.workflow import connect_links

pytestmark = pytest.mark.django_db

UID = "1d2fa661-bf1b-43cd-8077-91be5b8acd85"


@pytest.fixture(autouse=True)
def _connect(settings):
    settings.CONNECT_PRODUCTION_URL = "https://connect.example/"
    cache.clear()


@pytest.fixture
def person(client):
    user = get_user_model().objects.create_user(username="pm", password="p")
    client.force_login(user)
    return user


def _visit(username="u1", user_id=UID, opp=765, n=1):
    return RawVisitCache.objects.create(
        opportunity_id=opp,
        visit_count=1,
        expires_at=timezone.now() + timedelta(days=1),
        visit_id=f"v{n}",
        username=username,
        user_id=user_id,
    )


#: The MBW shape: Solina delivers opp 765 under program 60, managed by tnyirenda.
TREE = {
    "organizations": [{"slug": "tnyirenda"}],
    "programs": [{"id": 60, "organization": "tnyirenda"}],
    "opportunities": [{"id": 765, "program": 60, "organization": "solina"}],
}


def _get(client, allowed=(765,), slug="tnyirenda", tree=None, **params):
    params = {"opportunity_id": 765, "username": "u1", **params}
    with (
        patch("connect_labs.workers.access.accessible_opportunity_ids", return_value=(set(allowed), True)),
        patch("connect_labs.workers.access._token", return_value="tok"),
        patch("connect_labs.labs.context.get_org_data", return_value=tree or {}),
        patch.object(connect_links, "opportunity_org_slug", return_value=slug),
    ):
        return client.get(reverse("labs:workflow:connect_worker_tasks"), params)


def test_redirects_to_the_workers_tasks_in_connect(client, person):
    _visit()
    r = _get(client)
    assert r.status_code == 302
    assert r["Location"] == f"https://connect.example/a/tnyirenda/opportunity/765/user_tasks/?user={UID}"


def test_a_worker_with_no_cached_visit_goes_to_the_task_list(client, person):
    _visit(username="someone-else")
    r = _get(client)
    assert r["Location"] == "https://connect.example/a/tnyirenda/opportunity/765/assigned_tasks/"


def test_a_cached_row_with_no_user_id_is_skipped_for_one_that_has_it(client, person):
    _visit(user_id="", n=1)
    _visit(n=2)
    assert _get(client)["Location"].endswith(f"user_tasks/?user={UID}")


def test_an_opportunity_the_person_cannot_access_is_not_found(client, person):
    _visit()
    assert _get(client, allowed=()).status_code == 404


def test_bad_input_is_not_found(client, person):
    assert _get(client, opportunity_id="x").status_code == 404
    assert _get(client, username="a b/../c").status_code == 404


def test_an_opportunity_connect_does_not_name_is_not_found(client, person):
    _visit()
    assert _get(client, slug=None).status_code == 404


def test_signed_out_goes_to_sign_in(client):
    r = client.get(reverse("labs:workflow:connect_worker_tasks"), {"opportunity_id": 765, "username": "u1"})
    assert r.status_code == 302 and "connect.example" not in r["Location"]


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def test_the_org_slug_is_read_from_connect_and_cached():
    with patch("httpx.get", return_value=_Resp(200, {"organization": "tnyirenda"})) as get:
        assert connect_links.opportunity_org_slug(765, "tok") == "tnyirenda"
        assert connect_links.opportunity_org_slug(765, "tok") == "tnyirenda"
    assert get.call_count == 1
    assert get.call_args.args[0] == "https://connect.example/export/opportunity/765/"
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


def test_an_odd_org_slug_is_refused():
    with patch("httpx.get", return_value=_Resp(200, {"organization": "evil.example/x"})):
        assert connect_links.opportunity_org_slug(765, "tok") is None
    with patch("httpx.get", return_value=_Resp(403, {})):
        assert connect_links.opportunity_org_slug(766, "tok") is None


def test_a_program_manager_goes_through_their_own_org_not_the_owner(client, person):
    # Found live: /a/solina/... is a 404 for a tnyirenda program manager.
    _visit()
    r = _get(client, tree=TREE, slug="solina")
    assert r["Location"].startswith("https://connect.example/a/tnyirenda/opportunity/765/")


def test_viewer_org_slug_picks_the_org_the_person_belongs_to():
    assert connect_links.viewer_org_slug(TREE, 765) == "tnyirenda"
    solina_member = {**TREE, "organizations": [{"slug": "solina"}]}
    assert connect_links.viewer_org_slug(solina_member, 765) == "solina"
    assert connect_links.viewer_org_slug({**TREE, "organizations": []}, 765) is None
    assert connect_links.viewer_org_slug(TREE, 999) is None
    supervised = {
        "organizations": [{"slug": "sup"}],
        "programs": [],
        "opportunities": [{"id": 765, "program": 60, "organization": "solina", "supervising_organization": "sup"}],
    }
    assert connect_links.viewer_org_slug(supervised, 765) == "sup"


def test_the_worker_directory_gives_the_id_when_no_visit_is_cached(client, person):
    # MBW opp 765's visit pipeline reads Connect's CSV export, which caches no user id.
    from connect_labs.workers.models import ConnectWorker

    ConnectWorker.objects.create(username="u1", connect_user_id=UID)
    _visit(user_id="")
    r = _get(client, tree=TREE)
    assert r["Location"] == f"https://connect.example/a/tnyirenda/opportunity/765/user_tasks/?user={UID}"
