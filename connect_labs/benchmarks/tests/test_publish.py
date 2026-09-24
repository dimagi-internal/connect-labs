"""Publication reads a completed run's frozen snapshot and recomputes nothing.

THE FIXTURE IS BUILT BY THE REAL BUILDER. Every snapshot below comes out of
`connect_labs.semantic.snapshot.build()` -- the one function that produces the
payload a saved run stores -- graded with the REAL KMC registry catalog, so the
keys, the graded-cell shape and the units are the ones production emits rather
than the ones this publisher hopes for. The previous fixture was hand-written to
match the publisher, which is how a publisher that read `series[x]["opportunity"]`
-- a key `snapshot.py` has never emitted -- passed 23 tests while writing zero
rows against every real run.
"""

import datetime as dt

import pytest

from connect_labs.benchmarks import publish as publish_module
from connect_labs.benchmarks.models import BenchmarkCohort, BenchmarkPublication, BenchmarkValue
from connect_labs.benchmarks.publish import SnapshotShapeError, publish_benchmark, resolve_benchmarkable_ids
from connect_labs.semantic import snapshot as semantic_snapshot
from connect_labs.semantic.runtime import filter_to_series, load_registry, measure_catalog

pytestmark = pytest.mark.django_db

OPPS = [500 + i for i in range(6)]
MONTHS = ("2026-01", "2026-02")

_REGISTRY = load_registry("kmc")[1]


def _catalog(series: str, indicator_ids) -> list[dict]:
    """The REAL frozen catalog for these indicators: registry meta, units included."""
    catalog = [m for m in measure_catalog(filter_to_series(_REGISTRY, series)) if m["indicator"] in indicator_ids]
    assert len(catalog) == len(indicator_ids), f"registry no longer defines all of {indicator_ids}"
    return catalog


def _columns(measures, offset: float) -> dict:
    """One scope row's measure columns, as `evaluate()` returns them."""
    out = {}
    for i, m in enumerate(measures):
        # A count indicator carries a COUNT -- the opportunity's size, which is
        # the whole reason it must never be benchmarked (C1).
        value = 800 + 100 * offset if m["unit"] == "n" else 40.0 + offset + i
        out[m["id"]] = value
        out[f"{m['id']}_numerator"] = value
        out[f"{m['id']}_denominator"] = 100
    return out


def _rows(c_measures, n_measures, opps, months) -> list[dict]:
    rows = []
    for i, opp in enumerate(opps):
        rows.append(
            {"scope": "opportunity", "opportunity_id": opp, "n_cases": 100, **_columns(c_measures + n_measures, i)}
        )
        for j, month in enumerate(months):
            rows.append(
                {
                    "scope": "opportunity_month",
                    "opportunity_id": opp,
                    "cohort_month": f"{month}-01",
                    "n_cases": 100,
                    **_columns(c_measures, i + j),
                }
            )
    return rows


def build_snapshot(c_ids=("C01", "C15"), n_ids=("N08",), opps=OPPS, months=MONTHS) -> dict:
    """A real graded payload: `snapshot.build()` over real registry measures."""
    c_measures = _catalog("C", c_ids)
    n_measures = _catalog("N", n_ids) if n_ids else []
    return semantic_snapshot.build(
        spec={},
        rows=_rows(c_measures, n_measures, opps, months),
        measures=c_measures,
        deployment={"llo_map": {o: "LLO One" for o in opps}, "app_asks": {}, "asks_as": {}, "settings": {}},
        extra_series={"N": n_measures} if n_measures else None,
        as_of="2026-09-11",
    )


SNAPSHOT = build_snapshot()

# The shape the FIRST version of this publisher was written against, which
# `snapshot.py` has never emitted. Kept as a regression fixture: reading it must
# fail loudly rather than write an empty publication.
SHAPE_THAT_NEVER_EXISTED = {
    "as_of": "2026-09-11",
    "series": {
        "N": {
            "measures": [{"indicator": "N08", "id": "n08", "unit": "%"}],
            "opportunity": {
                "N08": [
                    {"opportunity_id": 500 + i, "value": 50.0 + i, "denominator": 100, "suppressed": False}
                    for i in range(6)
                ]
            },
            "opportunity_month": {},
        }
    },
}


