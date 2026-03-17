"""
Parity tests between v1 (MBWMonitoringStreamView) and v2 (job handler) MBW data paths.

Both paths call the same computation functions (GPS analysis, follow-up rates,
quality metrics, etc.). The difference is how raw data is transformed before
those calls. This test verifies the transformations produce identical inputs.

V1 transforms are imported from data_transforms.py (the shared module that
views.py also uses). V2 transforms come from the job handler module.

Strategy:
    1. Create VisitRow objects (as v1's pipeline would produce)
    2. Run v1's transforms via data_transforms module (same code views.py uses)
    3. Serialize VisitRows to dicts (as v2's pipeline SSE would deliver)
    4. Run v2's adapter/transformation functions from job handler
    5. Assert both paths produce identical computation inputs and outputs
"""

from dataclasses import dataclass, field
from datetime import date

import django
from django.conf import settings

# Minimal Django configuration — avoids PostGIS/GDAL dependency
if not settings.configured:
    settings.configure(
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
    )
    django.setup()

from commcare_connect.workflow.job_handlers.mbw_monitoring import (  # noqa: E402
    _adapt_rows,
    _build_gps_visit_dicts,
    _compute_ebf_by_flw,
    _extract_per_mother_fields,
)
from commcare_connect.workflow.templates.mbw_monitoring.data_transforms import (  # noqa: E402
    build_gps_visit_dicts,
    compute_ebf_by_flw,
    extract_per_mother_fields,
)

# =============================================================================
# Test Fixtures — realistic MBW data
# =============================================================================


def _make_visit_rows():
    """Create VisitRow-like objects as v1's pipeline would produce.

    Returns a list of mock VisitRow objects with the same attributes
    that views.py accesses: .username, .visit_date, .latitude, .longitude,
    .entity_name, .id, .computed dict.
    """

    @dataclass
    class MockVisitRow:
        id: int
        username: str
        visit_date: date | None
        latitude: float | None
        longitude: float | None
        entity_name: str
        computed: dict = field(default_factory=dict)

    rows = [
        MockVisitRow(
            id=1,
            username="flw_alpha",
            visit_date=date(2024, 1, 15),
            latitude=-1.2345,
            longitude=35.6789,
            entity_name="Mother A (1234567890)",
            computed={
                "gps_location": "-1.2345 35.6789 1000 10",
                "case_id": "case_001",
                "mother_case_id": "mother_001",
                "form_name": "ANC Visit",
                "visit_datetime": "2024-01-15T09:30:00Z",
                "entity_name": "Mother A (1234567890)",
                "app_build_version": 100,
                "parity": "2",
                "anc_completion_date": "2024-01-15",
                "pnc_completion_date": None,
                "baby_dob": None,
                "bf_status": "ebf",
                "entity_id_deliver": "deliver_001",
            },
        ),
        MockVisitRow(
            id=2,
            username="flw_alpha",
            visit_date=date(2024, 1, 20),
            latitude=-1.2346,
            longitude=35.6790,
            entity_name="Mother A (1234567890)",
            computed={
                "gps_location": "-1.2346 35.6790 950 9",
                "case_id": "case_002",
                "mother_case_id": "mother_001",
                "form_name": "1 Week Visit",
                "visit_datetime": "2024-01-20T10:15:00Z",
                "entity_name": "Mother A (1234567890)",
                "app_build_version": 100,
                "parity": None,
                "anc_completion_date": None,
                "pnc_completion_date": None,
                "baby_dob": None,
                "bf_status": None,
                "entity_id_deliver": "deliver_001",
            },
        ),
        MockVisitRow(
            id=3,
            username="flw_alpha",
            visit_date=date(2024, 2, 10),
            latitude=-1.3000,
            longitude=35.7000,
            entity_name="Mother B (9876543210)",
            computed={
                "gps_location": "-1.3000 35.7000 1100 12",
                "case_id": "case_003",
                "mother_case_id": "mother_002",
                "form_name": "Post delivery visit",
                "visit_datetime": "2024-02-10T14:00:00Z",
                "entity_name": "Mother B (9876543210)",
                "app_build_version": 100,
                "parity": None,
                "anc_completion_date": None,
                "pnc_completion_date": "2024-02-10",
                "baby_dob": "2024-02-08",
                "bf_status": "formula",
                "entity_id_deliver": "deliver_002",
            },
        ),
        MockVisitRow(
            id=4,
            username="FLW_Alpha",  # Mixed case — v1 lowercases
            visit_date=date(2024, 2, 15),
            latitude=-1.3001,
            longitude=35.7001,
            entity_name="Mother B (9876543210)",
            computed={
                "gps_location": "-1.3001 35.7001 1095 11",
                "case_id": "case_004",
                "mother_case_id": "mother_002",
                "form_name": "1 Month Visit",
                "visit_datetime": "2024-02-15T09:00:00Z",
                "entity_name": "Mother B (9876543210)",
                "app_build_version": 100,
                "parity": None,
                "anc_completion_date": None,
                "pnc_completion_date": None,
                "baby_dob": None,
                "bf_status": "ebf",
                "entity_id_deliver": "deliver_002",
            },
        ),
    ]
    return rows


