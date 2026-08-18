"""Source-structure profiling for high-fidelity 'close mirror' cloning (issue #713).

Mirror mode reproduces a source opp's *structure* exactly — visits-per-case and
cases-per-FLW ratios — rather than re-sampling from fitted summary statistics.
These tests pin the empirical extraction that feeds that reproduction.
"""

from __future__ import annotations

from connect_labs.labs.synthetic.generator.fixtures.mirror import profile_entity_structure


def _visit(entity_id: str, username: str, date: str, **form) -> dict:
    return {"entity_id": entity_id, "username": username, "visit_date": date, "form_json": form}


def test_visits_per_entity_is_the_exact_empirical_histogram():
    # e1 -> 3 visits, e2 -> 1 visit, e3 -> 3 visits: two 3-visit cases, one 1-visit case.
    visits = [
        _visit("e1", "flwA", "2026-01-01"),
        _visit("e1", "flwA", "2026-01-08"),
        _visit("e1", "flwA", "2026-01-15"),
        _visit("e2", "flwA", "2026-01-02"),
        _visit("e3", "flwB", "2026-01-03"),
        _visit("e3", "flwB", "2026-01-10"),
        _visit("e3", "flwB", "2026-01-17"),
    ]

    struct = profile_entity_structure(visits)

    assert struct.visits_per_entity == {3: 2, 1: 1}


def test_entity_owner_is_the_flw_with_the_most_visits_to_it():
    visits = [
        _visit("e1", "flwA", "2026-01-01"),
        _visit("e1", "flwA", "2026-01-08"),
        _visit("e1", "flwB", "2026-01-15"),  # flwA: 2 visits, flwB: 1 -> flwA owns e1
    ]

    struct = profile_entity_structure(visits)

    assert struct.entity_owner == {"e1": "flwA"}


def test_entity_owner_ties_break_on_username_for_determinism():
    visits = [
        _visit("e1", "flwB", "2026-01-01"),
        _visit("e1", "flwA", "2026-01-08"),  # 1 each -> tie broken to the lower username
    ]

    struct = profile_entity_structure(visits)

    assert struct.entity_owner == {"e1": "flwA"}


def test_owner_visit_counts_capture_cases_per_flw_jointly_with_visits_per_case():
    # flwA owns a 3-visit case and a 1-visit case; flwB owns a 3-visit case.
    visits = [
        _visit("e1", "flwA", "2026-01-01"),
        _visit("e1", "flwA", "2026-01-08"),
        _visit("e1", "flwA", "2026-01-15"),
        _visit("e2", "flwA", "2026-01-02"),
        _visit("e3", "flwB", "2026-01-03"),
        _visit("e3", "flwB", "2026-01-10"),
        _visit("e3", "flwB", "2026-01-17"),
    ]

    struct = profile_entity_structure(visits)

    # username -> sorted visit-counts of the entities it owns. Reproduces both
    # cases-per-FLW (list length) and visits-per-case (the counts) exactly.
    assert struct.owner_visit_counts == {"flwA": [1, 3], "flwB": [3]}


def test_transplant_pool_carries_owner_start_date_and_ordered_day_offsets():
    # Deliberately out of date order; each series must sort by date, record its
    # owner FLW and absolute first-visit date (for exact cases/FLW + timing), and
    # carry the day offset from that entity's first visit (its relative time axis).
    visits = [
        _visit("e1", "flwA", "2026-01-15", weight=1400, age=20),
        _visit("e1", "flwA", "2026-01-01", weight=1200, age=6),
        _visit("e1", "flwA", "2026-01-08", weight=1300, age=13),
    ]

    struct = profile_entity_structure(visits)

    assert struct.transplant_pool == [
        {
            "owner": "flwA",
            "start_date": "2026-01-01",
            "visits": [
                {"day": 0, "values": {"weight": 1200.0, "age": 6.0}},
                {"day": 7, "values": {"weight": 1300.0, "age": 13.0}},
                {"day": 14, "values": {"weight": 1400.0, "age": 20.0}},
            ],
        }
    ]


def test_transplant_pool_carries_numerics_only_not_identifiers_or_text():
    # De-identification: names/phones/free text must never leave the source.
    visits = [
        _visit("e1", "flwA", "2026-01-01", weight=1200, name="Amina", phone="0801234567", notes="ok"),
    ]

    struct = profile_entity_structure(visits)

    assert struct.transplant_pool == [
        {"owner": "flwA", "start_date": "2026-01-01", "visits": [{"day": 0, "values": {"weight": 1200.0}}]}
    ]