def _cohort(members=OPPS):
    cohort = BenchmarkCohort.objects.create(name="KMC", organization_id="dimagi-kmc")
    for opp in members:
        cohort.members.create(opportunity_id=opp)
    return cohort


def _history(indicator_ids=("C15",), series="C", opps=OPPS, reports=2, first_report=None, dates=None):
    """The source workflow's completed runs, oldest first — where a SERIES now
    comes from. Reports are taken at SHARED CALENDAR DATES, as the real ones are.

    `first_report` maps an opportunity to the index of the first report it
    appears in. `dates` overrides the report dates outright."""
    first_report = first_report or {}
    dates = dates or [f"2026-0{r + 1}-28" for r in range(reports)]
    out = []
    for r, date in enumerate(dates):
        by_opp = {}
        for i, opp in enumerate(opps):
            if r < first_report.get(opp, 0):
                continue  # this opportunity had not joined yet
            by_opp[opp] = {ind: {"id": ind, "value": 40.0 + i + r, "n": 100, "band": "green"} for ind in indicator_ids}
        out.append({"date": date, "byOpp": {series: by_opp}})
    return out


def _publish(cohort, snapshot=None, **kwargs):
    kwargs.setdefault("history", _history())
    return publish_benchmark(
        cohort,
        snapshot=SNAPSHOT if snapshot is None else snapshot,
        source_workflow_id=19778,
        source_run_id=19783,
        registry_id=19784,
        as_of="2026-09-11",
        **kwargs,
    )


# --- the shape itself -------------------------------------------------------


def test_the_fixture_is_the_real_builders_own_shape():
    """Pins the key path the publisher reads. If `snapshot.py` stops emitting
    `byOpp`, or renames `ind`/`opp`/`monthlyByScope`, this fails HERE rather than
    silently publishing nothing in production."""
    assert SNAPSHOT["byOpp"], "the builder emits no byOpp"
    entry = SNAPSHOT["byOpp"][0]
    assert set(entry) >= {"opp", "llo", "ind", "n"}
    cell = entry["ind"]["C15"]
    assert set(cell) >= {"id", "n", "value", "band"}, f"graded cell shape changed: {cell}"
    assert [k for k in SNAPSHOT["monthlyByScope"] if k.startswith("opp:")], "no per-opportunity monthly scope"
    assert SNAPSHOT["monthlyByScope"]["opp:500"][0]["month"] == "2026-01"
    assert "ind" in SNAPSHOT["monthlyByScope"]["opp:500"][0]
    assert set(SNAPSHOT["series"]["N"]) >= {"measures", "byOpp"}
    # And the keys the dead publisher read are NOT there, in either scope.
    assert "opportunity" not in SNAPSHOT["series"]["N"]
    assert "opportunity_month" not in SNAPSHOT["series"]["N"]


def test_a_snapshot_shape_the_builder_never_emits_raises_rather_than_publishing_nothing():
    cohort = _cohort()
    with pytest.raises(SnapshotShapeError):
        _publish(cohort, snapshot=SHAPE_THAT_NEVER_EXISTED, benchmarkable_indicator_ids={"N08"})
    assert BenchmarkPublication.objects.count() == 0
    assert BenchmarkValue.objects.count() == 0


# --- what may be published at all (C1) --------------------------------------


def test_a_raw_count_indicator_is_never_published():
    """C01 `unit: n` IS the opportunity's case count. Twelve anonymous bars would
    be twelve case counts, and the programme report lists those by name."""
    pub = _publish(_cohort())
    assert BenchmarkValue.objects.filter(publication=pub, indicator_id="C01").count() == 0
    assert "C:C01" in pub.withheld_indicator_ids


def test_a_rate_indicator_is_published():
    pub = _publish(_cohort())
    published = BenchmarkValue.objects.filter(publication=pub, indicator_id="C15")
    assert published.filter(period__isnull=True).count() == len(OPPS)
    assert "C:C15" not in pub.withheld_indicator_ids