def _serialize_visit_row(row) -> dict:
    """Serialize a VisitRow-like object to the dict format the pipeline SSE stream produces.

    This mimics what PipelineDataStreamView sends: base fields + computed
    fields merged at the top level.
    """
    return {
        "id": row.id,
        "username": row.username,
        "visit_date": row.visit_date.isoformat() if row.visit_date else None,
        "latitude": row.latitude,
        "longitude": row.longitude,
        "entity_name": row.entity_name,
        # Computed fields nested (as the SQL backend returns them)
        "computed": dict(row.computed),
        # Pipeline SSE also includes metadata
        "metadata": {
            "location": (f"{row.latitude} {row.longitude}" if row.latitude is not None else None),
        },
    }


# =============================================================================
# Parity Tests
# =============================================================================


class TestGPSTransformationParity:
    """Verify v1 and v2 GPS dict-building produces identical computation inputs."""

    def setup_method(self):
        self.visit_rows = _make_visit_rows()
        self.active_usernames = {"flw_alpha"}
        self.serialized_rows = [_serialize_visit_row(r) for r in self.visit_rows]

    def test_gps_dict_count_matches(self):
        """Both paths produce the same number of GPS visit dicts."""
        v1_dicts = build_gps_visit_dicts(self.visit_rows, self.active_usernames)
        v2_dicts = _build_gps_visit_dicts(self.serialized_rows)

        assert len(v1_dicts) == len(v2_dicts)

    def test_gps_dict_usernames_match(self):
        """Both paths produce identical lowercased usernames."""
        v1_dicts = build_gps_visit_dicts(self.visit_rows, self.active_usernames)
        v2_dicts = _build_gps_visit_dicts(self.serialized_rows)

        v1_usernames = [d["username"] for d in v1_dicts]
        v2_usernames = [d["username"] for d in v2_dicts]
        # v1 filters by active_usernames and lowercases; v2 doesn't filter
        # but preserves username. For parity, compare lowercased.
        v2_usernames_lower = [u.lower() for u in v2_usernames]
        assert v1_usernames == v2_usernames_lower[: len(v1_usernames)]

    def test_gps_dict_computed_fields_match(self):
        """Both paths produce identical computed dicts for GPS analysis."""
        v1_dicts = build_gps_visit_dicts(self.visit_rows, self.active_usernames)
        v2_dicts = _build_gps_visit_dicts(self.serialized_rows)

        # Compare computed fields for each visit
        for v1, v2 in zip(v1_dicts, v2_dicts):
            for key in [
                "gps_location",
                "case_id",
                "mother_case_id",
                "entity_name",
                "visit_datetime",
                "form_name",
                "app_build_version",
            ]:
                assert v1["computed"].get(key) == v2["computed"].get(
                    key
                ), f"Mismatch on computed['{key}']: v1={v1['computed'].get(key)} vs v2={v2['computed'].get(key)}"

    def test_gps_dict_metadata_location_match(self):
        """Both paths produce identical metadata.location strings."""
        v1_dicts = build_gps_visit_dicts(self.visit_rows, self.active_usernames)
        v2_dicts = _build_gps_visit_dicts(self.serialized_rows)

        for v1, v2 in zip(v1_dicts, v2_dicts):
            v1_loc = v1.get("metadata", {}).get("location")
            v2_loc = v2.get("metadata", {}).get("location")
            assert v1_loc == v2_loc, f"Location mismatch: v1={v1_loc} vs v2={v2_loc}"

    def test_gps_analysis_produces_identical_results(self):
        """Full GPS analysis produces identical results from both paths."""
        from commcare_connect.workflow.templates.mbw_monitoring.gps_analysis import analyze_gps_metrics

        flw_names = {"flw_alpha": "FLW Alpha"}
        v1_dicts = build_gps_visit_dicts(self.visit_rows, self.active_usernames)
        v2_dicts = _build_gps_visit_dicts(self.serialized_rows)

        v1_result = analyze_gps_metrics(v1_dicts, flw_names)
        v2_result = analyze_gps_metrics(v2_dicts, flw_names)

        assert v1_result.total_visits == v2_result.total_visits
        assert v1_result.total_flagged == v2_result.total_flagged
        assert len(v1_result.flw_summaries) == len(v2_result.flw_summaries)

        # Compare per-FLW summaries
        for v1_flw, v2_flw in zip(
            sorted(v1_result.flw_summaries, key=lambda x: x.username),
            sorted(v2_result.flw_summaries, key=lambda x: x.username),
        ):
            assert v1_flw.username == v2_flw.username
            assert v1_flw.total_visits == v2_flw.total_visits
            assert v1_flw.flagged_visits == v2_flw.flagged_visits
            assert v1_flw.unique_cases == v2_flw.unique_cases
            assert v1_flw.avg_case_distance_km == v2_flw.avg_case_distance_km


