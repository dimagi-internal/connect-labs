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

import pytest

from connect_labs.benchmarks import publish as publish_module
from connect_labs.benchmarks.models import BenchmarkCohort, BenchmarkPublication, BenchmarkValue
from connect_labs.benchmarks.publish import SnapshotShapeError, publish_benchmark
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


def _publish(cohort, snapshot=None, **kwargs):
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
    # C15 and N08 have a point each per opportunity; only the PRIMARY family has
    # monthly data (`monthlyByScope` is graded with the primary catalog alone).
    assert points.count() == 2 * len(OPPS)
    assert series.count() == len(OPPS) * len(MONTHS)
    assert set(series.values_list("indicator_id", flat=True)) == {"C15"}
    # Tenure offsets, not the calendar months the snapshot carried: every
    # opportunity in this fixture starts in the same month, so its M0 is
    # MONTHS[0] and its M1 is MONTHS[1]. See TestSeriesRunOnTenureNotCalendar
    # for why the axis is re-based off the calendar.
    assert set(series.values_list("period", flat=True)) == {f"M{i}" for i in range(len(MONTHS))}


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
    """An indicator can be absent from the point scope and present in the monthly
    one. Iterating just the point scope's keys would silently skip it -- zero
    rows, no exception, no log."""
    snapshot = build_snapshot()
    for entry in snapshot["byOpp"]:
        entry["ind"].pop("C15")
    pub = _publish(_cohort(), snapshot=snapshot)
    series = BenchmarkValue.objects.filter(publication=pub, indicator_id="C15", period__isnull=False)
    assert series.count() == len(OPPS) * len(MONTHS)
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

    def _series_spy(observations_by_period, *, min_peers, min_denominator, tie_salt):
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


class TestSeriesRunOnTenureNotCalendar:
    """A series period is months since THAT opportunity's own first month."""

    @staticmethod
    def _staggered_snapshot():
        """Six opportunities, each starting a month after the last, three months
        of data each. On a calendar axis they overlap barely; on a tenure axis
        every one of them has an M0, M1 and M2."""
        c = _catalog("C", ("C15",))
        rows = []
        for i, opp in enumerate(OPPS):
            rows.append({"scope": "opportunity", "opportunity_id": opp, "n_cases": 100, **_columns(c, i)})
            for j in range(3):
                rows.append(
                    {
                        "scope": "opportunity_month",
                        "opportunity_id": opp,
                        # opp 0 starts 2026-01, opp 1 starts 2026-02, ...
                        "cohort_month": f"2026-{i + j + 1:02d}-01",
                        "n_cases": 100,
                        **_columns(c, i + j),
                    }
                )
        return semantic_snapshot.build(
            spec={},
            rows=rows,
            measures=c,
            deployment={"llo_map": {o: "LLO One" for o in OPPS}, "app_asks": {}, "asks_as": {}, "settings": {}},
            as_of="2026-09-11",
        )

    def _publish(self):
        cohort = BenchmarkCohort.objects.create(name="Staggered", organization_id="dimagi-kmc")
        for opp in OPPS:
            cohort.members.create(opportunity_id=opp)
        publish_benchmark(
            cohort,
            snapshot=self._staggered_snapshot(),
            source_workflow_id=1,
            source_run_id=2,
            registry_id=3,
            as_of="2026-09-11",
        )
        return cohort

    def test_periods_are_tenure_offsets_not_calendar_months(self):
        cohort = self._publish()
        periods = {
            v.period for v in BenchmarkValue.objects.filter(publication=cohort.publications.first()) if v.period
        }
        assert periods, "no series was published at all"
        assert all(p.startswith("M") for p in periods), f"a calendar month leaked into the periods: {periods}"

    def test_no_calendar_month_is_recoverable_from_a_published_period(self):
        """A period that named a real month would say when an opportunity began,
        and a start date identifies it."""
        cohort = self._publish()
        periods = {
            v.period for v in BenchmarkValue.objects.filter(publication=cohort.publications.first()) if v.period
        }
        assert not any("2026" in p for p in periods)

    def test_staggered_starts_still_share_a_window(self):
        """The point of re-basing. Every opportunity has three months of data, so
        all three tenure periods clear R6 — where on a calendar axis the six
        start months would have thinned the common window to nothing."""
        cohort = self._publish()
        periods = {
            v.period for v in BenchmarkValue.objects.filter(publication=cohort.publications.first()) if v.period
        }
        assert periods == {"M0", "M1", "M2"}, periods

    def test_a_peer_index_denotes_the_same_peer_in_every_period(self):
        """What makes the points joinable into a line at all."""
        cohort = self._publish()
        vals = BenchmarkValue.objects.filter(publication=cohort.publications.first()).exclude(period=None)
        by_period = {}
        for v in vals:
            by_period.setdefault(v.period, {})[v.peer_index] = v.opportunity_id
        assert len(by_period) > 1
        first = by_period["M0"]
        for period, mapping in by_period.items():
            assert mapping == first, f"peer_index changed meaning between M0 and {period}"