def test_an_explicit_allow_list_narrows_what_is_published():
    """A caller may decide indicator by indicator; the default is still to withhold."""
    pub = _publish(_cohort(), benchmarkable_indicator_ids={"C15"})
    assert set(BenchmarkValue.objects.filter(publication=pub).values_list("indicator_id", flat=True)) == {"C15"}
    assert "N:N08" in pub.withheld_indicator_ids


def test_a_cell_the_grader_withheld_never_becomes_an_observation():
    """The bands that mean "do not stand on this figure" are R7's business."""
    assert publish_module._observation(500, {"value": 0.5, "n": 100, "band": "green"}) is not None
    assert publish_module._observation(500, {"value": None, "n": 100, "band": "nodata"}) is None
    assert publish_module._observation(500, {"value": 0.5, "n": 100, "band": "notcredible"}).suppressed
    assert publish_module._observation(500, {"value": 0.5, "n": 100, "band": "insufficient"}).suppressed
    assert publish_module._observation(500, {"value": 0.5, "n": 100, "band": "notinapp"}).suppressed
    thin = publish_module._observation(500, {"value": 0.5, "n": 100, "band": "green", "thinDenominator": True})
    assert thin.suppressed, "a rate over a self-selected minority is not comparable"


def test_a_cell_with_no_denominator_cannot_pass_the_denominator_rule():
    """Missing is not zero-but-fine: a cohort with min_denominator 0 would
    otherwise publish a peer whose base nobody can see."""
    observation = publish_module._observation(500, {"value": 0.5, "n": None, "band": "green"})
    assert observation.denominator == 0
    assert observation.suppressed


# --- the rules, through the publisher ---------------------------------------


def test_it_writes_point_and_series_values():
    pub = _publish(_cohort())
    points = BenchmarkValue.objects.filter(publication=pub, period__isnull=True)
    series = BenchmarkValue.objects.filter(publication=pub, period__isnull=False)
    # C15 and N08 have a point each per opportunity. The SERIES comes from the
    # saved runs, so only the indicator the history carries has one.
    assert points.count() == 2 * len(OPPS)
    assert series.count() == len(OPPS) * 2
    assert set(series.values_list("indicator_id", flat=True)) == {"C15"}
    # Each opportunity's own WEEK OF DELIVERING, not the report date and not
    # the report's ordinal. Every opportunity in the fixture starts 2026-01-01,
    # so the reports of 2026-01-28 and 2026-02-28 are its weeks 3 and 8.
    assert set(series.values_list("period", flat=True)) == {"W3", "W8"}


def test_the_published_values_are_the_graded_cells_own_values():
    pub = _publish(_cohort())
    expected = sorted(e["ind"]["C15"]["value"] for e in SNAPSHOT["byOpp"])
    got = sorted(
        BenchmarkValue.objects.filter(publication=pub, indicator_id="C15", period__isnull=True).values_list(
            "value", flat=True
        )
    )
    assert got == expected


def test_it_stores_provenance_while_the_public_projection_stays_clean():
    pub = _publish(_cohort())
    identified = BenchmarkValue.objects.filter(publication=pub).with_source()
    assert {r.opportunity_id for r in identified} == set(OPPS)
    assert all("opportunity_id" not in r.to_public() for r in BenchmarkValue.objects.filter(publication=pub))


def test_an_opportunity_outside_the_cohort_is_never_published():
    """The snapshot spans the whole report; only cohort members may be benchmarked."""
    pub = _publish(_cohort(members=OPPS[:5]))
    published = {r.opportunity_id for r in BenchmarkValue.objects.filter(publication=pub).with_source()}
    assert OPPS[5] not in published, "a non-member opportunity was benchmarked"


def test_a_cohort_too_small_after_the_rules_publishes_nothing():
    """Observations existed -- the RULES withheld them -- so this is a legitimate
    empty publication, not the shape failure above."""
    pub = _publish(_cohort(members=OPPS[:3]))
    assert BenchmarkValue.objects.filter(publication=pub).count() == 0


