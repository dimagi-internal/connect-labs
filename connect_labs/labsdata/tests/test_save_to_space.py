"""A results card's "Save to a space" (``?save=1``, labsdata/views.py ``space_to_save_in``):
the Data page opens on the saved space that holds the card's selection -- the workflow's
own, else its programme's, else exactly its opportunities' -- with the Save dialog filled;
with none, on the selection, where the dialog offers to make one. Nothing is saved here,
and a space the person cannot open is never chosen.

THIS REPOSITORY IS PUBLIC. Every name and value here is invented.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from connect_labs.labsdata import engine, service
from connect_labs.labsdata.models import SavedQuery
from connect_labs.labsdata.tests import test_spaces
from connect_labs.labsdata.tests.test_spaces import O1, O2, PROGRAM, _client, _post, _program_space

pytestmark = pytest.mark.django_db

people = test_spaces.people
connect = test_spaces.connect

CARD = "/labs/data/?programs={p}&sql=SELECT+1+AS+n+FROM+visits&run=1&save=1&display=%7B%7D"


def test_a_cards_save_opens_the_space_of_its_programme_with_the_dialog_filled(people):
    sid = _program_space(people)
    res = _client(people["bob"]).get(CARD.format(p=PROGRAM))
    assert res.status_code == 302
    url = urlsplit(res["Location"])
    assert url.path == f"/labs/data/space/{sid}/"
    assert parse_qs(url.query) == {
        "sql": ["SELECT 1 AS n FROM visits"],
        "run": ["1"],
        "save": ["1"],
        "display": ["{}"],
    }
    assert not SavedQuery.objects.exists()  # the person saves it there, with a click


def test_exactly_its_opportunities_is_a_match_too(people):
    res = _post(
        _client(people["alice"]), "/labs/data/api/spaces/", {"name": "Two opps", "opportunity_ids": [O1.id, O2.id]}
    )
    sid = res.json()["space"]["id"]
    out = _client(people["bob"]).get(f"/labs/data/?opps={O1.id},{O2.id}&sql=SELECT+1&save=1")
    assert urlsplit(out["Location"]).path == f"/labs/data/space/{sid}/"
    # One opportunity is not that space: the plain page, which offers a new one.
    plain = _client(people["bob"]).get(f"/labs/data/?opps={O1.id}&sql=SELECT+1&save=1")
    assert plain.status_code == 200


@pytest.mark.parametrize("who", ["carol", "dave"])
def test_a_space_the_person_cannot_open_is_never_chosen(people, who):
    _program_space(people)
    res = _client(people[who]).get(CARD.format(p=PROGRAM))
    # Not redirected to it (nor told of it): the page on the selection, refused as ever by
    # the page's own calls -- and the Save dialog still offered.
    assert res.status_code == 200
    assert res.context["open_intent"]["save"] is True


def test_a_workflow_space_is_the_one_read_in_the_same_scope(people, monkeypatch):
    from connect_labs.labsdata import spaces, views

    found = [
        {
            "spaces": [
                {"id": 1, "workflow": {"id": 9, "program_id": 77}, "program_ids": [], "opportunity_ids": [O1.id]}
            ]
        },
        {
            "spaces": [
                {
                    "id": 2,
                    "workflow": {"id": 9, "opportunity_id": O1.id},
                    "program_ids": [],
                    "opportunity_ids": [O1.id],
                }
            ]
        },
    ]
    monkeypatch.setattr(spaces, "openable", lambda caller: found)
    monkeypatch.setattr(
        spaces, "generate", lambda *a, **k: type("G", (), {"selection": type("S", (), {"ids": [O1.id]})()})()
    )
    monkeypatch.setattr(spaces, "open", lambda caller, sid: type("O", (), {"id": sid})())
    req = type("R", (), {"GET": {"save": "1", "sql": "SELECT 1", "workflow": f"9:opp:{O1.id}"}})()
    assert views.space_to_save_in(None, req) == "/labs/data/space/2/?sql=SELECT+1&save=1"


def test_a_query_that_does_not_run_in_the_space_is_not_sent_there(people, monkeypatch):
    _program_space(people)

    def validate(sql, space):
        raise engine.QueryError("invalid_sql", 'relation "p_visits" does not exist')

    monkeypatch.setattr(service, "validate_selected", validate)
    res = _client(people["bob"]).get(CARD.format(p=PROGRAM))
    assert res.status_code == 200


def test_without_save_the_card_link_opens_the_selection_as_before(people):
    _program_space(people)
    res = _client(people["bob"]).get(f"/labs/data/?programs={PROGRAM}&sql=SELECT+1&run=1")
    assert res.status_code == 200
