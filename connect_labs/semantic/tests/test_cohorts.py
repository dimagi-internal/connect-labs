"""Cohort assignment and the stats a worker's peer comparison rests on."""

from connect_labs.semantic import cohorts


class TestStartMonth:
    def test_takes_the_earliest_not_the_first_listed(self):
        # Ordered latest-first, so a function returning cases[0] passes only by luck.
        cases = [
            {"first_visit_date": "2026-05-20"},
            {"first_visit_date": "2026-01-09"},
            {"first_visit_date": "2026-03-02"},
        ]
        assert cohorts.start_month(cases) == "2026-01"

    def test_falls_back_to_reg_date_only_when_no_visit_date(self):
        # The visit date is EARLIER here, so a function preferring reg_date
        # unconditionally returns 2026-08 and fails.
        cases = [{"reg_date": "2026-08-01", "first_visit_date": "2026-02-11"}]
        assert cohorts.start_month(cases) == "2026-02"

    def test_reg_date_used_when_case_was_never_visited(self):
        assert cohorts.start_month([{"reg_date": "2026-04-30"}]) == "2026-04"

    def test_malformed_date_is_ignored_not_truncated(self):
        # "2026" truncated to 7 chars is "2026" -- shorter than YYYY-MM. Were it
        # accepted it would sort BELOW "2026-06" and win, returning "2026".
        cases = [{"first_visit_date": "2026"}, {"first_visit_date": "2026-06-04"}]
        assert cohorts.start_month(cases) == "2026-06"

    def test_no_dates_at_all_is_none(self):
        assert cohorts.start_month([{"entity_id": "x"}]) is None
        assert cohorts.start_month([]) is None


class TestQuantileEdges:
    def test_three_bands_split_a_uniform_population_evenly(self):
        edges = cohorts.quantile_edges(list(range(1, 10)), bands=3)
        assert len(edges) == 2
        placed = [cohorts.band_index(v, edges) for v in range(1, 10)]
        assert placed.count(0) == 3 and placed.count(1) == 3 and placed.count(2) == 3

    def test_refuses_when_population_is_smaller_than_the_band_count(self):
        assert cohorts.quantile_edges([5, 9], bands=3) == []

    def test_a_tie_spanning_an_edge_keeps_equal_values_together(self):
        # Two thirds of the population carry exactly 40. Whatever the edges,
        # every 40 must land in the same band -- splitting identical caseloads
        # across cohorts is the one outcome that is never defensible.
        vals = [10, 40, 40, 40, 40, 40, 40, 90, 95]
        edges = cohorts.quantile_edges(vals, bands=3)
        bands_for_40 = {cohorts.band_index(40, edges)}
        assert len(bands_for_40) == 1


class TestCohortKey:
    def test_unplaceable_worker_is_excluded_not_pooled(self):
        # None, never a catch-all key: a bucket of workers who share only the
        # fact that we know nothing about them is not a peer group.
        assert cohorts.cohort_key({"opp": None}, "opportunity") is None
        assert cohorts.cohort_key({"startMonth": None}, "start_month") is None
        assert cohorts.cohort_key({"n": None}, "caseload", edges=[10, 20]) is None

    def test_distinct_dimensions_never_collide(self):
        flw = {"opp": 3, "startMonth": "2026-03", "n": 3}
        keys = {cohorts.cohort_key(flw, d, edges=[10, 20]) for d in cohorts.DIMENSIONS}
        assert len(keys) == 3

    def test_caseload_key_tracks_the_band_not_the_raw_count(self):
        edges = [10, 20]
        a = cohorts.cohort_key({"n": 11}, "caseload", edges=edges)
        b = cohorts.cohort_key({"n": 19}, "caseload", edges=edges)
        c = cohorts.cohort_key({"n": 21}, "caseload", edges=edges)
        assert a == b and a != c

    def test_unknown_dimension_raises(self):
        try:
            cohorts.cohort_key({}, "astrological_sign")
        except ValueError:
            return
        raise AssertionError("expected ValueError")


class TestPercentileRank:
    def test_strictly_below_so_an_all_tie_reads_zero(self):
        assert cohorts.percentile_rank([5, 5, 5, 5], 5) == 0.0

    def test_top_of_the_cohort(self):
        assert cohorts.percentile_rank([1, 2, 3, 9], 9) == 75.0

    def test_empty_cohort_is_none(self):
        assert cohorts.percentile_rank([], 1) is None


class TestSummarise:
    def test_refuses_a_cohort_under_the_floor(self):
        assert cohorts.summarise([1, 2, 3], 2, min_cohort=8) is None

    def test_reports_rank_and_spread_over_a_real_cohort(self):
        vals = list(range(0, 100))
        out = cohorts.summarise(vals, 25)
        assert out["n"] == 100
        assert out["rank"] == 25.0
        assert out["min"] == 0 and out["max"] == 99
        assert out["q1"] == 25 and out["median"] == 50 and out["q3"] == 75

    def test_the_subject_need_not_be_a_member_of_the_cohort(self):
        # Comparing a worker against a cohort they are excluded from (e.g. their
        # own opportunity removed) must still rank them.
        assert cohorts.summarise(list(range(10, 30)), 5)["rank"] == 0.0


