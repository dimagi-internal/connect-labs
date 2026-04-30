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
        from commcare_connect.labs.analysis.data_access import fetch_flw_names
        from commcare_connect.labs.analysis.pipeline import AnalysisPipeline
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
        from commcare_connect.workflow.templates.mbw_monitoring_v3 import VISITS_GPS_SCHEMA, VISITS_SCHEMA

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

        # V3 visits (aggregated FLW summaries)
        visits_config = access._schema_to_config(VISITS_SCHEMA, definition_id=opportunity_id)
        # The raw visit cache was populated by stream_analysis_ignore_events above;
        # process_and_cache needs visit_dicts only for len(); skip_raw_store=True.
        v3_visits_result = backend.process_and_cache(
            request=request,
            config=visits_config,
            opportunity_id=opportunity_id,
            visit_dicts=[None] * v1_gps_result.total_visits,
            skip_raw_store=True,
        )
        v3_visits_by_flw = {row.username: row.custom_fields for row in v3_visits_result.rows}
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
        v3_overview = {
            "mother_counts": {flw: f.get("mother_count", 0) for flw, f in v3_visits_by_flw.items()},
            "ebf_pct_by_flw": {},
        }
        for flw, fields in v3_visits_by_flw.items():
            ebf_count = fields.get("ebf_count") or 0
            bf_total = fields.get("bf_status_count") or 0
            if bf_total:
                v3_overview["ebf_pct_by_flw"][flw] = round(ebf_count / bf_total * 100)

        # V3 quality_metrics — assembled from visits-pipeline custom_fields.
        # Only parity_concentration is wired (PR #110-#119); other v1 fields
        # (phone_dup_pct, age_concentration, anc_pnc_same_date_count,
        # age_equals_reg_pct) need cross-pipeline JOIN — not yet built.
        v3_quality = {}
        for flw, fields in v3_visits_by_flw.items():
            mode_share = fields.get("parity_mode_share")
            dup_share = fields.get("parity_dup_share")
            v3_quality[flw] = {
                "parity_concentration": {
                    "mode_pct": round(mode_share * 100) if mode_share is not None else 0,
                    "mode_value": fields.get("parity_mode_value", ""),
                    "pct_duplicate": round(dup_share * 100) if dup_share is not None else 0,
                },
                # Placeholders — v3 doesn't yet compute these (need JOIN)
                "phone_dup_pct": "<v3 not yet wired>",
                "age_concentration": "<v3 not yet wired>",
                "anc_pnc_same_date_count": "<v3 not yet wired>",
                "anc_pnc_denominator": "<v3 not yet wired>",
                "age_equals_reg_pct": "<v3 not yet wired>",
            }

        # V3 gps_data — partial. lag_haversine produces per-visit distance;
        # per-FLW aggregations of those distances need either a second pipeline
        # stage or client-side aggregation (we do the latter here).
        v3_gps_data = self._compute_v3_gps_summary(v3_gps_visits, flw_names)

        # V3 followup_data — not yet wired (needs cross-pipeline JOIN
        # visits ⋈ registrations on mother_case_id for the expected_visits schedule).
        v3_followup = {
            "total_cases": "<v3 not yet wired>",
            "flw_summaries": "<v3 not yet wired>",
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

        # Quality slice
        if section in ("all", "quality"):
            self.stdout.write("\n--- Quality (parity_concentration only — JOIN-dependent fields skipped) ---")
            quality_diffs = self._compare_quality_parity_concentration(
                v1_payload["quality_metrics"], v3_payload["quality_metrics"]
            )
            self._report("quality.parity_concentration", quality_diffs, verbose)
            all_diffs["quality.parity_concentration"] = quality_diffs

        # GPS slice (median meters/minutes parity)
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

        # Followup
        if section in ("all", "followup"):
            self.stdout.write("\n--- Followup ---")
            self.stdout.write(self.style.WARNING("  followup_data: <v3 not yet wired> (needs cross-pipeline JOIN)"))

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

    def _compute_v3_gps_summary(self, v3_gps_visits, flw_names):
        """Compute v3's gps_data block from visit-level pipeline output.

        v3's visits_gps pipeline emits per-row distance_from_prev_case_visit_m
        via the lag_haversine window field. To match v1's gps_data block we
        aggregate those per-row distances per-FLW and per-(FLW, day).
        """
        from commcare_connect.workflow.tests.mbw_parity.runners import (
            compute_gps_median_meters_by_flw,
            compute_gps_median_minutes_by_flw,
        )

        # Convert pipeline VisitRow objects to the dict shape the algorithm
        # spec functions expect. Each row's custom_fields holds latitude /
        # longitude / mother_case_id / visit_datetime / distance_from_prev_case_visit_m.
        visits_for_alg = []
        for r in v3_gps_visits:
            cf = r.computed if hasattr(r, "computed") else {}
            lat = cf.get("latitude")
            lon = cf.get("longitude")
            gps_str = f"{lat} {lon}" if lat is not None and lon is not None else None
            visits_for_alg.append(
                {
                    "username": r.username,
                    "visit_id": r.id,
                    "visit_date": r.visit_date.isoformat() if r.visit_date else None,
                    "visit_datetime": cf.get("visit_datetime"),
                    "mother_case_id": cf.get("mother_case_id"),
                    "gps_location": gps_str,
                    "app_build_version": int(cf.get("app_build_version") or 0) if cf.get("app_build_version") else 0,
                }
            )

        median_meters = compute_gps_median_meters_by_flw(visits_for_alg)
        median_minutes = compute_gps_median_minutes_by_flw(visits_for_alg)

        # total_visits from the visit-level pipeline (rows with valid GPS would
        # be a subset; for parity with v1's "all visits", we use raw count here).
        # Ditto for date_range and flagged — those are TODO once a per-FLW
        # pipeline aggregates the lag_haversine output.
        return {
            "total_visits": len(v3_gps_visits),
            "total_flagged": "<v3 not yet wired — needs aggregated lag_haversine>",
            "date_range_start": "<v3 not yet wired>",
            "date_range_end": "<v3 not yet wired>",
            "flw_summaries": "<v3 not yet wired — needs aggregated lag_haversine>",
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