class TestPerMotherFieldExtractionParity:
    """Verify v1 and v2 per-mother field extraction produces identical results."""

    def setup_method(self):
        self.visit_rows = _make_visit_rows()
        self.serialized_rows = [_serialize_visit_row(r) for r in self.visit_rows]

    def test_parity_by_mother_matches(self):
        v1_result = extract_per_mother_fields(self.visit_rows)
        v2_result = _extract_per_mother_fields(_adapt_rows(self.serialized_rows))

        assert v1_result["parity_by_mother"] == v2_result["parity_by_mother"]

    def test_anc_date_by_mother_matches(self):
        v1_result = extract_per_mother_fields(self.visit_rows)
        v2_result = _extract_per_mother_fields(_adapt_rows(self.serialized_rows))

        assert v1_result["anc_date_by_mother"] == v2_result["anc_date_by_mother"]

    def test_pnc_date_by_mother_matches(self):
        v1_result = extract_per_mother_fields(self.visit_rows)
        v2_result = _extract_per_mother_fields(_adapt_rows(self.serialized_rows))

        assert v1_result["pnc_date_by_mother"] == v2_result["pnc_date_by_mother"]

    def test_baby_dob_by_mother_matches(self):
        v1_result = extract_per_mother_fields(self.visit_rows)
        v2_result = _extract_per_mother_fields(_adapt_rows(self.serialized_rows))

        assert v1_result["baby_dob_by_mother"] == v2_result["baby_dob_by_mother"]

    def test_all_fields_match(self):
        """Full comparison of all per-mother field dicts."""
        v1_result = extract_per_mother_fields(self.visit_rows)
        v2_result = _extract_per_mother_fields(_adapt_rows(self.serialized_rows))

        assert v1_result == v2_result


class TestEBFComputationParity:
    """Verify v1 and v2 EBF % computation produces identical results."""

    def setup_method(self):
        self.visit_rows = _make_visit_rows()
        self.serialized_rows = [_serialize_visit_row(r) for r in self.visit_rows]

    def test_ebf_percentages_match(self):
        v1_ebf = compute_ebf_by_flw(self.visit_rows)
        v2_ebf = _compute_ebf_by_flw(_adapt_rows(self.serialized_rows))

        assert v1_ebf == v2_ebf

    def test_ebf_values_are_correct(self):
        """Verify the actual EBF values.

        Row 1: bf_status="ebf" → counts as EBF
        Row 3: bf_status="formula" → not EBF
        Row 4: bf_status="ebf" → counts as EBF
        Rows with None bf_status are skipped.

        flw_alpha: 2 ebf out of 3 total = 67%
        """
        v1_ebf = compute_ebf_by_flw(self.visit_rows)
        v2_ebf = _compute_ebf_by_flw(_adapt_rows(self.serialized_rows))

        assert v1_ebf["flw_alpha"] == 67
        assert v2_ebf["flw_alpha"] == 67