def test_transplant_pool_captures_date_leaves_as_offsets_from_first_visit():
    # The KMC growth curve's age axis is computed from a DATE (child_dob), not a
    # numeric field. So the pool must carry declared date paths too — as integer
    # day-offsets from this entity's first visit (negative for a DOB before it),
    # so a clone can reconstruct visit_date - dob faithfully and de-identified.
    visits = [
        _visit("e1", "flwA", "2026-02-01", child_weight_visit=1500, child_dob="2026-01-01"),
        _visit("e1", "flwA", "2026-02-08", child_weight_visit=1650, child_dob="2026-01-01"),
    ]

    struct = profile_entity_structure(visits, numeric_paths={"child_weight_visit"}, date_paths={"child_dob"})

    assert struct.transplant_pool == [
        {
            "owner": "flwA",
            "start_date": "2026-02-01",
            "visits": [
                # 2026-01-01 is 31 days before the 2026-02-01 first visit.
                {"day": 0, "values": {"child_weight_visit": 1500.0}, "dates": {"child_dob": -31}},
                {"day": 7, "values": {"child_weight_visit": 1650.0}, "dates": {"child_dob": -31}},
            ],
        }
    ]


def test_transplant_pool_omits_dates_key_when_no_date_paths_given():
    # Backward compatibility: callers that don't ask for dates get the legacy
    # numerics-only shape with no "dates" key (golden output stays byte-identical).
    visits = [_visit("e1", "flwA", "2026-01-01", weight=1200, dob="2025-12-01")]

    struct = profile_entity_structure(visits, numeric_paths={"weight"})

    assert struct.transplant_pool == [
        {"owner": "flwA", "start_date": "2026-01-01", "visits": [{"day": 0, "values": {"weight": 1200.0}}]}
    ]


def test_single_reading_of_a_time_varying_measure_is_not_stamped_across_the_series():
    """A child weighed ONCE must not appear weighed at every visit (connect-labs#1189).

    `_series_constants` decides constancy from one entity's own visits, so a path
    recorded exactly once is trivially "identical everywhere it appears" — correct for
    a registration-only attribute (DOB, birth weight), wrong for a clinical measure.
    On the KMC cohort 18.9% of babies with a visit-weight had exactly one, and stamping
    it across their series created 1,433 phantom weights and 1,183 fake-flat growth
    curves: the clone over-reported weight-consistency and under-reported growth
    velocity. Cohort evidence, not per-entity coincidence, decides.
    """
    from connect_labs.labs.synthetic.generator.fixtures.entities import _series_constants, _time_varying_paths

    W = "form.anthropometric.child_weight_visit"
    BW = "form.case.update.child_weight_birth"

    # A cohort where weight plainly varies within a child, and birth weight plainly doesn't.
    pool = []
    for i in range(10):
        pool.append(
            {
                "owner": "flw_1",
                "start_date": "2026-05-04",
                "visits": [
                    {"day": 0, "values": {W: 1500.0 + i, BW: 1400.0 + i}},
                    {"day": 14, "values": {W: 1700.0 + i, BW: 1400.0 + i}},
                    {"day": 28, "values": {W: 1900.0 + i, BW: 1400.0 + i}},
                ],
            }
        )
    tv = _time_varying_paths(pool)
    assert W in tv, "weight varies within a child -> time-varying"
    assert BW not in tv, "birth weight is identical within a child -> per-child constant"

    # The single-reading child: one weight at day 0, three visits.
    single = [
        {"day": 0, "values": {W: 1600.0, BW: 1450.0}},
        {"day": 14, "values": {BW: 1450.0}},
        {"day": 28, "values": {BW: 1450.0}},
    ]
    const_values, _ = _series_constants(single, tv)
    assert W not in const_values, "a lone weight reading must not become a per-child constant"
    assert const_values.get(BW) == 1450.0, "#734's birth-weight anchor must still hold"

    # With no cohort evidence at all (nothing recorded twice), the old constant
    # treatment stands — this is the DOB case #734 fixed.
    assert _series_constants(single, _time_varying_paths([{"visits": single}]))[0].get(W) == 1600.0


def _reg_v3(mother, baby, **vals):
    """V3 registration: submitted against the MOTHER, creates the baby as a subcase."""
    return {
        "username": "flwA",
        "visit_date": vals.pop("date"),
        "form_json": {"form": {"case": {"@case_id": mother}, "subcase_0": {"case": {"@case_id": baby}}, **vals}},
    }