class TestHistogram:
    def test_counts_sum_to_the_population_after_merging(self):
        vals = [1] * 20 + [50] + [99] * 20  # 50 is a lone outlier in the middle
        bins = cohorts.histogram(vals, bins=10, min_bin=5)
        assert sum(b["count"] for b in bins) == len(vals)

    def test_no_surviving_bin_is_under_the_floor(self):
        vals = [1] * 20 + [50] + [99] * 20
        bins = cohorts.histogram(vals, bins=10, min_bin=5)
        assert not [b for b in bins if 0 < b["count"] < 5]

    def test_an_empty_gap_is_preserved_not_merged_away(self):
        # Nothing between 20 and 80. A merge across the gap would invent a
        # population there; the zero-count bands are the true statement.
        vals = [1] * 10 + [2] * 10 + [98] * 10 + [99] * 10
        bins = cohorts.histogram(vals, bins=10, min_bin=5)
        assert [b for b in bins if b["count"] == 0]

    def test_refuses_a_population_under_the_floor(self):
        assert cohorts.histogram([1, 2, 3], bins=10, min_total=8) is None

    def test_a_single_repeated_value_is_one_band(self):
        bins = cohorts.histogram([7] * 12, bins=10)
        assert bins == [{"lo": 7, "hi": 7, "count": 12}]


class TestSnapshotCarriesStartMonth:
    """The cohort key has to survive into the payload, or no consumer can use it."""

    @staticmethod
    def _snapshot():
        from connect_labs.semantic import snapshot as semantic_snapshot

        measures = [{"indicator": "C01", "id": "c01", "unit": "%", "bands": {}, "direction": "up"}]
        rows = [
            {"scope": "flw", "opportunity_id": 501, "username": "ama", "n_cases": 30, "c01": 50.0, "c01_d": 30},
            {"scope": "flw", "opportunity_id": 501, "username": "bode", "n_cases": 12, "c01": 60.0, "c01_d": 12},
            # A worker with no dated cases at all -- must not invent a month.
            {"scope": "flw", "opportunity_id": 502, "username": "chidi", "n_cases": 5, "c01": 70.0, "c01_d": 5},
        ]
        cases = [
            # ama: earliest is March, listed AFTER a later case on purpose.
            {"opportunity_id": 501, "username": "ama", "first_visit_date": "2026-07-02"},
            {"opportunity_id": 501, "username": "ama", "first_visit_date": "2026-03-19"},
            # bode: registered in Jan, never visited -- reg_date carries it.
            {"opportunity_id": 501, "username": "bode", "reg_date": "2026-01-05"},
            {"opportunity_id": 502, "username": "chidi"},
        ]
        payload = semantic_snapshot.build(
            spec={},
            rows=rows,
            measures=measures,
            deployment={"llo_map": {501: "LLO One", 502: "LLO Two"}, "app_asks": {}, "asks_as": {}, "settings": {}},
            cases=cases,
            as_of="2026-09-11",
        )
        return {f["flw"]: f for f in payload["byFLW"]}

    def test_each_worker_gets_the_month_they_first_appeared(self):
        by = self._snapshot()
        assert by["ama"]["startMonth"] == "2026-03"

    def test_a_worker_who_only_registered_cases_still_gets_a_month(self):
        assert self._snapshot()["bode"]["startMonth"] == "2026-01"

    def test_a_worker_with_no_dates_is_left_unplaceable(self):
        # None, not a fabricated month and not the run's as_of date.
        assert self._snapshot()["chidi"]["startMonth"] is None

    def test_the_month_belongs_to_the_worker_not_the_cohort(self):
        # Two workers in the SAME opportunity with different start months. A
        # builder that computed one month per opportunity would collapse these.
        by = self._snapshot()
        assert by["ama"]["startMonth"] != by["bode"]["startMonth"]


class TestSnapshotAssignsCaseloadBands:
    """Banding is frozen in the payload so no two readers band differently."""

    @staticmethod
    def _payload(counts):
        from connect_labs.semantic import snapshot as semantic_snapshot

        measures = [{"indicator": "C01", "id": "c01", "unit": "%", "bands": {}, "direction": "up"}]
        rows = [
            {"scope": "flw", "opportunity_id": 501, "username": f"w{i}", "n_cases": n, "c01": 50.0, "c01_d": n}
            for i, n in enumerate(counts)
        ]
        return semantic_snapshot.build(
            spec={},
            rows=rows,
            measures=measures,
            deployment={"llo_map": {501: "LLO One"}, "app_asks": {}, "asks_as": {}, "settings": {}},
            cases=[],
            as_of="2026-09-11",
        )

    def test_workers_are_spread_across_the_bands(self):
        p = self._payload(list(range(1, 31)))
        bands = sorted({f["caseloadBand"] for f in p["byFLW"]})
        assert bands == [0, 1, 2]

    def test_the_heaviest_worker_is_not_in_the_lightest_band(self):
        p = self._payload(list(range(1, 31)))
        by_n = {f["n"]: f for f in p["byFLW"]}
        assert by_n[30]["caseloadBand"] == 2
        assert by_n[1]["caseloadBand"] == 0
        assert by_n[30]["caseloadLabel"] == "heaviest"

    def test_the_cut_points_travel_with_the_payload(self):
        p = self._payload(list(range(1, 31)))
        assert len(p["cohortEdges"]["caseload"]) == 2
        assert p["cohortEdges"]["caseload"] == sorted(p["cohortEdges"]["caseload"])

    def test_too_few_workers_to_band_leaves_every_worker_unbanded(self):
        # Two workers cannot be split into three bands. Unbanded, not all-band-0:
        # a band that everyone shares is not a peer group.
        p = self._payload([4, 9])
        assert p["cohortEdges"]["caseload"] == []
        assert all(f["caseloadBand"] is None for f in p["byFLW"])
