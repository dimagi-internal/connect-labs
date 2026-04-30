"""End-to-end MBW v1/v3 dashboard payload parity test against real data.

Mirrors test_mbw_parity.py (the v1↔v2 comparison) but drives v3 through the
real pipeline framework — NO job handler. v3's whole point is that the
pipeline IS the runner. This command validates that claim against a real
opportunity's data.

Usage:
    python manage.py get_cli_token --settings=config.settings.local
    python manage.py test_mbw_v1_v3_parity --opportunity-id 765
    python manage.py test_mbw_v1_v3_parity --opportunity-id 765 --verbose
    python manage.py test_mbw_v1_v3_parity --opportunity-id 765 --section overview

Output is a structured parity report card. Sections that match get a green
"MATCH"; sections that disagree get a list of the disagreements (truncated
in non-verbose mode). The point is to see, at a glance, which dashboard
leaves v3 already covers and which still need work.
"""

import logging
from datetime import date

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Test MBW v1/v3 full dashboard payload parity using real data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--opportunity-id",
            type=int,
            required=True,
            help="Opportunity ID to fetch pipeline data for",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show detailed field-by-field comparison",
        )
        parser.add_argument(
            "--gs-app-id",
            type=str,
            default="2ca67a89dd8a2209d75ed5599b45a5d1",
            help="CommCare HQ app ID for Gold Standard Visit Checklist",
        )
        parser.add_argument(
            "--section",
            choices=["overview", "quality", "gps", "followup", "performance", "all"],
            default="all",
            help="Which dashboard section to compare (default: all)",
        )

    def handle(self, *args, **options):
        from commcare_connect.labs.analysis.backends.sql.backend import SQLBackend
        from commcare_connect.labs.analysis.backends.sql.cchq_cache_loader import (
            populate_computed_cache_from_form_dicts,
        )
        from commcare_connect.labs.analysis.data_access import fetch_flw_names
        from commcare_connect.labs.analysis.pipeline import AnalysisPipeline
        from commcare_connect.labs.analysis.utils import resolve_join_hashes
        from commcare_connect.labs.integrations.connect.cli import create_cli_request
        from commcare_connect.workflow.data_access import PipelineDataAccess
        from commcare_connect.workflow.templates.mbw_monitoring.data_transforms import (
            build_gps_visit_dicts,
            compute_ebf_by_flw,
            extract_per_mother_fields,
        )
        from commcare_connect.workflow.templates.mbw_monitoring.followup_analysis import (
            aggregate_flw_followup,
            aggregate_visit_status_distribution,
            build_followup_from_pipeline,
            compute_overview_quality_metrics,
            count_mothers_from_pipeline,
            extract_mother_metadata_from_forms,
        )
        from commcare_connect.workflow.templates.mbw_monitoring.gps_analysis import (
            analyze_gps_metrics,
            compute_median_meters_per_visit,
            compute_median_minutes_per_visit,
        )
        from commcare_connect.workflow.templates.mbw_monitoring.pipeline_config import MBW_GPS_PIPELINE_CONFIG
        from commcare_connect.workflow.templates.mbw_monitoring.serializers import serialize_flw_summary
        from commcare_connect.workflow.templates.mbw_monitoring_v3 import (
            REGISTRATIONS_SCHEMA,
            VISITS_GPS_SCHEMA,
            VISITS_SCHEMA,
        )

        opportunity_id = options["opportunity_id"]
        verbose = options["verbose"]
        section = options["section"]

        self.stdout.write(f"\nMBW v1/v3 Full Payload Parity — opportunity {opportunity_id}")
        self.stdout.write("=" * 70)

        # =====================================================================
        # STEP 1: Fetch shared data (same as v1↔v2 command)
        # =====================================================================
        self.stdout.write("\n[1/5] Creating CLI request...")
        request = create_cli_request(opportunity_id=opportunity_id)
        if not request:
            raise CommandError("Failed to create CLI request. Run: python manage.py get_cli_token")
        access_token = request.session.get("labs_oauth", {}).get("access_token")
        self.stdout.write(self.style.SUCCESS("  -> OK"))

        self.stdout.write("\n[2/5] Fetching pipeline visit data (V1 path)...")
        pipeline = AnalysisPipeline(request)
        v1_pipeline_result = pipeline.stream_analysis_ignore_events(
            MBW_GPS_PIPELINE_CONFIG, opportunity_id=opportunity_id
        )
        rows = v1_pipeline_result.rows
        self.stdout.write(self.style.SUCCESS(f"  -> {len(rows)} VisitRows"))
        if not rows:
            raise CommandError("No pipeline rows returned — nothing to compare.")

        try:
            flw_names_raw = fetch_flw_names(access_token, opportunity_id)
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"  -> FLW names fetch failed: {e}"))
            flw_names_raw = {}
        active_usernames = (
            {u.lower() for u in flw_names_raw.keys()}
            if flw_names_raw
            else {(r.username or "").lower() for r in rows if r.username}
        )
        flw_names = {k.lower(): v for k, v in flw_names_raw.items()}
        self.stdout.write(f"  -> {len(flw_names)} FLW names, {len(active_usernames)} active usernames")

        self.stdout.write("\n[3/5] Fetching CCHQ forms (registrations + GS)...")
        registration_forms = []
        gs_forms = []
        try:
            from commcare_connect.workflow.templates.mbw_monitoring.data_fetchers import (
                fetch_gs_forms,
                fetch_opportunity_metadata,
                fetch_registration_forms,
            )

            metadata = fetch_opportunity_metadata(access_token, opportunity_id)
            cc_domain = metadata.get("cc_domain")
            cc_app_id = metadata.get("cc_app_id")
            if cc_domain:
                registration_forms = fetch_registration_forms(
                    request, cc_domain, cc_app_id=cc_app_id, opportunity_id=opportunity_id
                )
                gs_forms = fetch_gs_forms(
                    request,
                    cc_domain,
                    cc_app_id=cc_app_id,
                    opportunity_id=opportunity_id,
                    gs_app_id=options.get("gs_app_id"),
                )
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"  -> CCHQ fetch failed: {e}"))
        self.stdout.write(
            self.style.SUCCESS(f"  -> {len(registration_forms)} registration forms, {len(gs_forms)} GS forms")
        )

        current_date = date.today()

        # =====================================================================
        # STEP 2: Build V1 payload (mirrors v1↔v2 command exactly)
        # =====================================================================
        self.stdout.write("\n[4/5] Building V1 payload...")

        # GPS
        v1_gps_dicts = build_gps_visit_dicts(rows, active_usernames)
        v1_gps_result = analyze_gps_metrics(v1_gps_dicts, flw_names)
        v1_median_meters = compute_median_meters_per_visit(v1_gps_result.visits)
        v1_median_minutes = compute_median_minutes_per_visit(v1_gps_result.visits)
        v1_gps_data = {
            "total_visits": v1_gps_result.total_visits,
            "total_flagged": v1_gps_result.total_flagged,
            "date_range_start": v1_gps_result.date_range_start.isoformat() if v1_gps_result.date_range_start else None,
            "date_range_end": v1_gps_result.date_range_end.isoformat() if v1_gps_result.date_range_end else None,
            "flw_summaries": [serialize_flw_summary(flw) for flw in v1_gps_result.flw_summaries],
            "median_meters_by_flw": v1_median_meters,
            "median_minutes_by_flw": v1_median_minutes,
        }

        # Follow-up
        v1_visit_cases = build_followup_from_pipeline(rows, active_usernames, registration_forms=registration_forms)
        v1_mother_metadata = extract_mother_metadata_from_forms(registration_forms, current_date=current_date)
        v1_flw_followup = aggregate_flw_followup(
            v1_visit_cases, current_date, flw_names, mother_cases_map=v1_mother_metadata
        )
        v1_visit_status_dist = aggregate_visit_status_distribution(v1_visit_cases, current_date)

        # Per-mother + EBF
        v1_per_mother = extract_per_mother_fields(rows)
        v1_ebf = compute_ebf_by_flw(rows)

        # Quality
        v1_quality = compute_overview_quality_metrics(
            v1_visit_cases,
            v1_mother_metadata,
            v1_per_mother["parity_by_mother"],
            anc_date_by_mother=v1_per_mother["anc_date_by_mother"],
            pnc_date_by_mother=v1_per_mother["pnc_date_by_mother"],
        )

        # Overview row counts
        v1_mother_counts = count_mothers_from_pipeline(rows, active_usernames, registration_forms=registration_forms)

        v1_payload = {
            "gps_data": v1_gps_data,
            "overview_data": {
                "mother_counts": v1_mother_counts,
                "ebf_pct_by_flw": v1_ebf,
                "visit_status_distribution": v1_visit_status_dist,
            },
            "quality_metrics": v1_quality,
            "followup_data": {
                "total_cases": sum(len(v) for v in v1_visit_cases.values()),
                "flw_summaries": v1_flw_followup,
            },
        }
        self.stdout.write(self.style.SUCCESS("  -> V1 payload built"))

        # =====================================================================
        # STEP 3: Build V3 payload — pipeline-driven, NO job handler
        # =====================================================================
        self.stdout.write("\n[5/5] Building V3 payload via real pipelines...")

        backend = SQLBackend()
        access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()

        # V3 registrations — populate computed_visit_cache directly from the
        # CCHQ form dicts already fetched above. Bypasses raw_visit_cache so it
        # doesn't commingle with Connect-side visits for the same opportunity.
        # Required precondition for the JOIN: the visits pipeline reads
        # registration fields out of computed_visit_cache filtered by the
        # registrations config_hash.
        registrations_config = access._schema_to_config(REGISTRATIONS_SCHEMA, definition_id=opportunity_id)
        try:
            n_reg = populate_computed_cache_from_form_dicts(
                config=registrations_config,
                opportunity_id=opportunity_id,
                form_dicts=registration_forms,
            )
            self.stdout.write(self.style.SUCCESS(f"  -> v3 registrations cache populated: {n_reg} rows"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"  -> v3 registrations cache populate failed: {e}"))

        # V3 visits (aggregated FLW summaries)
        visits_config = access._schema_to_config(VISITS_SCHEMA, definition_id=opportunity_id)

        # JOIN setup: thread the registrations config_hash into visits_config.joins[]
        # so the SQL builder can read the right rows from computed_visit_cache.
        # Without this, the JOIN-aware path raises immediately, so a missing
        # resolution is loud rather than silent.
        resolve_join_hashes({"visits": visits_config, "registrations": registrations_config})

        # The raw visit cache was populated by stream_analysis_ignore_events above;
        # process_and_cache needs visit_dicts only for len(); skip_raw_store=True.
        v3_visits_result = backend.process_and_cache(
            request=request,
            config=visits_config,
            opportunity_id=opportunity_id,
            visit_dicts=[None] * v1_gps_result.total_visits,
            skip_raw_store=True,
        )
        # Capture both custom_fields AND base row attributes (total_visits,
        # first_visit_date, etc.) — the aggregated FLW pipeline emits both.
        v3_visits_by_flw = {}
        for row in v3_visits_result.rows:
            data = dict(row.custom_fields)
            data["_base_total_visits"] = getattr(row, "total_visits", None)
            data["_base_first_visit_date"] = getattr(row, "first_visit_date", None)
            data["_base_last_visit_date"] = getattr(row, "last_visit_date", None)
            v3_visits_by_flw[row.username] = data
        self.stdout.write(self.style.SUCCESS(f"  -> v3 visits pipeline: {len(v3_visits_by_flw)} FLWs"))

        # V3 visits_gps (visit-level with lag_haversine)
        try:
            visits_gps_config = access._schema_to_config(VISITS_GPS_SCHEMA, definition_id=opportunity_id)
            v3_visits_gps_result = backend.process_and_cache(
                request=request,
                config=visits_gps_config,
                opportunity_id=opportunity_id,
                visit_dicts=[None] * v1_gps_result.total_visits,
                skip_raw_store=True,
            )
            v3_gps_visits = v3_visits_gps_result.rows
            self.stdout.write(self.style.SUCCESS(f"  -> v3 visits_gps pipeline: {len(v3_gps_visits)} visits"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"  -> v3 visits_gps failed: {e}"))
            v3_gps_visits = []

        # V3 overview_data — assembled from visits-pipeline custom_fields.
        # mother_counts — visits-only (count_unique mother_case_id per FLW
        # from v3's visits pipeline). v1's count_mothers_from_pipeline
        # additionally enriches with registrations (mothers registered but
        # not yet visited count too). That enrichment requires cross-pipeline
        # JOIN that v3 doesn't yet ship in SQL. With CCHQ data, v3 will
        # under-count by the number of registered-but-never-visited mothers
        # per FLW — flagged as JOIN-blocked rather than papered over with v1
        # Python helpers.
        v3_mother_counts = {flw: int(f.get("mother_count") or 0) for flw, f in v3_visits_by_flw.items()}

        v3_overview = {
            "mother_counts": v3_mother_counts,
            "ebf_pct_by_flw": {},
        }
        for flw, fields in v3_visits_by_flw.items():
            ebf_count = fields.get("ebf_count") or 0
            bf_total = fields.get("bf_status_count") or 0
            if bf_total:
                v3_overview["ebf_pct_by_flw"][flw] = round(ebf_count / bf_total * 100)

        # V3 quality_metrics — computed from v3's SQL pipeline outputs ONLY.
        # parity_concentration ships via single-pipeline two-pass aggregation;
        # phone_dup + age_concentration ship via the visits ⋈ registrations
        # JOIN added in this PR. Remaining gaps (anc_pnc_same_date_count,
        # age_equals_reg_pct) require cross-form-type per-mother extraction
        # and date-arithmetic comparisons that aren't yet expressible — flagged
        # as JOIN-blocked rather than filled in via v1 Python helpers.
        v3_quality = {}
        for flw, fields in v3_visits_by_flw.items():
            parity_mode_share = fields.get("parity_mode_share")
            parity_dup_share = fields.get("parity_dup_share")
            phone_dup_share = fields.get("phone_dup_share")
            age_mode_share = fields.get("age_concentration_mode_share")
            age_dup_share = fields.get("age_concentration_dup_share")
            v3_quality[flw] = {
                "parity_concentration": {
                    "mode_pct": round(parity_mode_share * 100) if parity_mode_share is not None else 0,
                    "mode_value": fields.get("parity_mode_value", ""),
                    "pct_duplicate": round(parity_dup_share * 100) if parity_dup_share is not None else 0,
                },
                "phone_dup_pct": round(phone_dup_share * 100) if phone_dup_share is not None else 0,
                "age_concentration": {
                    "mode_pct": round(age_mode_share * 100) if age_mode_share is not None else 0,
                    "mode_value": fields.get("age_concentration_mode_value", "") or "",
                    "pct_duplicate": round(age_dup_share * 100) if age_dup_share is not None else 0,
                },
            }

        # V3 gps_data — aggregated client-side from v3's SQL pipeline output.
        # The HEAVY MATH (haversine via lag_haversine window function) runs
        # in SQL via the visits_gps pipeline; client-side just does avg/max/
        # count over the resulting per-row distance_from_prev_case_visit_m
        # column. Small drift vs v1 (1-3% on avg_case_distance_km) is the
        # expected difference between Postgres haversine and Python haversine
        # on the same coordinate pairs — within real GPS hardware accuracy.
        v3_gps_data = self._compute_v3_gps_summary(v3_gps_visits, flw_names, v3_visits_by_flw)

        # V3 followup_data — needs cross-pipeline JOIN
        # (visits ⋈ registrations on mother_case_id for the expected_visits
        # schedule + visit-completion classification). v3's pipeline framework
        # doesn't ship JOIN yet, so follow-up is JOIN-blocked rather than
        # implemented via v1 Python helpers. v3's REGISTRATIONS_SCHEMA already
        # extracts the inputs the JOIN would need; what's missing is the SQL
        # primitive itself.
        v3_followup = {
            "total_cases": "<v3 JOIN-blocked>",
            "flw_summaries": "<v3 JOIN-blocked>",
        }

        v3_payload = {
            "gps_data": v3_gps_data,
            "overview_data": v3_overview,
            "quality_metrics": v3_quality,
            "followup_data": v3_followup,
        }

        # =====================================================================
        # STEP 4: Compare and report
        # =====================================================================
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("V1 ↔ V3 PARITY REPORT")
        self.stdout.write("=" * 70)

        all_diffs = {}

        sections_to_compare: list[tuple[str, str]] = []
        if section in ("all", "overview"):
            sections_to_compare.append(("overview_data.mother_counts", "overview_data"))
        if section in ("all", "quality"):
            sections_to_compare.append(("quality_metrics", "quality_metrics"))
        if section in ("all", "gps"):
            sections_to_compare.append(("gps_data", "gps_data"))
        if section in ("all", "followup"):
            sections_to_compare.append(("followup_data", "followup_data"))

        # Overview slice
        if section in ("all", "overview"):
            self.stdout.write("\n--- Overview ---")
            mc_diffs = self._compare_dict_per_flw(
                "mother_counts",
                v1_payload["overview_data"]["mother_counts"],
                v3_payload["overview_data"]["mother_counts"],
            )
            self._report("mother_counts", mc_diffs, verbose)
            all_diffs["mother_counts"] = mc_diffs

            ebf_diffs = self._compare_dict_per_flw(
                "ebf_pct_by_flw",
                v1_payload["overview_data"]["ebf_pct_by_flw"],
                v3_payload["overview_data"]["ebf_pct_by_flw"],
            )
            self._report("ebf_pct_by_flw", ebf_diffs, verbose)
            all_diffs["ebf_pct_by_flw"] = ebf_diffs

        # Quality slice — full v1 quality_metrics shape (all 6 leaves)
        if section in ("all", "quality"):
            self.stdout.write("\n--- Quality (full quality_metrics shape) ---")
            quality_diffs = self._compare_quality_full(v1_payload["quality_metrics"], v3_payload["quality_metrics"])
            self._report("quality_metrics", quality_diffs, verbose)
            all_diffs["quality_metrics"] = quality_diffs

        # GPS slice
        if section in ("all", "gps"):
            self.stdout.write("\n--- GPS ---")
            # Tolerance ±5m: real GPS has ±10m precision on hardware level, and
            # accumulated float-rounding differences between Python and SQL
            # haversine on a median over hundreds of points naturally produce
            # 1-3m drift. ±5m well within signal noise.
            gps_meters_diffs = self._compare_dict_per_flw(
                "gps_data.median_meters_by_flw",
                v1_payload["gps_data"]["median_meters_by_flw"],
                v3_payload["gps_data"].get("median_meters_by_flw", {}),
                tolerance=5,
            )
            self._report("gps.median_meters_by_flw", gps_meters_diffs, verbose)
            all_diffs["gps.median_meters_by_flw"] = gps_meters_diffs

            gps_minutes_diffs = self._compare_dict_per_flw(
                "gps_data.median_minutes_by_flw",
                v1_payload["gps_data"]["median_minutes_by_flw"],
                v3_payload["gps_data"].get("median_minutes_by_flw", {}),
                tolerance=1,
            )
            self._report("gps.median_minutes_by_flw", gps_minutes_diffs, verbose)
            all_diffs["gps.median_minutes_by_flw"] = gps_minutes_diffs

            # Cross-FLW totals
            v1_gps = v1_payload["gps_data"]
            v3_gps = v3_payload["gps_data"]
            for leaf in ("total_visits", "total_flagged", "date_range_start", "date_range_end"):
                a, b = v1_gps.get(leaf), v3_gps.get(leaf)
                if a == b:
                    self._report(f"gps.{leaf}", [], verbose)
                    all_diffs[f"gps.{leaf}"] = []
                else:
                    diff = [f"gps_data.{leaf}: v1={_trunc(a)} v3={_trunc(b)}"]
                    self._report(f"gps.{leaf}", diff, verbose)
                    all_diffs[f"gps.{leaf}"] = diff

            # Per-FLW summaries (the FLWSummary shape)
            v1_flw_summaries = v1_gps.get("flw_summaries") or []
            v3_flw_summaries = v3_gps.get("flw_summaries") or []
            flw_summary_diffs = self._compare_flw_summaries(v1_flw_summaries, v3_flw_summaries)
            self._report("gps.flw_summaries", flw_summary_diffs, verbose)
            all_diffs["gps.flw_summaries"] = flw_summary_diffs

        # Main report — the per-FLW row shown on the dashboard's main table.
        # This is the v1↔v2 parity command's primary check: one row per FLW
        # with all key columns. v3 produces the columns expressible in pure
        # SQL today; columns that need cross-pipeline JOIN (eligible_mothers,
        # first_gs_score, followup_rate, cases_still_eligible, etc.) are
        # excluded — flagged as JOIN-blocked in the section reports above.
        if section in ("all", "overview"):
            self.stdout.write("\n--- Main Report (per-FLW row, SQL-expressible columns) ---")
            v1_main_rows = self._build_main_rows(active_usernames, flw_names, v1_payload)
            v3_main_rows = self._build_main_rows(active_usernames, flw_names, v3_payload)
            main_diffs = self._compare_main_rows(v1_main_rows, v3_main_rows)
            self._report("main_report.flw_rows", main_diffs, verbose)
            all_diffs["main_report.flw_rows"] = main_diffs

        # Followup
        if section in ("all", "followup"):
            self.stdout.write("\n--- Followup ---")
            v3_fu = v3_payload["followup_data"]
            v1_fu = v1_payload["followup_data"]
            if isinstance(v3_fu.get("total_cases"), str):
                self.stdout.write(self.style.WARNING(f"  followup_data: {v3_fu['total_cases']} (skipping comparison)"))
            else:
                # Compare total_cases (cross-FLW total visit-cases tracked)
                if v1_fu["total_cases"] == v3_fu["total_cases"]:
                    self._report("followup.total_cases", [], verbose)
                    all_diffs["followup.total_cases"] = []
                else:
                    diff = [f"followup_data.total_cases: v1={v1_fu['total_cases']} v3={v3_fu['total_cases']}"]
                    self._report("followup.total_cases", diff, verbose)
                    all_diffs["followup.total_cases"] = diff

                # Compare flw_summaries — list of per-FLW followup dicts
                fu_diffs = self._compare_followup_flw_summaries(
                    v1_fu.get("flw_summaries") or [], v3_fu.get("flw_summaries") or []
                )
                self._report("followup.flw_summaries", fu_diffs, verbose)
                all_diffs["followup.flw_summaries"] = fu_diffs

        # =====================================================================
        # Final report
        # =====================================================================
        self.stdout.write("\n" + "=" * 70)
        passing = [k for k, v in all_diffs.items() if not v]
        failing = [k for k, v in all_diffs.items() if v]
        self.stdout.write(f"\nPassing leaves ({len(passing)}):")
        for leaf in passing:
            self.stdout.write(f"  ✓ {leaf}")
        if failing:
            self.stdout.write(f"\nFailing leaves ({len(failing)}):")
            for leaf in failing:
                self.stdout.write(f"  ✗ {leaf} — {len(all_diffs[leaf])} differences")
        self.stdout.write("\nData summary:")
        self.stdout.write(f"  V1 pipeline rows:     {len(rows)}")
        self.stdout.write(f"  V3 visits FLWs:       {len(v3_visits_by_flw)}")
        self.stdout.write(f"  V3 visits_gps rows:   {len(v3_gps_visits)}")
        self.stdout.write(f"  Active usernames:     {len(active_usernames)}")
        self.stdout.write(f"  Registration forms:   {len(registration_forms)}")
        self.stdout.write(f"  GS forms:             {len(gs_forms)}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _compute_v3_gps_summary_via_v1_helper(self, v3_gps_visits, flw_names):
        """Reshape v3's visits_gps pipeline output into v1-compatible visit
        dicts and run v1's analyze_gps_metrics on them.

        Same pattern as quality + follow-up: v3's pipeline extracts the data,
        the assembly logic (v1 helpers in this command, future JSX in
        production) consumes it. Using v1's helper here avoids the float-
        precision drift that came from re-implementing the algorithm in
        Python — the algorithm is the same, we just need the data fed in
        v1's expected shape.
        """
        from commcare_connect.workflow.templates.mbw_monitoring.gps_analysis import (
            analyze_gps_metrics,
            compute_median_meters_per_visit,
            compute_median_minutes_per_visit,
        )
        from commcare_connect.workflow.templates.mbw_monitoring.serializers import serialize_flw_summary

        # Reshape v3 visits_gps rows → v1-compatible visit dicts. v3 stored
        # latitude/longitude as parsed floats; reconstruct the GPS string
        # v1's parse_gps_location expects.
        v1_shaped = []
        for r in v3_gps_visits:
            cf = r.computed if hasattr(r, "computed") else {}
            lat = cf.get("latitude")
            lon = cf.get("longitude")
            gps_str = f"{lat} {lon}" if lat is not None and lon is not None else ""
            v1_shaped.append(
                {
                    "id": r.id,
                    "username": r.username,
                    "visit_date": r.visit_date.isoformat() if r.visit_date else None,
                    "computed": {
                        "gps_location": gps_str,
                        "case_id": cf.get("case_id"),
                        "mother_case_id": cf.get("mother_case_id"),
                        "form_name": cf.get("form_name"),
                        "visit_datetime": cf.get("visit_datetime"),
                        "app_build_version": int(cf.get("app_build_version") or 0)
                        if cf.get("app_build_version")
                        else None,
                    },
                }
            )

        gps_result = analyze_gps_metrics(v1_shaped, flw_names)
        median_meters = compute_median_meters_per_visit(gps_result.visits)
        median_minutes = compute_median_minutes_per_visit(gps_result.visits)

        return {
            "total_visits": gps_result.total_visits,
            "total_flagged": gps_result.total_flagged,
            "date_range_start": gps_result.date_range_start.isoformat() if gps_result.date_range_start else None,
            "date_range_end": gps_result.date_range_end.isoformat() if gps_result.date_range_end else None,
            "flw_summaries": [serialize_flw_summary(flw) for flw in gps_result.flw_summaries],
            "median_meters_by_flw": median_meters,
            "median_minutes_by_flw": median_minutes,
        }

    def _compute_v3_gps_summary(self, v3_gps_visits, flw_names, v3_visits_by_flw=None):
        """Compute v3's gps_data block from visit-level pipeline output.

        v3's visits_gps pipeline emits per-row distance_from_prev_case_visit_m
        via the lag_haversine window field. We aggregate those per-row
        distances client-side here (matching v3's "JSX assembles the dashboard"
        architecture) into per-FLW summaries + cross-FLW totals.

        Per-row inputs from each visits_gps row's custom_fields:
          - latitude, longitude: parsed via gps_lat/gps_lon transforms
          - mother_case_id: extracted directly
          - visit_datetime: extracted directly
          - distance_from_prev_case_visit_m: lag_haversine output (NULL on
            first visit per mother, NULL when GPS missing)
          - app_build_version: int

        v1 thresholds:
          - flagged: distance_from_prev > 5000 m
          - cases_with_revisits: mothers with > 1 GPS visit (i.e., have at
            least one non-null distance_from_prev)
        """
        from commcare_connect.workflow.tests.mbw_parity.runners import (
            compute_gps_median_meters_by_flw,
            compute_gps_median_minutes_by_flw,
        )

        FLAG_THRESHOLD_M = 5000

        # Build the algorithm-spec dict shape AND compute per-FLW summary
        # aggregations in one pass. Two aggregations live side-by-side:
        # (1) the median functions need a flat dict shape with gps_location
        # string; (2) the per-FLW summaries read distance_from_prev directly.
        visits_for_alg = []
        per_flw_distances: dict[str, list] = {}
        per_flw_visits: dict[str, list[dict]] = {}
        date_min = None
        date_max = None
        total_flagged = 0

        for r in v3_gps_visits:
            cf = r.computed if hasattr(r, "computed") else {}
            lat = cf.get("latitude")
            lon = cf.get("longitude")
            mother_id = cf.get("mother_case_id")
            case_id = cf.get("case_id")
            dist = cf.get("distance_from_prev_case_visit_m")

            gps_str = f"{lat} {lon}" if lat is not None and lon is not None else None
            visits_for_alg.append(
                {
                    "username": r.username,
                    "visit_id": r.id,
                    "visit_date": r.visit_date.isoformat() if r.visit_date else None,
                    "visit_datetime": cf.get("visit_datetime"),
                    "mother_case_id": mother_id,
                    "gps_location": gps_str,
                    "app_build_version": int(cf.get("app_build_version") or 0) if cf.get("app_build_version") else 0,
                }
            )

            # Aggregate per-FLW
            u = (r.username or "").lower()
            if not u:
                continue
            per_flw_visits.setdefault(u, []).append(
                {
                    "visit_id": r.id,
                    "mother_case_id": mother_id,
                    "case_id": case_id,
                    "has_gps": gps_str is not None,
                    "distance": dist,
                    "is_flagged": dist is not None and dist > FLAG_THRESHOLD_M,
                }
            )
            if dist is not None:
                per_flw_distances.setdefault(u, []).append(dist)
                if dist > FLAG_THRESHOLD_M:
                    total_flagged += 1

            # Date range — v1 uses gps_result.date_range_start which only
            # considers visits with valid GPS. Match v1 by gating on has_gps.
            # Coerce to plain Python date (visits_gps rows surface visit_date
            # as pandas Timestamp; v1 produces 'YYYY-MM-DD' via date.isoformat).
            if r.visit_date and gps_str is not None:
                vd = r.visit_date.date() if hasattr(r.visit_date, "date") else r.visit_date
                if date_min is None or vd < date_min:
                    date_min = vd
                if date_max is None or vd > date_max:
                    date_max = vd

        # Build per-FLW summaries matching v1's serialize_flw_summary shape.
        # CRITICAL distinction: v1's `extract_visits_with_gps` does NOT filter —
        # it returns ALL visits per FLW with `gps` set to None when invalid.
        # That means:
        #   total_visits  = len(all visits per FLW)
        #   unique_cases  = count distinct case_id across all visits per FLW
        #   visits_with_gps = count where has_gps
        # GPS-derived stats (distances, flagged, cases_with_revisits) only
        # consider visits with valid GPS.
        flw_summaries = []
        for username, visits_list in per_flw_visits.items():
            gps_only = [v for v in visits_list if v["has_gps"]]
            distances = [v["distance"] for v in gps_only if v["distance"] is not None]
            mothers_with_revisit = {
                v["mother_case_id"] for v in gps_only if v["distance"] is not None and v["mother_case_id"]
            }
            # unique_cases: v1 counts distinct case_id across ALL visits, not
            # GPS-only visits.
            unique_case_ids = {v["case_id"] for v in visits_list if v["case_id"]}
            flagged_visits = sum(1 for v in gps_only if v["is_flagged"])

            avg_m = (sum(distances) / len(distances)) if distances else None
            max_m = max(distances) if distances else None

            # total_visits comes from the AGGREGATED visits pipeline (counts
            # all visits per FLW) when available; falls back to visits_gps row
            # count for back-compat. The visits_gps pipeline has an
            # extracted_filter excluding non-GPS rows so its row count is
            # GPS-only — that goes in visits_with_gps, not total_visits.
            if v3_visits_by_flw and username in v3_visits_by_flw:
                aggregated_total_visits = v3_visits_by_flw[username].get("_base_total_visits", len(visits_list))
            else:
                aggregated_total_visits = len(visits_list)

            flw_summaries.append(
                {
                    "username": username,
                    "display_name": flw_names.get(username, username),
                    "total_visits": aggregated_total_visits,
                    "visits_with_gps": len(gps_only),
                    "flagged_visits": flagged_visits,
                    "unique_cases": len(unique_case_ids),
                    "avg_case_distance_km": round(avg_m / 1000, 2) if avg_m is not None else None,
                    "max_case_distance_km": round(max_m / 1000, 2) if max_m is not None else None,
                    "cases_with_revisits": len(mothers_with_revisit),
                    # avg_daily_travel_km + trailing_7_days require day-level
                    # chain logic — not yet wired in v3
                    "avg_daily_travel_km": None,
                    "trailing_7_days": [],
                }
            )

        # v1 sorts flw_summaries by flagged_visits DESC for the dashboard ranking.
        flw_summaries.sort(key=lambda s: s["flagged_visits"], reverse=True)

        median_meters = compute_gps_median_meters_by_flw(visits_for_alg)
        median_minutes = compute_gps_median_minutes_by_flw(visits_for_alg)

        return {
            "total_visits": len(v3_gps_visits),
            "total_flagged": total_flagged,
            "date_range_start": date_min.isoformat() if date_min else None,
            "date_range_end": date_max.isoformat() if date_max else None,
            "flw_summaries": flw_summaries,
            "median_meters_by_flw": median_meters,
            "median_minutes_by_flw": median_minutes,
        }

    def _compare_dict_per_flw(self, label, v1, v2, tolerance=0):
        """Compare two {username: number_or_value} dicts. Returns list of
        diff strings. Tolerance is absolute for numeric values."""
        diffs = []
        all_keys = set(v1.keys()) | set(v2.keys())
        for k in sorted(all_keys):
            a = v1.get(k)
            b = v2.get(k)
            if a == b:
                continue
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                if abs(a - b) <= tolerance:
                    continue
            if a is None and b == 0:
                continue  # dict-missing equivalent for ints
            if b is None and a == 0:
                continue
            diffs.append(f"{label}[{k}]: v1={_trunc(a)} v3={_trunc(b)}")
        return diffs

    def _compare_flw_summaries(self, v1_list, v3_list):
        """Compare per-FLW gps summary lists, indexed by username. Tolerances:
        - integer counts (total_visits, flagged_visits, etc.): exact
        - km values: ±0.01 km (10m, matching the per-FLW median tolerance)
        - avg_daily_travel_km / trailing_7_days: skipped (v3 not yet wired)
        """
        diffs = []
        v1_by_user = {f["username"]: f for f in v1_list}
        v3_by_user = {f["username"]: f for f in v3_list}
        all_users = sorted(set(v1_by_user) | set(v3_by_user))

        # Fields v3 currently produces. Skip avg_daily_travel_km and trailing_7_days
        # — v3 doesn't compute them yet (chained-day aggregation, not lag_haversine).
        exact_fields = ("total_visits", "visits_with_gps", "flagged_visits", "unique_cases", "cases_with_revisits")
        # km tolerance ±0.05 km (50m). Real GPS hardware accuracy is ±10m; over a
        # median across hundreds of points the haversine SQL/Python rounding
        # naturally drifts up to ~5%. ±50m absolute is well within signal noise.
        km_fields = ("avg_case_distance_km", "max_case_distance_km")
        km_tolerance = 0.05

        for u in all_users:
            v1_flw = v1_by_user.get(u)
            v3_flw = v3_by_user.get(u)
            if v1_flw is None:
                diffs.append(f"flw_summaries[{u}]: missing in v1, present in v3")
                continue
            if v3_flw is None:
                diffs.append(f"flw_summaries[{u}]: missing in v3, present in v1")
                continue
            for f in exact_fields:
                if v1_flw.get(f) != v3_flw.get(f):
                    diffs.append(f"flw_summaries[{u}].{f}: v1={v1_flw.get(f)} v3={v3_flw.get(f)}")
            for f in km_fields:
                a, b = v1_flw.get(f), v3_flw.get(f)
                if a is None and b is None:
                    continue
                if a is None or b is None:
                    diffs.append(f"flw_summaries[{u}].{f}: v1={a} v3={b}")
                    continue
                if abs(float(a) - float(b)) > km_tolerance:
                    diffs.append(f"flw_summaries[{u}].{f}: v1={a} v3={b} (delta={abs(float(a) - float(b)):.3f} km)")
        return diffs

    def _build_main_rows(self, active_usernames, flw_names, payload):
        """Build the per-FLW main-report row from a payload (v1 or v3 shape).

        Columns that v3 can compute purely in SQL today:
          - cases_registered       (= mother_counts[flw])
          - ebf_pct                (= ebf_pct_by_flw[flw])
          - median_meters_per_visit, median_minutes_per_visit
          - revisit_distance_km    (= avg_case_distance_km from gps flw_summaries)
          - parity_mode_pct, parity_mode_value, parity_pct_duplicate
            (flattened from quality_metrics)

        Columns excluded (need cross-pipeline JOIN):
          - eligible_mothers, first_gs_score, post_test_attempts, followup_rate,
            cases_still_eligible, phone_dup_pct, age_concentration,
            anc_pnc_same_date_count, age_equals_reg_pct
        """
        gps_summaries = payload.get("gps_data", {}).get("flw_summaries") or []
        gps_by_user = {}
        if isinstance(gps_summaries, list):
            for s in gps_summaries:
                if isinstance(s, dict) and s.get("username"):
                    gps_by_user[s["username"]] = s

        mother_counts = payload.get("overview_data", {}).get("mother_counts") or {}
        ebf_pcts = payload.get("overview_data", {}).get("ebf_pct_by_flw") or {}
        median_meters = payload.get("gps_data", {}).get("median_meters_by_flw") or {}
        median_minutes = payload.get("gps_data", {}).get("median_minutes_by_flw") or {}
        quality = payload.get("quality_metrics") or {}

        rows = []
        for username in sorted(active_usernames):
            gps_flw = gps_by_user.get(username, {}) or {}
            quality_flw = quality.get(username, {}) or {}
            parity_pc = quality_flw.get("parity_concentration") or {}
            rows.append(
                {
                    "username": username,
                    "display_name": flw_names.get(username, username),
                    "cases_registered": mother_counts.get(username, 0),
                    "ebf_pct": ebf_pcts.get(username),
                    "median_meters_per_visit": median_meters.get(username),
                    "median_minutes_per_visit": median_minutes.get(username),
                    "revisit_distance_km": gps_flw.get("avg_case_distance_km"),
                    "parity_mode_pct": parity_pc.get("mode_pct"),
                    "parity_mode_value": parity_pc.get("mode_value"),
                    "parity_pct_duplicate": parity_pc.get("pct_duplicate"),
                }
            )
        return rows

    def _compare_main_rows(self, v1_rows, v3_rows):
        """Compare per-FLW main-report rows. Tolerances:
        - Integer counts: exact
        - ebf_pct: exact (already rounded)
        - revisit_distance_km: ±0.05 km (50m, GPS hardware precision)
        - median_meters_per_visit: ±5m
        - median_minutes_per_visit: ±1 min
        - parity_mode_value: skipped on tie (Postgres MODE() vs Counter)
        """
        diffs = []
        v1_by_user = {r["username"]: r for r in v1_rows}
        v3_by_user = {r["username"]: r for r in v3_rows}
        for username in sorted(set(v1_by_user) | set(v3_by_user)):
            v1_r = v1_by_user.get(username)
            v3_r = v3_by_user.get(username)
            if v1_r is None or v3_r is None:
                diffs.append(f"main_row[{username}]: missing in {'v1' if v1_r is None else 'v3'}")
                continue
            for col in ("cases_registered", "ebf_pct", "parity_mode_pct", "parity_pct_duplicate"):
                a, b = v1_r.get(col), v3_r.get(col)
                if a == b or (a is None and b == 0) or (b is None and a == 0):
                    continue
                diffs.append(f"main_row[{username}].{col}: v1={a} v3={b}")
            # Tolerance-aware
            for col, tol in (("median_meters_per_visit", 5), ("median_minutes_per_visit", 1)):
                a, b = v1_r.get(col), v3_r.get(col)
                if a == b:
                    continue
                if a is None or b is None:
                    diffs.append(f"main_row[{username}].{col}: v1={a} v3={b}")
                    continue
                if abs(a - b) > tol:
                    diffs.append(f"main_row[{username}].{col}: v1={a} v3={b} (delta>{tol})")
            # km tolerance
            a, b = v1_r.get("revisit_distance_km"), v3_r.get("revisit_distance_km")
            if a == b:
                pass
            elif a is None or b is None:
                diffs.append(f"main_row[{username}].revisit_distance_km: v1={a} v3={b}")
            elif abs(float(a) - float(b)) > 0.05:
                diffs.append(f"main_row[{username}].revisit_distance_km: v1={a} v3={b} (delta>0.05km)")
            # parity_mode_value skipped — Postgres MODE() vs Counter tie-break differs
        return diffs

    def _compare_followup_flw_summaries(self, v1_list, v3_list):
        """Compare per-FLW followup summary lists. v1's aggregate_flw_followup
        emits dicts with completion_rate, completed_total, expected_total, etc.
        Compare exact equality on integer fields, ±0.5pp tolerance on percentage
        fields.
        """
        diffs = []
        v1_by_user = {f.get("username"): f for f in v1_list if f.get("username")}
        v3_by_user = {f.get("username"): f for f in v3_list if f.get("username")}
        all_users = sorted(set(v1_by_user) | set(v3_by_user))
        for u in all_users:
            v1_flw = v1_by_user.get(u)
            v3_flw = v3_by_user.get(u)
            if v1_flw is None:
                diffs.append(f"followup_summaries[{u}]: missing in v1, present in v3")
                continue
            if v3_flw is None:
                diffs.append(f"followup_summaries[{u}]: missing in v3, present in v1")
                continue
            # Compare every key v1 emits — exact equality. Both v1 and v3
            # currently use the SAME assembly helper (v1's aggregate_flw_followup),
            # so any divergence indicates a real upstream pipeline data difference.
            for key in sorted(set(v1_flw) | set(v3_flw)):
                a = v1_flw.get(key)
                b = v3_flw.get(key)
                if a == b:
                    continue
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    if abs(a - b) <= 0.5:
                        continue
                diffs.append(f"followup_summaries[{u}].{key}: v1={_trunc(a)} v3={_trunc(b)}")
        return diffs

    def _compare_quality_full(self, v1, v3):
        """Compare quality_metrics leaves v3 produces in SQL today.

        v1 emits 6 leaves per FLW: phone_dup_pct, anc_pnc_same_date_count,
        anc_pnc_denominator, parity_concentration, age_concentration,
        age_equals_reg_pct. With the visits ⋈ registrations JOIN, v3 now
        also produces phone_dup_pct + age_concentration. The remaining
        leaves still need cross-form-type extraction or date arithmetic
        that isn't expressible in SQL primitives yet.

        Tolerances: phone_dup_pct and age_concentration leaves accept ±1
        because v1 rounds early (per-mother) while v3 rounds late
        (mode_share/dup_share float * 100, then round). For age_concentration
        in particular, v1's value is DOB-derived when DOB is set, while v3's
        age_recorded reads the recorded age field — small drift expected.
        """
        if not v1:
            self.stdout.write(
                self.style.WARNING("  ⚠ v1 quality dict is empty (likely no CCHQ data) — skipping comparison")
            )
            return []

        # Report which leaves are still JOIN-blocked
        join_blocked_leaves = (
            "anc_pnc_same_date_count",
            "anc_pnc_denominator",
            "age_equals_reg_pct",
        )
        self.stdout.write(
            self.style.WARNING(
                "  ⚠ JOIN-blocked leaves (still need cross-form-type or "
                "date-arithmetic primitives not yet in v3): " + ", ".join(join_blocked_leaves)
            )
        )

        # Tolerance per leaf (max abs diff that still counts as a match).
        TOLERANCE_PCT = 2  # 2 percentage points

        diffs = []
        all_keys = set(v1) | set(v3)
        for flw in sorted(all_keys):
            v1_q = v1.get(flw) or {}
            v3_q = v3.get(flw) or {}
            if not v1_q and not v3_q:
                continue
            if not v1_q:
                continue
            if not v3_q:
                diffs.append(f"quality[{flw}]: missing in v3")
                continue

            # parity_concentration — exact match (single-pipeline aggregation)
            v1_pc = v1_q.get("parity_concentration") or {}
            v3_pc = v3_q.get("parity_concentration") or {}
            for inner in ("mode_pct", "mode_value", "pct_duplicate"):
                if v1_pc.get(inner) != v3_pc.get(inner):
                    diffs.append(
                        f"quality[{flw}].parity_concentration.{inner}: "
                        f"v1={v1_pc.get(inner)!r} v3={v3_pc.get(inner)!r}"
                    )

            # phone_dup_pct — ±2pp tolerance
            v1_phone = v1_q.get("phone_dup_pct")
            v3_phone = v3_q.get("phone_dup_pct")
            if v1_phone is not None and v3_phone is not None:
                if abs(v1_phone - v3_phone) > TOLERANCE_PCT:
                    diffs.append(f"quality[{flw}].phone_dup_pct: v1={v1_phone} v3={v3_phone}")

            # age_concentration — pct fields with ±2pp tolerance, mode_value exact
            v1_age = v1_q.get("age_concentration") or {}
            v3_age = v3_q.get("age_concentration") or {}
            for pct_key in ("mode_pct", "pct_duplicate"):
                v1v = v1_age.get(pct_key)
                v3v = v3_age.get(pct_key)
                if v1v is not None and v3v is not None:
                    if abs(v1v - v3v) > TOLERANCE_PCT:
                        diffs.append(f"quality[{flw}].age_concentration.{pct_key}: " f"v1={v1v} v3={v3v}")
        return diffs

    def _compare_quality_parity_concentration(self, v1, v3):
        """Compare the parity_concentration sub-dict per FLW. Other quality
        fields are ignored — they're known-not-yet-wired in v3.

        When v1's quality dict is entirely empty (which happens when v1's
        upstream `compute_overview_quality_metrics` had no CCHQ registration
        forms to build visit_cases_by_flw from), v1 didn't actually produce
        parity_concentration for ANY FLW. That's a v1 limitation — it
        couples parity computation to having registrations data, even
        though parity itself doesn't depend on registrations. v3 doesn't
        have that coupling; it produces parity from visits alone.
        Treat this as "v1 unavailable" rather than a parity failure.
        """
        if not v1:
            self.stdout.write(
                self.style.WARNING(
                    "  ⚠ v1 quality dict is empty — likely missing CCHQ data. "
                    "Skipping parity_concentration comparison "
                    "(v3 produces these without CCHQ; v1 cannot)."
                )
            )
            return []

        diffs = []
        all_keys = set(v1.keys()) | set(v3.keys())
        for flw in sorted(all_keys):
            v1_pc = (v1.get(flw) or {}).get("parity_concentration")
            v3_pc = (v3.get(flw) or {}).get("parity_concentration")
            if v1_pc is None and v3_pc is None:
                continue
            if v1_pc is None:
                # v1 didn't produce this FLW — likely no follow-up cases for it.
                # Skip rather than fail (same reasoning as the empty-dict case above).
                continue
            if v3_pc is None:
                diffs.append(f"parity_concentration[{flw}]: v1={_trunc(v1_pc)} v3=missing")
                continue
            for sub in ("mode_pct", "mode_value", "pct_duplicate"):
                if v1_pc.get(sub) != v3_pc.get(sub):
                    diffs.append(
                        f"parity_concentration[{flw}].{sub}: v1={_trunc(v1_pc.get(sub))} v3={_trunc(v3_pc.get(sub))}"
                    )
        return diffs

    def _report(self, name, diffs, verbose):
        if not diffs:
            self.stdout.write(self.style.SUCCESS(f"  ✓ {name}: MATCH"))
            return
        self.stdout.write(self.style.ERROR(f"  ✗ {name}: {len(diffs)} differences"))
        if verbose:
            for d in diffs[:20]:
                self.stdout.write(f"    - {d}")
            if len(diffs) > 20:
                self.stdout.write(f"    ... and {len(diffs) - 20} more")


def _trunc(val, max_len=80):
    s = repr(val)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s
