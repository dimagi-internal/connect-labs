"""Organisations as the peer unit: a stable set, every member on every indicator.

The per-opportunity peer charts were unreadable because the peer set changed
from indicator to indicator -- a peer without a publishable figure simply
vanished. A `complete_cohort` keeps every organisation on every non-count
indicator, a withheld figure carrying its band, and the reader's own
organisation told apart without any name reaching them.
"""

import json
from unittest.mock import MagicMock

import pytest

from connect_labs.benchmarks.data_access import benchmarks_for_opportunity
from connect_labs.benchmarks.disclosure import OrganisationCell, anonymise_organisations
from connect_labs.benchmarks.models import BenchmarkCohort, BenchmarkValue
from connect_labs.benchmarks.publish import publish_benchmark
from connect_labs.benchmarks.tests.test_publish import OPPS, build_snapshot

pytestmark = pytest.mark.django_db

# OPPS 500..505 across three organisations.
LLO = {500: "Alpha", 501: "Alpha", 502: "Beta", 503: "Beta", 504: "Gamma", 505: "Gamma"}


def _cell(v, band="green", n=40):
    return {"value": v, "band": band, "n": n}


def snapshot():
    snap = build_snapshot()
    snap["deployment"] = {**snap["deployment"], "llo_map": {str(k): v for k, v in LLO.items()}}
    snap["byLLO"] = [
        {"llo": "Alpha", "ind": {"lost_by_day_28": _cell(0.2), "total_cases": _cell(900, "unbanded")}},
        {"llo": "Beta", "ind": {"lost_by_day_28": _cell(0.4, "notcredible"), "total_cases": _cell(300, "unbanded")}},
        {"llo": "Gamma", "ind": {"lost_by_day_28": _cell(0.1, "red", n=4), "total_cases": _cell(60, "unbanded")}},
    ]
    return snap


def _cohort(complete=True):
    cohort = BenchmarkCohort.objects.create(
        name="KMC", organization_id="dimagi-kmc", min_peers=1, min_denominator=20, complete_cohort=complete
    )
    for opp in OPPS:
        cohort.members.create(opportunity_id=opp)
    return cohort


def _publish(cohort):
    return publish_benchmark(
        cohort,
        snapshot=snapshot(),
        source_workflow_id=19778,
        source_run_id=19783,
        registry_id=19784,
        as_of="2026-09-11",
    )


def _org_rows(pub, indicator="lost_by_day_28"):
    return list(
        BenchmarkValue.objects.filter(publication=pub, unit="organisation", indicator_id=indicator).order_by(
            "peer_index"
        )
    )


class TestACompleteCohort:
    def test_every_organisation_is_on_the_indicator_withheld_ones_with_their_reason(self):
        rows = _org_rows(_publish(_cohort()))
        assert sorted(r.organisation for r in rows) == ["Alpha", "Beta", "Gamma"]
        bands = {r.organisation: r.band for r in rows}
        # Beta's recording is not credible; Gamma has 4 babies, under the
        # cohort's minimum of 20. Both stay, graded.
        assert bands == {"Alpha": "green", "Beta": "notcredible", "Gamma": "insufficient"}

    def test_drawable_figures_come_first_in_value_order(self):
        rows = _org_rows(_publish(_cohort()))
        assert [r.organisation for r in rows][0] == "Alpha", "a withheld figure outranked a drawable one"

    def test_a_count_is_never_published_for_organisations_either(self):
        """An organisation's case count IS its size, however complete the cohort."""
        assert _org_rows(_publish(_cohort()), "total_cases") == []

    def test_the_publication_records_which_organisation_each_member_is_in(self):
        pub = _publish(_cohort())
        assert pub.organisation_of == {str(k): v for k, v in LLO.items()}


class TestAnOrdinaryCohort:
    def test_drops_a_withheld_organisation_as_it_always_has(self):
        rows = _org_rows(_publish(_cohort(complete=False)))
        assert [r.organisation for r in rows] == ["Alpha"]


class TestTheReader:
    def _request(self, *opps):
        request = MagicMock()
        request.session = {"labs_oauth": {"organization_data": {"opportunities": [{"id": o} for o in opps]}}}
        request.user = MagicMock(is_authenticated=True, view_synthetic_opps=False)
        return request

    def test_sees_its_own_organisation_apart_from_the_others(self):
        cohort = _cohort()
        _publish(cohort)
        out = benchmarks_for_opportunity(self._request(502), 502)
        orgs = out["indicators"][str(cohort.pk)]["KMC"]["lost_by_day_28"]["organisations"]
        assert orgs["own"] == {"value": 0.4, "band": "notcredible"}, "502 is in Beta"
        assert [o["band"] for o in orgs["others"]] == ["green", "insufficient"]

    def test_never_sees_an_organisation_name(self):
        cohort = _cohort()
        _publish(cohort)
        blob = json.dumps(benchmarks_for_opportunity(self._request(502), 502))
        for name in LLO.values():
            assert name not in blob, f"{name} reached a reader"


class TestTheDisclosureRule:
    CELLS = [
        OrganisationCell("A", 0.5, "green", 40),
        OrganisationCell("B", 0.2, "red", 40),
        OrganisationCell("C", None, "notinapp", None),
        OrganisationCell("D", 0.9, "green", 3),
    ]

    def test_complete_keeps_everyone_and_grades_the_thin(self):
        out = anonymise_organisations(self.CELLS, min_peers=1, min_denominator=20, tie_salt="x", complete=True)
        assert [(o, b) for _i, _v, b, o in out] == [
            ("B", "red"),
            ("A", "green"),
            ("D", "insufficient"),
            ("C", "notinapp"),
        ]

    def test_otherwise_only_drawable_figures_survive(self):
        out = anonymise_organisations(self.CELLS, min_peers=1, min_denominator=20, tie_salt="x", complete=False)
        assert [o for _i, _v, _b, o in out] == ["B", "A"]

    def test_too_few_drawable_organisations_publish_nothing(self):
        assert anonymise_organisations(self.CELLS, min_peers=3, min_denominator=20, tie_salt="x", complete=True) == []

    def test_one_organisation_twice_is_refused(self):
        with pytest.raises(ValueError):
            anonymise_organisations(
                [OrganisationCell("A", 1, "green"), OrganisationCell("A", 2, "green")],
                min_peers=1,
                min_denominator=0,
                tie_salt="x",
                complete=True,
            )
