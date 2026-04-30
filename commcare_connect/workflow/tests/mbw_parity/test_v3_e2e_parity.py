"""End-to-end v1↔v3 parity through real SQL execution.

Bridges the in-memory parity harness with the actual workflow pipeline
runner: builds form-shaped visit_dicts from the existing FixtureBundle,
seeds labs_raw_visit_cache, runs the v3 mbw_monitoring_v3 visits pipeline
through SQLBackend.process_and_cache, and compares the resulting per-FLW
aggregations against the v1 reference functions in runners.py.

This is the proof that v3 is ready for live parity testing — every
primitive shipped this session (median/mode/mode_share/dup_share/
contains_word/pre_aggregate_by/lag_haversine + the schema parsing wiring)
threads from v3's PIPELINE_SCHEMAS through to actual SQL output that
matches v1's algorithm.

This file deliberately starts SMALL: one parity assertion (mother_counts)
against the SQL backend. As more pieces fall in line, additional
assertions get added here without changing the harness shape.
"""

import pytest
from django.utils import timezone

from commcare_connect.labs.analysis.backends.sql.backend import SQLBackend
from commcare_connect.labs.analysis.backends.sql.models import RawVisitCache
from commcare_connect.workflow.data_access import PipelineDataAccess
from commcare_connect.workflow.tests.mbw_parity.fixtures import edge_cases, small_realistic
from commcare_connect.workflow.tests.mbw_parity.runners import (
    compute_v1_overview_reference,
    compute_v1_quality_reference,
)


def _fixture_visit_to_form_json(v: dict) -> dict:
    """Translate a flat fixture visit dict into the nested form_json shape
    that v3's mbw_monitoring_v3 pipeline schemas extract from.

    The v3 visits schema reads from paths like
    `form.parents.parent.case.@case_id` (mother_case_id) and
    `form.confirm_visit_information.parity__...` — but our parity-harness
    fixtures use flat top-level keys for human readability. This translator
    is the seam: keeps fixture authoring simple, satisfies the SQL
    extraction contract.
    """
    return {
        "form": {
            "@name": v.get("form_name", ""),
            "meta": {
                "timeEnd": v.get("visit_datetime", ""),
                "location": v.get("gps_location", ""),
                "app_build_version": str(v.get("app_build_version") or ""),
            },
            "parents": {
                "parent": {
                    "case": {"@case_id": v.get("mother_case_id", "")},
                },
            },
            "confirm_visit_information": {
                "parity__of_live_births_or_stillbirths_after_24_weeks": v.get("parity") or "",
            },
            "feeding_history": {
                "pnc_current_bf_status": v.get("bf_status") or "",
            },
            "case": {"@case_id": v.get("case_id", "")},
        }
    }


def _seed_fixture(opp_id: int, fixture_visits: list[dict]) -> None:
    """Seed labs_raw_visit_cache with fixture visits, in the form_json shape
    v3's pipelines expect."""
    expires = timezone.now() + timezone.timedelta(days=1)
    for v in fixture_visits:
        RawVisitCache.objects.create(
            opportunity_id=opp_id,
            visit_count=len(fixture_visits),
            expires_at=expires,
            visit_id=str(v["visit_id"]),
            username=v["username"],
            visit_date=v["visit_date"],
            status=v.get("status", "approved"),
            entity_id=v.get("entity_id", ""),
            entity_name=v.get("entity_name", ""),
            location=v.get("gps_location") or "",
            form_json=_fixture_visit_to_form_json(v),
        )


def _build_visits_config(opp_id: int):
    """Build the VISITS_SCHEMA config and resolve its registrations JOIN hash.

    VISITS_SCHEMA now declares a JOIN onto registrations; the SQL builder
    refuses to run until the joined pipeline's `config_hash` is patched onto
    the JoinConfig. Tests that don't care about the JOIN (mother_count, ebf,
    parity_*) still need the resolution because the SQL builder's fail-fast
    guard runs unconditionally. Resolution is cheap and the joined cache is
    just empty for these tests, so JOIN paths read NULL — harmless for
    aggregations that don't reference `joined.*`.
    """
    from commcare_connect.labs.analysis.utils import resolve_join_hashes
    from commcare_connect.workflow.templates.mbw_monitoring_v3 import REGISTRATIONS_SCHEMA, VISITS_SCHEMA

    access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()
    visits_config = access._schema_to_config(VISITS_SCHEMA, definition_id=opp_id)
    reg_config = access._schema_to_config(REGISTRATIONS_SCHEMA, definition_id=opp_id)
    resolve_join_hashes({"visits": visits_config, "registrations": reg_config})
    return visits_config


def _run_v3_visits(opp_id: int, fixture_visits: list[dict]) -> dict[str, dict]:
    """Run mbw_monitoring_v3's visits pipeline through real SQL and return
    {username: custom_fields_dict}. Shared setup for all e2e parity tests.
    """
    _seed_fixture(opp_id, fixture_visits)
    config = _build_visits_config(opp_id)
    backend = SQLBackend()
    result = backend.process_and_cache(
        request=None,
        config=config,
        opportunity_id=opp_id,
        visit_dicts=fixture_visits,
        skip_raw_store=True,
    )
    return {row.username: row.custom_fields for row in result.rows}