class TestPipelineRowAdapterParity:
    """Verify _PipelineRowAdapter faithfully reproduces VisitRow attribute access."""

    def setup_method(self):
        self.visit_rows = _make_visit_rows()
        self.serialized_rows = [_serialize_visit_row(r) for r in self.visit_rows]
        self.adapted_rows = _adapt_rows(self.serialized_rows)

    def test_username_access(self):
        for orig, adapted in zip(self.visit_rows, self.adapted_rows):
            assert adapted.username == orig.username

    def test_computed_access(self):
        for orig, adapted in zip(self.visit_rows, self.adapted_rows):
            for key in orig.computed:
                assert adapted.computed.get(key) == orig.computed.get(
                    key
                ), f"computed['{key}']: orig={orig.computed.get(key)} vs adapted={adapted.computed.get(key)}"

    def test_visit_date_access(self):
        for orig, adapted in zip(self.visit_rows, self.adapted_rows):
            assert adapted.visit_date == orig.visit_date


class TestFollowupAnalysisParity:
    """Verify that follow-up analysis functions produce identical results
    when given v1 VisitRow objects vs v2 adapted pipeline dicts."""

    def setup_method(self):
        self.visit_rows = _make_visit_rows()
        self.serialized_rows = [_serialize_visit_row(r) for r in self.visit_rows]
        self.active_usernames = {"flw_alpha"}
        self.flw_names = {"flw_alpha": "FLW Alpha"}

    def test_build_followup_produces_same_structure(self):
        """build_followup_from_pipeline produces identical visit_cases_by_flw."""
        from commcare_connect.workflow.templates.mbw_monitoring.followup_analysis import build_followup_from_pipeline

        v1_cases = build_followup_from_pipeline(self.visit_rows, self.active_usernames)
        v2_cases = build_followup_from_pipeline(_adapt_rows(self.serialized_rows), self.active_usernames)

        # Same FLW keys
        assert set(v1_cases.keys()) == set(v2_cases.keys())

        # Same number of cases per FLW
        for flw in v1_cases:
            assert len(v1_cases[flw]) == len(
                v2_cases[flw]
            ), f"FLW {flw}: v1 has {len(v1_cases[flw])} cases, v2 has {len(v2_cases[flw])}"

    def test_aggregate_flw_followup_matches(self):
        """aggregate_flw_followup produces identical summaries."""
        from commcare_connect.workflow.templates.mbw_monitoring.followup_analysis import (
            aggregate_flw_followup,
            build_followup_from_pipeline,
        )

        current_date = date(2024, 2, 20)

        v1_cases = build_followup_from_pipeline(self.visit_rows, self.active_usernames)
        v2_cases = build_followup_from_pipeline(_adapt_rows(self.serialized_rows), self.active_usernames)

        v1_followup = aggregate_flw_followup(v1_cases, current_date, self.flw_names)
        v2_followup = aggregate_flw_followup(v2_cases, current_date, self.flw_names)

        assert len(v1_followup) == len(v2_followup)

        for v1_flw, v2_flw in zip(v1_followup, v2_followup):
            assert v1_flw["username"] == v2_flw["username"]
            assert v1_flw["completion_rate"] == v2_flw["completion_rate"]
            assert v1_flw["completed_total"] == v2_flw["completed_total"]

    def test_visit_status_distribution_matches(self):
        """aggregate_visit_status_distribution produces identical results."""
        from commcare_connect.workflow.templates.mbw_monitoring.followup_analysis import (
            aggregate_visit_status_distribution,
            build_followup_from_pipeline,
        )

        current_date = date(2024, 2, 20)

        v1_cases = build_followup_from_pipeline(self.visit_rows, self.active_usernames)
        v2_cases = build_followup_from_pipeline(_adapt_rows(self.serialized_rows), self.active_usernames)

        v1_dist = aggregate_visit_status_distribution(v1_cases, current_date)
        v2_dist = aggregate_visit_status_distribution(v2_cases, current_date)

        assert v1_dist == v2_dist