def _visit_v3(baby, per_visit, **vals):
    """V3 visit: submitted against the BABY, creates a throwaway per-visit case."""
    return {
        "entity_id": per_visit,
        "username": "flwA",
        "visit_date": vals.pop("date"),
        "form_json": {"form": {"case": {"@case_id": baby}, "subcase_0": {"case": {"@case_id": per_visit}}, **vals}},
    }


def test_v3_registration_keys_to_the_baby_not_the_mother():
    """On a V3 registration form, form.case.@case_id is the MOTHER.

    Keying on it stranded the registration — and every field only it collects, like
    birth weight — on a separate entity from the baby's visits, doubling case counts
    (BERI 553 -> 1173) and halving per-field coverage.
    Regression for connect-labs#1224/#1225.
    """
    visits = [
        _reg_v3("mother-1", "baby-1", date="2026-01-01", child_weight_birth=1200),
        _visit_v3("baby-1", "pv-1", date="2026-01-08", child_weight_visit=1400),
        _visit_v3("baby-1", "pv-2", date="2026-01-15", child_weight_visit=1600),
    ]

    struct = profile_entity_structure(visits)

    assert struct.visits_per_entity == {3: 1}, "registration must join the baby's series"
    assert len(struct.transplant_pool) == 1
    series = struct.transplant_pool[0]["visits"]
    assert [v["day"] for v in series] == [0, 7, 14]
    assert series[0]["values"]["form.child_weight_birth"] == 1200


def test_v3_twins_stay_separate_rather_than_collapsing_onto_the_mother():
    """Two babies registered against one mother must remain two entities."""
    visits = [
        _reg_v3("mother-1", "baby-1", date="2026-01-01", child_weight_birth=1200),
        _reg_v3("mother-1", "baby-2", date="2026-01-01", child_weight_birth=1100),
        _visit_v3("baby-1", "pv-1", date="2026-01-08", child_weight_visit=1400),
        _visit_v3("baby-2", "pv-2", date="2026-01-08", child_weight_visit=1300),
    ]

    struct = profile_entity_structure(visits)

    assert len(struct.transplant_pool) == 2
    assert struct.visits_per_entity == {2: 2}


def test_gen1_visits_are_not_shredded_by_their_per_visit_subcase():
    """Gen-1 apps submit visits against the baby and create a throwaway subcase.

    That subcase is never submitted against, so it must not be mistaken for the
    beneficiary — otherwise every visit becomes its own entity.
    """
    visits = [
        # Gen-1 registration: against the baby, no subcase
        {
            "username": "flwA",
            "visit_date": "2026-01-01",
            "form_json": {"form": {"case": {"@case_id": "baby-1"}, "child_weight_birth": 1000}},
        },
        _visit_v3("baby-1", "pv-1", date="2026-01-08", child_weight_visit=1200),
        _visit_v3("baby-1", "pv-2", date="2026-01-15", child_weight_visit=1300),
    ]

    struct = profile_entity_structure(visits)

    assert struct.visits_per_entity == {3: 1}
    assert struct.transplant_pool[0]["visits"][0]["values"]["form.child_weight_birth"] == 1000


def test_entity_id_still_keys_sources_that_carry_no_case_block():
    """Synthetic clones have no case block at all, so entity_id must still work."""
    visits = [
        _visit("e1", "flwA", "2026-01-01", weight=1000),
        _visit("e1", "flwA", "2026-01-08", weight=1100),
        _visit("e2", "flwA", "2026-01-02", weight=1200),
    ]

    struct = profile_entity_structure(visits)

    assert struct.visits_per_entity == {2: 1, 1: 1}
    assert len(struct.transplant_pool) == 2


def test_falls_back_to_entity_id_when_the_case_id_is_itself_per_visit():
    """Some apps put a distinct case id on every visit.

    On opp 675 there were 505 distinct form.case.@case_id values across 505 visits,
    so keying on the case produced one "baby" per visit — the same shredding this
    resolver exists to prevent. entity_id groups those correctly, so a degenerate
    case key must lose to it. Regression for connect-labs#1225.
    """
    visits = []
    for baby in ("b1", "b2"):
        for i in range(4):
            visits.append(
                {
                    "entity_id": baby,
                    "username": "flwA",
                    "visit_date": f"2026-01-0{i + 1}",
                    # a fresh case id on every single visit
                    "form_json": {"form": {"case": {"@case_id": f"{baby}-visit-{i}"}, "w": 1000 + i}},
                }
            )

    struct = profile_entity_structure(visits)

    assert len(struct.transplant_pool) == 2, "should group by entity_id, not per-visit case"
    assert struct.visits_per_entity == {4: 2}