def test_republishing_leaves_the_first_publications_values_untouched():
    """Immutability means re-publishing creates a SEPARATE, unmodified record --
    not merely that Django handed back a different pk (which `objects.create`
    guarantees unconditionally and can't fail to do)."""
    cohort = _cohort()
    fields = ("series", "indicator_id", "period", "peer_index", "value", "opportunity_id")
    first = _publish(cohort)
    first_values_before = set(BenchmarkValue.objects.filter(publication=first).values_list(*fields))
    second = _publish(cohort)

    assert first.pk != second.pk
    assert BenchmarkPublication.objects.count() == 2
    first_values_after = set(BenchmarkValue.objects.filter(publication=first).values_list(*fields))
    assert first_values_after == first_values_before
    expected = 2 * len(OPPS) + len(OPPS) * len(MONTHS)
    assert BenchmarkValue.objects.filter(publication=first).count() == expected
    assert BenchmarkValue.objects.filter(publication=second).count() == expected


def test_an_indicator_present_only_in_the_series_scope_is_still_published():
    """An indicator can be absent from the run being published and present in the
    earlier ones the series is drawn from. Iterating just the snapshot's keys
    would silently skip it -- zero rows, no exception, no log."""
    snapshot = build_snapshot()
    for entry in snapshot["byOpp"]:
        entry["ind"].pop("C15")
    pub = _publish(_cohort(), snapshot=snapshot)
    series = BenchmarkValue.objects.filter(publication=pub, indicator_id="C15", period__isnull=False)
    assert series.count() == len(OPPS) * 2
    assert BenchmarkValue.objects.filter(publication=pub, indicator_id="C15", period__isnull=True).count() == 0


def test_tie_salt_varies_per_indicator(monkeypatch):
    """tie_salt is what stops tied peers holding the same index in every indicator.

    disclosure.py can only reject an EMPTY salt -- it has no way to know whether
    the salt actually differs across indicators within one publication. That
    contract belongs to the publisher, so it must be tested here: capture every
    tie_salt the publisher actually passes to anonymise_point AND
    anonymise_series, and assert there is one distinct value per indicator per
    call site -- not one constant value reused.

    Both call sites are spied on: a constant salt on the SERIES call
    (anonymise_series) reinstates the cross-indicator linkage attack on peer
    LINES, which disclosure.py documents as the higher residual re-identification
    risk -- a spy on anonymise_point alone would never see that regression.
    """
    point_salts = []
    series_salts = []
    real_anonymise_point = publish_module.anonymise_point
    real_anonymise_series = publish_module.anonymise_series

    def _point_spy(observations, *, min_peers, min_denominator, tie_salt):
        point_salts.append(tie_salt)
        return real_anonymise_point(
            observations, min_peers=min_peers, min_denominator=min_denominator, tie_salt=tie_salt
        )

    def _series_spy(observations_by_period, *, min_peers, min_denominator, tie_salt, **kw):
        series_salts.append(tie_salt)
        return real_anonymise_series(
            observations_by_period, min_peers=min_peers, min_denominator=min_denominator, tie_salt=tie_salt
        )

    monkeypatch.setattr(publish_module, "anonymise_point", _point_spy)
    monkeypatch.setattr(publish_module, "anonymise_series", _series_spy)

    # Two rate indicators in the PRIMARY family, so both have point and monthly
    # data -- each call site must have received one distinct salt per indicator.
    _publish(_cohort(), snapshot=build_snapshot(c_ids=("C14", "C15"), n_ids=()))

    assert len(point_salts) == 2
    assert len(set(point_salts)) == 2
    assert len(series_salts) == 2
    assert len(set(series_salts)) == 2


