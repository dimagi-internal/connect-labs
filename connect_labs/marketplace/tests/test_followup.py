"""The CHP RFI's follow-up form columns. All values invented, except that the shipped data file is checked as it is."""

import datetime as dt
import json

import pytest
from django.urls import reverse

from connect_labs.marketplace import followup
from connect_labs.marketplace.testing import make_partner
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse
from connect_labs.supply_chain.runner import dimagi_org


class _Resp:
    def __init__(self, pk):
        self.pk = pk


def _row(pk):
    return {"response": _Resp(pk)}


def _entry(response_id, form, org_slug=None, french=False, **extra):
    return {
        "response_id": response_id,
        "org_slug": org_slug,
        "form": form,
        "french": french,
        "rule": "a rule",
        "flags": [],
        **extra,
    }


@pytest.fixture
def data(monkeypatch):
    entries = [
        _entry(1, "kc"),
        _entry(2, "chc", french=True),
        # One organisation, two submissions, two different forms.
        _entry(3, "mbw", org_slug="twice"),
        _entry(4, "rutf", org_slug="twice"),
        # A merged resubmission answers to the same entry.
        _entry(5, "lead", also_response_ids=[6]),
    ]
    monkeypatch.setattr(followup, "_load", lambda: {"round": followup.ROUND_SLUG, "entries": entries})
    return entries


class TestShippedData:
    """The file as committed: the checks that would have caught a bad regeneration."""

    def _data(self):
        with followup.DATA_FILE.open(encoding="utf-8") as f:
            return json.load(f)

    def test_is_for_this_round(self):
        assert self._data()["round"] == followup.ROUND_SLUG

    def test_every_entry_has_a_known_form_and_a_unique_response(self):
        entries = self._data()["entries"]
        ids = [e["response_id"] for e in entries] + [i for e in entries for i in e.get("also_response_ids", [])]
        assert len(ids) == len(set(ids))
        assert {e["form"] for e in entries} <= set(followup.FORMS)
        assert all(isinstance(e["french"], bool) for e in entries)

    def test_holds_no_names_or_contacts(self):
        """Keyed by response id and directory slug only, so the file can sit in the repository."""
        allowed = {"response_id", "org_slug", "form", "french", "rule", "flags", "also_response_ids"}
        assert all(set(e) <= allowed for e in self._data()["entries"])


class TestAttach:
    def test_gives_each_row_its_form_and_language(self, data):
        rows = [_row(1), _row(2)]
        followup.attach(rows)
        assert rows[0]["followup"]["label"] == "Kangaroo Care"
        assert rows[0]["followup"]["french"] is False
        assert rows[1]["followup"]["label"] == "Child Health Campaigns"
        assert rows[1]["followup"]["french"] is True

    def test_a_row_with_no_entry_has_none(self, data):
        rows = [_row(999)]
        followup.attach(rows)
        assert rows[0]["followup"] is None

    def test_lists_the_other_form_an_organisation_was_given(self, data):
        rows = [_row(3)]
        followup.attach(rows)
        assert rows[0]["followup"]["label"] == "Mother Baby Wellness"
        assert rows[0]["followup"]["others"] == ["RUTF wasting treatment"]

    def test_a_merged_resubmission_is_the_same_entry(self, data):
        rows = [_row(6)]
        followup.attach(rows)
        assert rows[0]["followup"]["label"] == "Lead exposure"
        assert rows[0]["followup"]["others"] == []


class TestFilter:
    def _rows(self):
        rows = [_row(1), _row(2), _row(3), _row(999)]
        followup.attach(rows)
        return rows

    def test_by_form(self, data):
        assert [r["response"].pk for r in followup.filter_rows(self._rows(), form="kc")] == [1]

    def test_by_language(self, data):
        assert [r["response"].pk for r in followup.filter_rows(self._rows(), french="yes")] == [2]
        assert [r["response"].pk for r in followup.filter_rows(self._rows(), french="no")] == [1, 3]

    def test_both_together(self, data):
        assert followup.filter_rows(self._rows(), form="kc", french="yes") == []

    def test_the_rows_with_no_form(self, data):
        assert [r["response"].pk for r in followup.filter_rows(self._rows(), form="none")] == [999]

    def test_an_unrecognised_value_filters_nothing(self, data):
        assert len(followup.filter_rows(self._rows(), form="bogus", french="maybe")) == 4

    def test_counts_every_form_even_when_zero(self, data):
        counts = {c["key"]: c["count"] for c in followup.form_counts(self._rows())}
        assert counts["kc"] == 1 and counts["chc"] == 1 and counts["povgrad"] == 0
        assert list(counts) == list(followup.FORMS)


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user(username="staff", password="x", email="staff@dimagi.com")


@pytest.fixture
def chp_round(db, monkeypatch):
    round_ = Solicitation.objects.create(
        owner_org=dimagi_org(),
        slug=followup.ROUND_SLUG,
        title="Community Health RFI 2026",
        solicitation_type="eoi",
        status="closed",
        sa_access_state="ok",
        published_on=dt.date(2026, 10, 1),
    )
    orgs = [make_partner(f"Org {n}", f"O{n}", countries=["Nigeria"]) for n in range(3)]
    responses = [
        SolicitationResponse.objects.create(
            solicitation=round_, llo_entity=org, source_row=n + 2, org_name=org.name, match_state="email"
        )
        for n, org in enumerate(orgs)
    ]
    forms = [("kc", False), ("chc", True), ("rutf", False)]
    entries = [_entry(r.pk, f, french=fr) for r, (f, fr) in zip(responses, forms)]
    monkeypatch.setattr(followup, "_load", lambda: {"round": followup.ROUND_SLUG, "entries": entries})
    return round_


@pytest.mark.django_db
class TestRoundPage:
    def _get(self, client, user, **params):
        client.force_login(user)
        return client.get(reverse("marketplace:round", args=[followup.ROUND_SLUG]), params)

    def test_shows_the_follow_up_columns_with_the_sent_ones_blank(self, client, user, chp_round):
        body = self._get(client, user).content.decode()
        assert "Follow-up form" in body
        assert "Follow-up form sent" in body
        assert "Date sent" in body
        assert "Kangaroo Care" in body and "Child Health Campaigns" in body

    def test_filters_by_form(self, client, user, chp_round):
        response = self._get(client, user, form="kc")
        assert len(response.context["table_rows"]) == 1
        assert "1 of 3 shown" in response.content.decode()

    def test_filters_by_language(self, client, user, chp_round):
        assert len(self._get(client, user, french="yes").context["table_rows"]) == 1

    def test_a_filter_that_matches_nobody_says_so(self, client, user, chp_round):
        body = self._get(client, user, form="lead").content.decode()
        assert "No applicants match this filter" in body

    def test_the_header_counts_ignore_the_filter(self, client, user, chp_round):
        response = self._get(client, user, form="kc")
        assert len(response.context["applicants"]) == 3

    def test_another_round_has_none_of_it(self, client, user, chp_round):
        other = Solicitation.objects.create(
            owner_org=dimagi_org(), slug="other-2026", title="Other", status="closed", sa_access_state="ok"
        )
        org = make_partner("Elsewhere", "EW", countries=["Kenya"])
        SolicitationResponse.objects.create(
            solicitation=other, llo_entity=org, source_row=2, org_name=org.name, match_state="email"
        )
        client.force_login(user)
        body = client.get(reverse("marketplace:round", args=["other-2026"]), {"form": "kc"}).content.decode()
        assert "Follow-up form" not in body
        assert "followup-filters" not in body