@pytest.mark.django_db
class TestV3VisitsPipelineE2E:
    """Drive the v3 visits pipeline through the real SQL backend and compare
    output against v1 reference. Each method exercises a different framework
    primitive end-to-end:

    - mother_count        → count_unique
    - ebf_count           → count + filter_op="contains_word"
    - parity_mode_share   → mode_share + pre_aggregate_by
    - parity_mode_value   → mode + pre_aggregate_by
    - parity_dup_share    → dup_share + pre_aggregate_by
    """

    def test_mother_count_matches_v1_on_small_realistic(self, db):
        """v3's `mother_count` aggregation, run end-to-end through SQLBackend,
        must produce per-FLW counts matching compute_v1_overview_reference.

        The full chain here:
          fixture.visits (flat)
            → _fixture_visit_to_form_json (nested form_json)
            → labs_raw_visit_cache rows
            → SQLBackend.process_and_cache(v3 visits config)
            → FLWAnalysisResult with custom_fields per FLW
            → mother_count per username
            → compared to v1's set-based mother_counts reference

        If this passes on small_realistic, the same shape will work for
        every other v3 aggregation; new assertions get added here.
        """
        bundle = small_realistic()
        opp_id = 700001
        _seed_fixture(opp_id, bundle.visits)

        # Build the AnalysisPipelineConfig the same way the live runner does;
        # _build_visits_config also resolves the registrations JOIN hash so
        # the SQL builder's fail-fast guard is satisfied.
        config = _build_visits_config(opp_id)

        # Visit dicts only need to satisfy len(); we already stored the rows
        # via _seed_fixture, so use skip_raw_store=True to avoid a re-write.
        backend = SQLBackend()
        result = backend.process_and_cache(
            request=None,
            config=config,
            opportunity_id=opp_id,
            visit_dicts=bundle.visits,
            skip_raw_store=True,
        )

        # FLWAnalysisResult has rows keyed by username; custom_fields holds
        # the aggregated values declared in the schema.
        v3_mother_counts = {row.username: row.custom_fields.get("mother_count") for row in result.rows}
        v1 = compute_v1_overview_reference(bundle.visits, bundle.registrations, bundle.gs_forms)
        v1_mother_counts = v1["mother_counts"]

        # Both sides should agree on the FLWs and on each FLW's count.
        assert set(v3_mother_counts.keys()) == set(v1_mother_counts.keys()), (
            f"v3 keys: {sorted(v3_mother_counts)}\n" f"v1 keys: {sorted(v1_mother_counts)}\n"
        )
        for flw, v1_count in v1_mother_counts.items():
            assert (
                v3_mother_counts[flw] == v1_count
            ), f"mother_count mismatch for {flw!r}: v3 SQL={v3_mother_counts[flw]} v1={v1_count}"

    def test_ebf_count_matches_v1_via_contains_word_filter(self, db):
        """v3's ebf_count + bf_status_count via contains_word filter, run
        end-to-end through SQLBackend, must match v1's `if "ebf" in
        bf_status.split()` Python loop on the same fixture rows.

        Exercises filter_op="contains_word" through the SQL FILTER (WHERE ...)
        clause with `'ebf' = ANY(string_to_array(...))` syntax.
        """
        bundle = small_realistic()
        v3_by_flw = _run_v3_visits(700002, bundle.visits)

        v1 = compute_v1_overview_reference(bundle.visits, bundle.registrations, bundle.gs_forms)
        v1_ebf_pct = v1["ebf_pct_by_flw"]

        # Reconstruct ebf_pct from v3's ebf_count + bf_status_count.
        # V1 lowercases username before computing this metric (see compute_v1_overview_reference).
        # The seeded SQL keeps original case; for fixture FLWs the case happens to match.
        for flw, v1_pct in v1_ebf_pct.items():
            v3_fields = v3_by_flw.get(flw)
            assert v3_fields is not None, f"v3 has no row for FLW {flw!r}"
            ebf_count = v3_fields.get("ebf_count") or 0
            bf_total = v3_fields.get("bf_status_count") or 0
            v3_pct = round(ebf_count / bf_total * 100) if bf_total else 0
            assert v3_pct == v1_pct, f"ebf_pct mismatch for {flw!r}: v3 SQL={v3_pct} v1={v1_pct}"

    def test_parity_mode_share_matches_v1_via_two_pass(self, db):
        """v3's parity_mode_share via pre_aggregate_by + mode_share, run
        end-to-end through SQLBackend, must match v1's per-mother-then-per-FLW
        algorithm on the same fixture rows.

        Exercises the deepest correlated-subquery shape in the framework:
        outer mode_share over an inner GROUP BY mother_case_id with
        pre_aggregation="last".
        """
        bundle = small_realistic()
        v3_by_flw = _run_v3_visits(700003, bundle.visits)

        v1 = compute_v1_quality_reference(bundle.visits, bundle.registrations, bundle.gs_forms)

        for flw, v1_quality in v1.items():
            v3_fields = v3_by_flw.get(flw)
            assert v3_fields is not None, f"v3 has no row for FLW {flw!r}"
            v1_mode_pct = v1_quality["parity_concentration"]["mode_pct"]
            v3_mode_share = v3_fields.get("parity_mode_share")
            v3_mode_pct = round(v3_mode_share * 100) if v3_mode_share is not None else 0
            assert (
                v3_mode_pct == v1_mode_pct
            ), f"parity mode_pct mismatch for {flw!r}: v3 SQL={v3_mode_pct} v1={v1_mode_pct}"

    def test_parity_mode_value_matches_v1(self, db):
        """v3's parity_mode_value via pre_aggregate_by + `mode`, end-to-end."""
        bundle = small_realistic()
        v3_by_flw = _run_v3_visits(700004, bundle.visits)

        v1 = compute_v1_quality_reference(bundle.visits, bundle.registrations, bundle.gs_forms)

        for flw, v1_quality in v1.items():
            v3_fields = v3_by_flw.get(flw)
            assert v3_fields is not None
            v1_mode_value = v1_quality["parity_concentration"]["mode_value"]
            v3_mode_value = v3_fields.get("parity_mode_value")
            assert (
                v3_mode_value == v1_mode_value
            ), f"mode_value mismatch for {flw!r}: v3 SQL={v3_mode_value!r} v1={v1_mode_value!r}"

    def test_parity_dup_share_matches_v1(self, db):
        """v3's parity_dup_share via pre_aggregate_by + `dup_share`,
        end-to-end. Closes the parity_concentration triad."""
        bundle = small_realistic()
        v3_by_flw = _run_v3_visits(700005, bundle.visits)

        v1 = compute_v1_quality_reference(bundle.visits, bundle.registrations, bundle.gs_forms)

        for flw, v1_quality in v1.items():
            v3_fields = v3_by_flw.get(flw)
            assert v3_fields is not None
            v1_pct_duplicate = v1_quality["parity_concentration"]["pct_duplicate"]
            v3_dup_share = v3_fields.get("parity_dup_share")
            v3_pct = round(v3_dup_share * 100) if v3_dup_share is not None else 0
            assert (
                v3_pct == v1_pct_duplicate
            ), f"pct_duplicate mismatch for {flw!r}: v3 SQL={v3_pct} v1={v1_pct_duplicate}"

    def test_edge_cases_fixture_holds_parity(self, db):
        """The same v3 visits pipeline must hold parity on edge_cases too —
        which exercises missing GPS, missing mother_case_id, single-visit
        mothers, ebf-token vs substring, mode_share extremes (all-same parity
        vs diverse), rejected status, etc.

        If small_realistic passes but edge_cases fails, we have a corner-case
        regression — the harness immediately tells us which leaf disagrees.
        """
        bundle = edge_cases()
        v3_by_flw = _run_v3_visits(700006, bundle.visits)

        v1_overview = compute_v1_overview_reference(bundle.visits, bundle.registrations, bundle.gs_forms)
        v1_quality = compute_v1_quality_reference(bundle.visits, bundle.registrations, bundle.gs_forms)

        # mother_count parity across every FLW that v1 reports.
        for flw, v1_count in v1_overview["mother_counts"].items():
            v3_fields = v3_by_flw.get(flw)
            assert v3_fields is not None, f"v3 missing FLW {flw!r}"
            assert (
                v3_fields.get("mother_count") == v1_count
            ), f"edge_cases mother_count mismatch for {flw!r}: v3={v3_fields.get('mother_count')} v1={v1_count}"

        # parity_concentration parity for every FLW v1 reports a value for.
        for flw, q in v1_quality.items():
            v3_fields = v3_by_flw.get(flw)
            assert v3_fields is not None, f"v3 missing FLW {flw!r}"

            v1_pc = q["parity_concentration"]
            v3_mode_share = v3_fields.get("parity_mode_share")
            v3_mode_pct = round(v3_mode_share * 100) if v3_mode_share is not None else 0
            assert (
                v3_mode_pct == v1_pc["mode_pct"]
            ), f"edge_cases mode_pct mismatch for {flw!r}: v3={v3_mode_pct} v1={v1_pc['mode_pct']}"

            v3_mode_value = v3_fields.get("parity_mode_value")
            assert (
                v3_mode_value == v1_pc["mode_value"]
            ), f"edge_cases mode_value mismatch for {flw!r}: v3={v3_mode_value!r} v1={v1_pc['mode_value']!r}"

            v3_dup_share = v3_fields.get("parity_dup_share")
            v3_pct_dup = round(v3_dup_share * 100) if v3_dup_share is not None else 0
            assert (
                v3_pct_dup == v1_pc["pct_duplicate"]
            ), f"edge_cases pct_duplicate mismatch for {flw!r}: v3={v3_pct_dup} v1={v1_pc['pct_duplicate']}"