def test_a_failure_mid_publication_leaves_nothing_committed():
    """publish_benchmark is @transaction.atomic: a publication is complete or
    absent, never half-written. Force the ValueError disclosure.py raises on a
    duplicate opportunity inside the SECOND indicator's rows -- the first
    indicator's rows must not survive either."""
    snapshot = build_snapshot()
    snapshot["byOpp"].append(dict(snapshot["byOpp"][0]))  # one opportunity, twice
    with pytest.raises(ValueError):
        _publish(_cohort(), snapshot=snapshot)
    assert BenchmarkPublication.objects.count() == 0
    assert BenchmarkValue.objects.count() == 0


class TestSeriesRunOnEachOpportunitysOwnTenure:
    """A series period is a TENURE WEEK — that opportunity's own Nth week of
    delivering — not the report date and not the report's ordinal."""

    def _publish_staggered(self):
        """Six opportunities joining at different points across six reports."""
        cohort = _cohort()
        first = {opp: i for i, opp in enumerate(OPPS)}
        return _publish(cohort, history=_history(reports=6, first_report=first)), first

    def test_periods_are_tenure_weeks_not_dates(self):
        pub, _ = self._publish_staggered()
        periods = {v.period for v in BenchmarkValue.objects.filter(publication=pub) if v.period}
        assert periods, "no series was published at all"
        assert all(p.startswith("W") for p in periods), f"a report date leaked into the periods: {periods}"

    def test_no_date_is_recoverable_from_a_published_period(self):
        """A period naming a real date would say when an opportunity joined, and
        that identifies it."""
        pub, _ = self._publish_staggered()
        periods = {v.period for v in BenchmarkValue.objects.filter(publication=pub) if v.period}
        assert not any("2026" in p for p in periods)

    def test_a_peer_index_denotes_the_same_peer_in_every_period(self):
        """What makes the points joinable into a line at all."""
        pub, _ = self._publish_staggered()
        by_period = {}
        for v in BenchmarkValue.objects.filter(publication=pub).exclude(period=None):
            by_period.setdefault(v.period, {})[v.peer_index] = v.opportunity_id
        assert len(by_period) > 1
        first = by_period[sorted(by_period, key=lambda p: int(p[1:]))[0]]
        for period, mapping in by_period.items():
            for idx, opp in mapping.items():
                assert first.get(idx) == opp, f"peer_index {idx} changed meaning at {period}"

    def test_two_opportunities_that_started_months_apart_meet_at_the_same_week(self):
        """The whole point of the axis: two DIFFERENT report dates land on the
        same period, because that is the same week of delivering for each."""
        snapshot = build_snapshot()
        early, late = OPPS[:3], OPPS[3:]
        snapshot["weekly"] = {f"opp:{o}": [{"week": "2026-01-05"}] for o in early}
        snapshot["weekly"].update({f"opp:{o}": [{"week": "2026-03-02"}] for o in late})
        snapshot["monthlyByScope"] = {}  # weekly is the precise origin; drop the fallback
        pub = _publish(_cohort(), snapshot=snapshot, history=_history(dates=["2026-01-12", "2026-03-09"]))
        weeks = {}
        for v in BenchmarkValue.objects.filter(publication=pub, indicator_id="C15").exclude(period=None):
            weeks.setdefault(v.period, set()).add(v.opportunity_id)
        # Each group reached week 1 on a different date: the January starters at
        # the 12 Jan report, the March starters at the 9 Mar one. Both are W1.
        assert set(weeks) == {"W1"}, f"tenure did not align the two groups: {weeks}"
        assert weeks["W1"] == set(OPPS)
        # And the 9 Mar report is week 9 for the January starters — three peers,
        # below min_peers, so R5 withholds it rather than publishing a thin period.
        assert "W9" not in weeks

    def test_a_finished_opportunitys_repeated_figure_is_not_drawn_as_its_first_weeks(self):
        """THE DEFECT THIS AXIS EXISTS FOR. An opportunity that stopped
        delivering before the reporting window still scores in every report,
        repeating its final figure. Indexed on reports, those repeats were
        placed at R0, R1, R2 — the same x as a live opportunity's first three
        weeks — which is how three dead opportunities came to be drawn as flat
        lines along the whole axis of a live one."""
        cohort = _cohort()
        # Every opportunity starts 2026-01-01 in the fixture; report a year on.
        pub = _publish(cohort, history=_history(dates=["2027-01-04", "2027-01-11", "2027-01-18"]))
        periods = {v.period for v in BenchmarkValue.objects.filter(publication=pub) if v.period}
        assert periods, "nothing published"
        assert min(int(p[1:]) for p in periods) > 40, f"a year-old report was placed near week 0: {periods}"

    def test_a_report_older_than_the_opportunity_is_dropped_not_negative(self):
        """It is not that opportunity's week -1; it is nothing."""
        cohort = _cohort()
        pub = _publish(cohort, history=_history(dates=["2025-06-30", "2026-01-28", "2026-02-28"]))
        periods = {v.period for v in BenchmarkValue.objects.filter(publication=pub) if v.period}
        assert periods == {"W3", "W8"}, f"a pre-start report survived: {periods}"

    def test_two_reports_in_one_tenure_week_keep_the_later_one(self):
        """A hand-saved run beside a rebuilt one. Both kept would give one peer
        two points at one x, sharing a peer_index.

        The history here is deliberately NOT in date order, which is the only
        arrangement that tells "the later report wins" apart from "whichever was
        seen last wins" — a dict assignment does the second for free."""
        cohort = _cohort()
        # Report 0 is 28 Jan (values 40+i), report 1 is 26 Jan (values 41+i).
        # Both are week 3; the 28th is the later one, so 40+i must survive.
        pub = _publish(cohort, history=_history(dates=["2026-01-28", "2026-01-26", "2026-02-28"]))
        rows = BenchmarkValue.objects.filter(publication=pub, indicator_id="C15").exclude(period=None)
        seen = set()
        for row in rows:
            key = (row.period, row.opportunity_id)
            assert key not in seen, f"{row.opportunity_id} contributed twice to {row.period}"
            seen.add(key)
        week3 = set(rows.filter(period="W3").values_list("value", flat=True))
        assert week3 == {40.0 + i for i in range(len(OPPS))}, f"the earlier report won week 3: {sorted(week3)}"

    def test_an_opportunity_with_no_recorded_activity_contributes_no_series(self):
        """Fail closed: there is no honest origin to place it on, and inventing
        one would put it at week 0 beside genuinely new peers."""
        snapshot = build_snapshot()
        snapshot["weekly"] = {}
        snapshot["monthlyByScope"] = {}
        assert publish_module.opportunity_starts(snapshot) == {}
        pub = _publish(_cohort(), snapshot=snapshot)
        assert not BenchmarkValue.objects.filter(publication=pub).exclude(period=None).exists()
        assert BenchmarkValue.objects.filter(publication=pub, period=None).exists(), "the points went too"