class TestEndToEndJobHandlerParity:
    """End-to-end test: verify the job handler produces the same results
    as running the v1 computation steps inline."""

    def setup_method(self):
        self.visit_rows = _make_visit_rows()
        self.serialized_rows = [_serialize_visit_row(r) for r in self.visit_rows]
        self.active_usernames = {"flw_alpha"}
        self.flw_names = {"flw_alpha": "FLW Alpha"}

    def test_gps_data_matches(self):
        """GPS data from job handler matches v1 computation."""
        from commcare_connect.workflow.templates.mbw_monitoring.gps_analysis import analyze_gps_metrics
        from commcare_connect.workflow.templates.mbw_monitoring.serializers import serialize_flw_summary

        # V1 path
        v1_gps_dicts = build_gps_visit_dicts(self.visit_rows, self.active_usernames)
        v1_result = analyze_gps_metrics(v1_gps_dicts, self.flw_names)
        v1_gps_data = {
            "total_visits": v1_result.total_visits,
            "total_flagged": v1_result.total_flagged,
            "flw_summaries": [serialize_flw_summary(f) for f in v1_result.flw_summaries],
        }

        # V2 path (via job handler internals)
        v2_gps_dicts = _build_gps_visit_dicts(self.serialized_rows)
        v2_result = analyze_gps_metrics(v2_gps_dicts, self.flw_names)
        v2_gps_data = {
            "total_visits": v2_result.total_visits,
            "total_flagged": v2_result.total_flagged,
            "flw_summaries": [serialize_flw_summary(f) for f in v2_result.flw_summaries],
        }

        assert v1_gps_data["total_visits"] == v2_gps_data["total_visits"]
        assert v1_gps_data["total_flagged"] == v2_gps_data["total_flagged"]
        assert len(v1_gps_data["flw_summaries"]) == len(v2_gps_data["flw_summaries"])

        # Deep compare serialized FLW summaries
        for v1_flw, v2_flw in zip(v1_gps_data["flw_summaries"], v2_gps_data["flw_summaries"]):
            for key in v1_flw:
                if key == "trailing_7_days":
                    # Compare length and structure
                    assert len(v1_flw[key]) == len(v2_flw[key])
                else:
                    assert (
                        v1_flw[key] == v2_flw[key]
                    ), f"GPS FLW summary mismatch on '{key}': v1={v1_flw[key]} vs v2={v2_flw[key]}"

    def test_followup_data_matches(self):
        """Follow-up data from job handler matches v1 computation."""
        from commcare_connect.workflow.templates.mbw_monitoring.followup_analysis import (
            aggregate_flw_followup,
            aggregate_visit_status_distribution,
            build_followup_from_pipeline,
        )

        current_date = date(2024, 2, 20)

        # V1 path
        v1_cases = build_followup_from_pipeline(self.visit_rows, self.active_usernames)
        v1_followup = aggregate_flw_followup(v1_cases, current_date, self.flw_names)
        v1_dist = aggregate_visit_status_distribution(v1_cases, current_date)

        # V2 path
        v2_cases = build_followup_from_pipeline(_adapt_rows(self.serialized_rows), self.active_usernames)
        v2_followup = aggregate_flw_followup(v2_cases, current_date, self.flw_names)
        v2_dist = aggregate_visit_status_distribution(v2_cases, current_date)

        # Compare follow-up summaries
        assert len(v1_followup) == len(v2_followup)
        for v1_f, v2_f in zip(v1_followup, v2_followup):
            assert v1_f["username"] == v2_f["username"]
            assert v1_f["completion_rate"] == v2_f["completion_rate"]

        # Compare status distributions
        assert v1_dist == v2_dist

    def test_quality_metrics_match(self):
        """Quality metrics from job handler match v1 computation."""
        from commcare_connect.workflow.templates.mbw_monitoring.followup_analysis import (
            build_followup_from_pipeline,
            compute_overview_quality_metrics,
            extract_mother_metadata_from_forms,
        )

        current_date = date(2024, 2, 20)

        # V1 path
        v1_cases = build_followup_from_pipeline(self.visit_rows, self.active_usernames)
        v1_per_mother = extract_per_mother_fields(self.visit_rows)
        v1_mother_meta = extract_mother_metadata_from_forms([], current_date=current_date)
        v1_quality = compute_overview_quality_metrics(
            v1_cases,
            v1_mother_meta,
            v1_per_mother["parity_by_mother"],
            anc_date_by_mother=v1_per_mother["anc_date_by_mother"],
            pnc_date_by_mother=v1_per_mother["pnc_date_by_mother"],
        )

        # V2 path
        adapted = _adapt_rows(self.serialized_rows)
        v2_cases = build_followup_from_pipeline(adapted, self.active_usernames)
        v2_per_mother = _extract_per_mother_fields(adapted)
        v2_mother_meta = extract_mother_metadata_from_forms([], current_date=current_date)
        v2_quality = compute_overview_quality_metrics(
            v2_cases,
            v2_mother_meta,
            v2_per_mother["parity_by_mother"],
            anc_date_by_mother=v2_per_mother["anc_date_by_mother"],
            pnc_date_by_mother=v2_per_mother["pnc_date_by_mother"],
        )

        assert v1_quality == v2_quality
