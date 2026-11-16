"""What labs reads from CommCare about a person, in the shapes HQ really answers with (read live 2026-11-16)."""

from unittest import mock

import httpx
import pytest

from connect_labs.hqdata.identity import IdentityError, fetch_domains, fetch_identity


class Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


IDENTITY = {
    "id": "abc",
    "username": "ace@example.org",
    "first_name": "A",
    "last_name": "B",
    "email": "ace@example.org",
}


def test_the_projects_come_from_the_user_domains_list_because_identity_carries_none():
    pages = [
        Resp(
            {
                "meta": {"next": "/api/v0.5/user_domains/?limit=100&offset=2"},
                "objects": [
                    {"domain_name": "Demo", "project_name": "Demo"},
                    {"domain_name": "other", "project_name": "o"},
                ],
            }
        ),
        Resp({"meta": {"next": None}, "objects": [{"domain_name": "third", "project_name": "t"}]}),
    ]
    with mock.patch("httpx.get", side_effect=[Resp(IDENTITY), *pages]) as get:
        got = fetch_identity("tok")
    assert got["domains"] == ["demo", "other", "third"] and got["username"] == "ace@example.org"
    assert [c.args[0] for c in get.call_args_list][1:] == [
        "https://www.commcarehq.org/api/v0.5/user_domains/?limit=100",
        "https://www.commcarehq.org/api/v0.5/user_domains/?limit=100&offset=2",
    ]


@pytest.mark.parametrize(
    "answer",
    [Resp({}, 403), Resp({}, 500), Resp(ValueError("x")), Resp({"meta": {}}), httpx.ConnectError("down")],
)
def test_a_failed_project_list_is_an_error_never_an_empty_list(answer):
    # An empty list deletes the person's memberships: "could not ask" must not look like "in none".
    effect = answer if isinstance(answer, Exception) else [answer]
    with mock.patch("httpx.get", side_effect=effect if isinstance(effect, list) else effect):
        with pytest.raises(IdentityError):
            fetch_domains("tok")


def test_a_list_that_never_ends_stops():
    endless = Resp({"meta": {"next": "/api/v0.5/user_domains/?offset=1"}, "objects": []})
    with mock.patch("httpx.get", return_value=endless):
        with pytest.raises(IdentityError):
            fetch_domains("tok")