class TestTheTenureOriginIsTheOpportunitysOwnFirstActivity:
    """Not the first report it scored in — see `opportunity_starts`."""

    def test_the_weekly_scope_gives_week_precision(self):
        snapshot = {"weekly": {"opp:500": [{"week": "2026-03-16"}, {"week": "2026-03-09"}]}}
        assert publish_module.opportunity_starts(snapshot) == {500: dt.date(2026, 3, 9)}

    def test_the_monthly_scope_is_the_fallback_when_a_run_carried_no_visits(self):
        snapshot = {"monthlyByScope": {"opp:500": [{"month": "2026-02"}, {"month": "2026-05"}]}}
        assert publish_module.opportunity_starts(snapshot) == {500: dt.date(2026, 2, 1)}

    def test_whichever_scope_reaches_further_back_wins(self):
        snapshot = {
            "weekly": {"opp:500": [{"week": "2026-03-09"}]},
            "monthlyByScope": {"opp:500": [{"month": "2026-01"}]},
        }
        assert publish_module.opportunity_starts(snapshot) == {500: dt.date(2026, 1, 1)}

    def test_non_opportunity_scopes_are_ignored(self):
        """`all` and `llo:` sit in the same dicts and are not opportunities."""
        snapshot = {"weekly": {"all": [{"week": "2020-01-06"}], "llo:One": [{"week": "2020-01-06"}]}}
        assert publish_module.opportunity_starts(snapshot) == {}


