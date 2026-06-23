"""Source-structure profiling for high-fidelity 'close mirror' cloning (issue #713).

Mirror mode reproduces a source opp's *structure* exactly — visits-per-case and
cases-per-FLW ratios — rather than re-sampling from fitted summary statistics.
These tests pin the empirical extraction that feeds that reproduction.
"""

from __future__ import annotations

from commcare_connect.labs.synthetic.generator.fixtures.mirror import profile_entity_structure


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
