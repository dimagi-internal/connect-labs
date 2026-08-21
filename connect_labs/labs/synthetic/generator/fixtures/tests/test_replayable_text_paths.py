"""Hidden calculated fields must reach the transplant pool — but identifiers must not.

CommCare reports hidden calculated questions as type ``DataBindOnly``. _KIND_MAP has
no entry for it, so they fall back to ``text`` — 320 of the 409 questions on the KMC
deliver app. The mirror pool only carried numeric / date / select paths, so all of
them were dropped, and indicators resting on them (KMC C03, C18, C22) read as
"never recorded" on every clone. That is a clone gap that looked exactly like a
programme collection gap.

These pin both halves: the promotion works, and it refuses to carry free text.
"""

from connect_labs.labs.synthetic.generator.fixtures.profiler import (
    _discover_replayable_text_paths,
    _is_identifier_path,
)


def _visits(*form_jsons):
    return [{"form_json": fj} for fj in form_jsons]


def test_calculated_date_field_is_promoted_to_a_date_path():
    visits = _visits(*({"form": {"reg_date": f"2025-06-{d:02d}"}} for d in range(1, 9)))
    dates, enums = _discover_replayable_text_paths(visits, {"form.reg_date"})
    assert dates == {"form.reg_date"}
    assert enums == set()


def test_calculated_status_field_is_promoted_to_an_enumerable_path():
    visits = _visits(*({"form": {"kmc": {"status_discharged": "discharged"}}} for _ in range(6)))
    dates, enums = _discover_replayable_text_paths(visits, {"form.kmc.status_discharged"})
    assert enums == {"form.kmc.status_discharged"}
    assert dates == set()


def test_free_text_is_not_replayed():
    """High cardinality + long values = prose. Never goes in the pool."""
    visits = _visits(
        *(
            {"form": {"clinical_summary": f"Mother reported difficulty feeding on day {i} of the visit"}}
            for i in range(12)
        )
    )
    dates, enums = _discover_replayable_text_paths(visits, {"form.clinical_summary"})
    assert dates == set()
    assert enums == set()


def test_identifier_shaped_leaves_are_refused_even_when_low_cardinality():
    """A tiny sample can make a name look enumerable — the leaf-name guard still wins."""
    for leaf in ("child_name", "alternative_phone_number", "address", "reg_gps", "refer_hospital_name"):
        path = f"form.{leaf}"
        visits = _visits(*({"form": {leaf: "Aisha B"}} for _ in range(8)))
        dates, enums = _discover_replayable_text_paths(visits, {path})
        assert path not in dates and path not in enums, leaf
        assert _is_identifier_path(path), leaf


def test_a_field_seen_only_once_or_twice_is_not_promoted():
    visits = _visits({"form": {"rare": "x"}}, {"form": {}}, {"form": {}})
    dates, enums = _discover_replayable_text_paths(visits, {"form.rare"})
    assert dates == set() and enums == set()


def test_identifier_guard_matches_whole_segments_not_substrings():
    """Substring matching over-blocks, and over-blocking costs the fidelity we came for.

    "nin" is inside "kmc_positioning_checklist"; "id" is inside "valid" and "avoid".
    """
    for path in (
        "form.kmc_positioning_checklist",
        "form.reg_date",
        "form.kmc_discontinuation.kmc_status_discharged",
        "form.valid_reading",
        "form.avoid_duplicate",
    ):
        assert not _is_identifier_path(path), path

    for path in (
        "form.child_name",
        "form.mother_name",
        "form.address",
        "form.alternative_phone_number",
        "form.gps_visit",
        "form.entity_name",
        "form.case.@case_id",
        "form.child_disability_specify",
    ):
        assert _is_identifier_path(path), path


def test_real_case_ids_are_never_replayed_verbatim():
    """Entity linkage is rebuilt from freshly minted uuids, never transplanted."""
    for path in ("form.case.@case_id", "entity_id", "form.kmc_beneficiary_case_id"):
        assert _is_identifier_path(path), path


def test_bare_integers_are_not_mistaken_for_dates():
    visits = _visits(*({"form": {"count": "20250601"}} for _ in range(6)))
    dates, _ = _discover_replayable_text_paths(visits, {"form.count"})
    assert dates == set()