class TestAScorecardFamilyCanBeTrendedToo:
    """The series is drawn per FAMILY from the history, so a scorecard indicator
    trends exactly as a headline one does."""

    def test_a_scorecard_indicator_publishes_a_series(self):
        pub = _publish(_cohort(), history=_history(indicator_ids=("N08",), series="N", reports=3))
        series = BenchmarkValue.objects.filter(publication=pub, series="N").exclude(period=None)
        assert series.exists(), "the scorecard family published points but no series"
        assert set(series.values_list("period", flat=True)) == {"W3", "W8", "W12"}

    def test_no_history_means_points_and_no_series(self):
        """Degrade, never fail."""
        pub = _publish(_cohort(), history=None)
        vals = BenchmarkValue.objects.filter(publication=pub)
        assert vals.filter(period=None).exists(), "the points went missing too"
        assert not vals.exclude(period=None).exists()


class TestRelaxingR6:
    """R6 keeps only peers present in every period. That is the safer default and
    it is also what leaves a real cohort with no series at all, because members
    join at different times."""

    def _publish(self, require_complete):
        cohort = BenchmarkCohort.objects.create(
            name="R6", organization_id="dimagi-kmc", require_complete_series=require_complete
        )
        for opp in OPPS:
            cohort.members.create(opportunity_id=opp)
        return _publish(cohort, history=_history(reports=6, first_report={opp: i for i, opp in enumerate(OPPS)}))

    def test_on_by_default_a_late_joiner_is_dropped(self):
        assert BenchmarkCohort().require_complete_series is True
        pub = self._publish(True)
        latest = OPPS[-1]  # joined at the last report, so it has one point
        assert not BenchmarkValue.objects.filter(publication=pub, opportunity_id=latest).exclude(period=None).exists()

    def test_off_a_late_joiner_contributes_at_the_weeks_it_has(self):
        pub = self._publish(False)
        rows = BenchmarkValue.objects.filter(publication=pub, opportunity_id=OPPS[-1]).exclude(period=None)
        assert {r.period for r in rows}, "the late joiner still did not contribute"

    def test_off_does_not_publish_a_period_too_few_peers_reached(self):
        """R5 still holds — relaxing R6 must not smuggle a thin period through."""
        pub = self._publish(False)
        by_period = {}
        for v in BenchmarkValue.objects.filter(publication=pub).exclude(period=None):
            by_period.setdefault(v.period, set()).add(v.opportunity_id)
        assert by_period, "nothing published at all"
        assert all(len(o) >= 3 for o in by_period.values()), by_period


class TestBenchmarkableIsAPropertyOfTheIndicator:
    """Which indicators may be benchmarked is decided in the REGISTRY, per
    indicator, not inferred from the unit — `unit: n` covers both a count (which
    re-identifies an opportunity instantly) and a mean (which does not), and
    `g/kg/d` is no more dangerous than `%`."""

    @staticmethod
    def _cat(*specs):
        return [
            {"indicator": ind, "unit": unit, "kind": kind, "benchmarkable": flag} for ind, unit, kind, flag in specs
        ]

    def test_a_declared_mean_is_publishable_even_though_it_is_not_a_rate(self):
        cat = self._cat(("C13", "g/kg/d", "mean", True), ("C15", "%", None, None))
        assert "C13" in resolve_benchmarkable_ids(cat)

    def test_declaring_one_indicator_does_not_withhold_every_other(self):
        """The unit rule is the floor and a declaration adjusts it. Treated as a
        pure allow-list, adding `benchmarkable: true` to a single indicator
        would silently stop publishing every rate in the registry."""
        cat = self._cat(("C13", "g/kg/d", "mean", True), ("C15", "%", None, None))
        assert resolve_benchmarkable_ids(cat) == {"C13", "C15"}

    def test_a_rate_can_be_withheld_by_declaring_it_false(self):
        cat = self._cat(("C15", "%", None, False), ("C16", "%", None, None))
        assert resolve_benchmarkable_ids(cat) == {"C16"}

    def test_a_registry_that_declares_nothing_is_exactly_the_old_unit_rule(self):
        cat = self._cat(("C13", "g/kg/d", "mean", None), ("C15", "%", None, None))
        assert resolve_benchmarkable_ids(cat) == {"C15"}

    def test_a_count_is_refused_even_when_it_declares_itself_benchmarkable(self):
        """The one rule a registry edit must not be able to switch off. The
        registry is editable with no deploy; opportunity sizes are visible on
        the programme report, so a published count names the opportunity."""
        cat = self._cat(("C01", "n", "count", True), ("C13", "g/kg/d", "mean", True))
        out = resolve_benchmarkable_ids(cat)
        assert "C01" not in out
        assert "C13" in out

    def test_a_count_is_refused_under_the_fallback_too(self):
        cat = self._cat(("N01", "%", "count", None), ("C15", "%", None, None))
        assert resolve_benchmarkable_ids(cat) == {"C15"}


def test_the_real_kmc_registry_now_benchmarks_the_growth_rate():
    """End to end over the REAL catalog, not a fixture: C13 is declared and
    reaches the allow-list, and the counts stay out."""
    cat = _catalog("C", ("C13", "C01", "C15"))
    out = resolve_benchmarkable_ids(cat)
    assert "C13" in out, "the registry declaration did not reach the publisher"
    assert "C01" not in out, "a case count became benchmarkable"


def test_a_declared_indicator_reaches_the_PUBLISHER_not_just_the_helper():
    """Through `publish_benchmark`, not the resolver alone. The resolver was
    first named `benchmarkable_indicator_ids`, which is also the name of a
    `publish_benchmark` PARAMETER — so inside the publisher the parameter
    shadowed it, the fallback called None, and every publication raised. The
    unit tests above call the resolver directly and cannot see that."""
    pub = _publish(
        _cohort(),
        snapshot=build_snapshot(c_ids=("C13", "C01", "C15")),
        history=_history(indicator_ids=("C13",)),
    )
    published = set(BenchmarkValue.objects.filter(publication=pub).values_list("indicator_id", flat=True))
    assert "C13" in published, "the registry's declaration did not survive the publisher"
    assert "C01" not in published, "a case count was published"


class TestTheCountGuardSurvivesAnExplicitOverride:
    """`benchmarkable_indicator_ids` exists so a caller can decide indicator by
    indicator. It must not become the way to publish case counts — the one thing
    the disclosure design exists to prevent."""

    def test_an_override_naming_a_count_does_not_publish_it(self):
        pub = _publish(
            _cohort(),
            snapshot=build_snapshot(c_ids=("C01", "C15")),
            history=_history(indicator_ids=("C01", "C15")),
            benchmarkable_indicator_ids={"C01", "C15"},
        )
        published = set(BenchmarkValue.objects.filter(publication=pub).values_list("indicator_id", flat=True))
        assert "C01" not in published, "an explicit override published a case count"
        assert "C15" in published, "the override did not widen anything at all"

    def test_an_override_can_still_add_a_non_rate(self):
        """The whole point of the override: publish a mean the unit rule missed,
        without editing a shared registry."""
        pub = _publish(
            _cohort(),
            snapshot=build_snapshot(c_ids=("C13", "C15")),
            history=_history(indicator_ids=("C13",)),
            benchmarkable_indicator_ids={"C13"},
        )
        published = set(BenchmarkValue.objects.filter(publication=pub).values_list("indicator_id", flat=True))
        assert "C13" in published


def test_the_primary_family_is_named_by_its_catalog_not_by_its_ids():
    """Slug ids carry no family in their letters; the catalog entry does."""
    measures = [{"indicator": "mortality", "series": "KMC"}, {"indicator": "pct_slow_growth", "series": "KMC"}]
    assert publish_module._primary_series_name(measures) == "KMC"
    # a catalog frozen before entries stated their family still resolves by prefix
    assert publish_module._primary_series_name([{"indicator": "C14"}, {"indicator": "C15"}]) == "C"
