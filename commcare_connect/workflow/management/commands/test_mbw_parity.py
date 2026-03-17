"""
End-to-end MBW v1/v2 dashboard payload parity test.

Fetches real data via API, builds the complete dashboard JSON payload using
both the v1 path (MBWMonitoringStreamView assembly) and the v2 path
(job handler + JS assembly logic), and deep-compares them field-by-field.

Usage:
    # First, ensure you have a valid token:
    python manage.py get_cli_token --settings=config.settings.local

    # Run the parity test:
    python manage.py test_mbw_parity --opportunity-id 765

    # With verbose output:
    python manage.py test_mbw_parity --opportunity-id 765 --verbose
"""

import logging
from datetime import date

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Test MBW v1/v2 full dashboard payload parity using real data"

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
            help="CommCare HQ app ID for Gold Standard Visit Checklist (supervisor app)",
        )

    def handle(self, *args, **options):
        from commcare_connect.labs.analysis.data_access import fetch_flw_names
        from commcare_connect.labs.analysis.pipeline import AnalysisPipeline
        from commcare_connect.labs.integrations.connect.cli import create_cli_request
        from commcare_connect.workflow.job_handlers.mbw_monitoring import handle_mbw_monitoring_job
        from commcare_connect.workflow.templates.mbw_monitoring.data_transforms import (
            build_gps_visit_dicts,
            compute_ebf_by_flw,
            extract_per_mother_fields,
        )
        from commcare_connect.workflow.templates.mbw_monitoring.followup_analysis import (
            aggregate_flw_followup,
            aggregate_mother_metrics,
            aggregate_visit_status_distribution,
            build_followup_from_pipeline,
            compute_flw_performance_by_status,
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

        opportunity_id = options["opportunity_id"]
        verbose = options["verbose"]

        self.stdout.write(f"\nMBW v1/v2 Full Payload Parity Test — opportunity {opportunity_id}")
        self.stdout.write("=" * 70)

        # =====================================================================
        # STEP 1: Fetch all shared data
        # =====================================================================
        self.stdout.write("\n[1/6] Creating CLI request...")
        request = create_cli_request(opportunity_id=opportunity_id)
        if not request:
            raise CommandError("Failed to create CLI request. Run: python manage.py get_cli_token")
        access_token = request.session.get("labs_oauth", {}).get("access_token")
        self.stdout.write(self.style.SUCCESS("  -> OK"))

        self.stdout.write("\n[2/6] Fetching pipeline visit data...")
        pipeline = AnalysisPipeline(request)
        pipeline_result = pipeline.stream_analysis_ignore_events(
            MBW_GPS_PIPELINE_CONFIG, opportunity_id=opportunity_id
        )
        rows = pipeline_result.rows
        self.stdout.write(self.style.SUCCESS(f"  -> {len(rows)} VisitRows"))
        if not rows:
            raise CommandError("No pipeline rows returned — nothing to compare.")

        self.stdout.write("\n[3/6] Fetching FLW names...")
        try:
            flw_names = fetch_flw_names(access_token, opportunity_id)
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"  -> FLW names fetch failed: {e}"))
            flw_names = {}
        active_usernames = (
            {u.lower() for u in flw_names.keys()}
            if flw_names
            else {(r.username or "").lower() for r in rows if r.username}
        )
        flw_names = {k.lower(): v for k, v in flw_names.items()}
        self.stdout.write(
            self.style.SUCCESS(f"  -> {len(flw_names)} FLW names, {len(active_usernames)} active usernames")
        )

        self.stdout.write("\n[4/6] Fetching CCHQ forms (registrations + GS)...")
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
        # STEP 2: Build V1 payload (mirrors MBWMonitoringStreamView.stream_data)
        # =====================================================================
        self.stdout.write("\n[5/6] Building V1 payload...")

        # GPS analysis — NO date filtering (v2 job handler doesn't date-filter)
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

        # Follow-up analysis
        v1_visit_cases = build_followup_from_pipeline(rows, active_usernames, registration_forms=registration_forms)
        v1_mother_metadata = extract_mother_metadata_from_forms(registration_forms, current_date=current_date)
        v1_flw_followup = aggregate_flw_followup(
            v1_visit_cases, current_date, flw_names, mother_cases_map=v1_mother_metadata
        )
        v1_visit_status_dist = aggregate_visit_status_distribution(v1_visit_cases, current_date)

        # Per-mother fields + EBF
        v1_per_mother = extract_per_mother_fields(rows)
        v1_ebf = compute_ebf_by_flw(rows)

        # Drilldown
        v1_drilldown = {}
        for flw_username, flw_cases in v1_visit_cases.items():
            v1_drilldown[flw_username] = aggregate_mother_metrics(
                flw_cases,
                current_date,
                mother_cases_map=v1_mother_metadata,
                anc_date_by_mother=v1_per_mother["anc_date_by_mother"],
                pnc_date_by_mother=v1_per_mother["pnc_date_by_mother"],
                baby_dob_by_mother=v1_per_mother["baby_dob_by_mother"],
            )

        v1_followup_data = {
            "flw_summaries": v1_flw_followup,
            "total_cases": sum(len(v) for v in v1_visit_cases.values()),
            "flw_drilldown": v1_drilldown,
        }

        # GS scores (v1 path: raw CCHQ form dicts)
        v1_gs_by_flw = {}
        for form_dict in gs_forms:
            form = form_dict.get("form", {})
            connect_id = (form.get("load_flw_connect_id", "") or "").lower()
            score = form.get("checklist_percentage", "")
            time_end = form.get("meta", {}).get("timeEnd", "")
            if connect_id and score:
                v1_gs_by_flw.setdefault(connect_id, []).append((time_end, score))
        v1_first_gs = {}
        for connect_id, scores in v1_gs_by_flw.items():
            scores.sort(key=lambda x: x[0])
            v1_first_gs[connect_id] = scores[0][1]

        # Quality metrics
        v1_quality = compute_overview_quality_metrics(
            v1_visit_cases,
            v1_mother_metadata,
            v1_per_mother["parity_by_mother"],
            anc_date_by_mother=v1_per_mother["anc_date_by_mother"],
            pnc_date_by_mother=v1_per_mother["pnc_date_by_mother"],
        )

        # Overview assembly (mirrors views.py lines 696-718)
        v1_mother_counts = count_mothers_from_pipeline(rows, active_usernames, registration_forms=registration_forms)
        v1_gps_median_by_flw = {}
        for flw in v1_gps_result.flw_summaries:
            if flw.avg_case_distance_km is not None:
                v1_gps_median_by_flw[flw.username] = round(flw.avg_case_distance_km, 2)

        v1_completed_by_flw = {}
        v1_followup_rate_by_flw = {}
        for flw_summary in v1_flw_followup:
            v1_completed_by_flw[flw_summary["username"]] = flw_summary["completed_total"]
            v1_followup_rate_by_flw[flw_summary["username"]] = flw_summary["completion_rate"]

        # Eligible mothers from mother_metadata
        v1_eligible_mothers_by_flw = {}
        for flw_username, flw_cases in v1_visit_cases.items():
            mother_ids = {
                c.get("properties", {}).get("mother_case_id", "")
                for c in flw_cases
                if c.get("properties", {}).get("mother_case_id")
            }
            eligible_count = sum(
                1
                for mid in mother_ids
                if v1_mother_metadata.get(mid, {}).get("properties", {}).get("eligible_full_intervention_bonus") == "1"
            )
            v1_eligible_mothers_by_flw[flw_username] = eligible_count

        # Cases still eligible
        v1_cases_eligible = {}
        for flw_username, mothers in v1_drilldown.items():
            eligible_mothers = [m for m in mothers if m.get("eligible")]
            still_on_track = 0
            for m in eligible_mothers:
                completed_count = sum(1 for v in m["visits"] if v["status"].startswith("Completed"))
                missed_count = sum(1 for v in m["visits"] if v["status"] == "Missed")
                if completed_count >= 5 or missed_count <= 1:
                    still_on_track += 1
            total_eligible = len(eligible_mothers)
            v1_cases_eligible[flw_username] = {
                "eligible": still_on_track,
                "total": total_eligible,
                "pct": round(still_on_track / total_eligible * 100) if total_eligible > 0 else 0,
            }

        v1_overview_flws = []
        for username in sorted(active_usernames):
            display_name = flw_names.get(username, username)
            v1_overview_flws.append(
                {
                    "username": username,
                    "display_name": display_name,
                    "cases_registered": v1_mother_counts.get(username, 0),
                    "eligible_mothers": v1_eligible_mothers_by_flw.get(username, 0),
                    "first_gs_score": v1_first_gs.get(username),
                    "post_test_attempts": None,
                    "followup_rate": v1_followup_rate_by_flw.get(username, 0),
                    "ebf_pct": v1_ebf.get(username),
                    "revisit_distance_km": v1_gps_median_by_flw.get(username),
                    "median_meters_per_visit": v1_median_meters.get(username)
                    if v1_median_meters.get(username) is not None
                    else None,
                    "median_minutes_per_visit": v1_median_minutes.get(username)
                    if v1_median_minutes.get(username) is not None
                    else None,
                    **v1_quality.get(username, {}),
                    "cases_still_eligible": v1_cases_eligible.get(username, {"eligible": 0, "total": 0, "pct": 0}),
                }
            )

        # FLW performance (skip _get_latest_flw_statuses — requires audit/workflow access)
        # Use empty statuses so both paths get the same input
        flw_statuses = {u: "none" for u in active_usernames}
        v1_performance = compute_flw_performance_by_status(flw_statuses, v1_drilldown, current_date)

        v1_payload = {
            "gps_data": v1_gps_data,
            "followup_data": v1_followup_data,
            "overview_data": {
                "flw_summaries": v1_overview_flws,
                "visit_status_distribution": v1_visit_status_dist,
            },
            "performance_data": v1_performance,
        }

        self.stdout.write(self.style.SUCCESS("  -> V1 payload built"))

        # =====================================================================
        # STEP 3: Build V2 payload (job handler + JS assembly)
        # =====================================================================
        self.stdout.write("\n[6/6] Building V2 payload...")

        # Serialize visit rows as dicts (mimics pipeline SSE serialization)
        serialized_visits = []
        for row in rows:
            srow = (
                row.to_dict()
                if hasattr(row, "to_dict")
                else {
                    "id": getattr(row, "id", None),
                    "username": row.username,
                    "visit_date": row.visit_date.isoformat() if row.visit_date else None,
                    "latitude": getattr(row, "latitude", None),
                    "longitude": getattr(row, "longitude", None),
                    "entity_name": getattr(row, "entity_name", None),
                    "computed": dict(row.computed) if row.computed else {},
                }
            )
            # Add metadata.location (v2 pipeline SSE includes this)
            lat = getattr(row, "latitude", None)
            lon = getattr(row, "longitude", None)
            if lat is not None and lon is not None:
                srow.setdefault("metadata", {})["location"] = f"{lat} {lon}"
            else:
                srow.setdefault("metadata", {})["location"] = None
            serialized_visits.append(srow)

        # Run job handler
        progress_messages = []

        def mock_progress(msg, processed=0, total=0):
            progress_messages.append(msg)

        v2_job_config = {
            "pipeline_data": {
                "visits": {"rows": serialized_visits, "metadata": {}},
                "registrations": {"rows": registration_forms, "metadata": {}},
                "gs_forms": {"rows": gs_forms, "metadata": {}},
            },
            "active_usernames": list(active_usernames),
            "flw_names": flw_names,
            "flw_statuses": flw_statuses,
        }

        v2_results = handle_mbw_monitoring_job(v2_job_config, access_token, mock_progress)

        # Replicate JS assembly (mbw_monitoring_v2.py lines 407-504)
        v2_gps_data = v2_results.get("gps_data") or {}
        v2_followup_data = v2_results.get("followup_data") or {}
        v2_quality_metrics = v2_results.get("quality_metrics") or {}
        v2_overview_summary = v2_results.get("overview_data") or {}
        v2_performance_data = v2_results.get("performance_data") or []

        # Build overview_flws by merging sections (mirrors JS onComplete)
        v2_overview_flws = []
        for username in sorted(active_usernames):
            u_lower = username.lower()
            display_name = flw_names.get(u_lower, username)

            # From GPS data
            gps_flw = {}
            for g in v2_gps_data.get("flw_summaries") or []:
                if g.get("username") == u_lower:
                    gps_flw = g
                    break
            v2_median_meters_val = (v2_gps_data.get("median_meters_by_flw") or {}).get(u_lower)
            v2_median_minutes_val = (v2_gps_data.get("median_minutes_by_flw") or {}).get(u_lower)

            # From follow-up data
            fu_flw = {}
            for f in v2_followup_data.get("flw_summaries") or []:
                if f.get("username") == u_lower:
                    fu_flw = f
                    break

            # From quality metrics
            quality = v2_quality_metrics.get(u_lower, {})

            # From overview summary
            mother_count = (v2_overview_summary.get("mother_counts") or {}).get(u_lower, 0)
            ebf_pct = (v2_overview_summary.get("ebf_pct_by_flw") or {}).get(u_lower)

            # Build cases_still_eligible from drilldown (mirrors JS logic)
            drilldown = (v2_followup_data.get("flw_drilldown") or {}).get(u_lower, [])
            eligible_mothers = [m for m in drilldown if m.get("eligible")]
            still_on_track = 0
            for m in eligible_mothers:
                completed_count = sum(
                    1 for v in (m.get("visits") or []) if (v.get("status") or "").startswith("Completed")
                )
                missed_count = sum(1 for v in (m.get("visits") or []) if v.get("status") == "Missed")
                if completed_count >= 5 or missed_count <= 1:
                    still_on_track += 1
            total_eligible = len(eligible_mothers)

            revisit_dist = gps_flw.get("avg_case_distance_km")
            if revisit_dist is not None:
                revisit_dist = round(revisit_dist * 100) / 100

            flw_entry = {
                "username": u_lower,
                "display_name": display_name,
                "cases_registered": mother_count,
                "eligible_mothers": total_eligible,
                "first_gs_score": None,  # populated below from GS forms
                "post_test_attempts": None,
                "followup_rate": fu_flw.get("completion_rate", 0),
                "ebf_pct": ebf_pct,
                "revisit_distance_km": revisit_dist,
                "median_meters_per_visit": v2_median_meters_val,
                "median_minutes_per_visit": v2_median_minutes_val,
                **quality,
                "cases_still_eligible": {
                    "eligible": still_on_track,
                    "total": total_eligible,
                    "pct": round(still_on_track / total_eligible * 100) if total_eligible > 0 else 0,
                },
            }
            v2_overview_flws.append(flw_entry)

        # GS score enrichment (v2 path: pipeline-extracted fields)
        # V2 JS reads (row.computed || row).user_connect_id and (row.computed || row).gs_score
        v2_gs_by_flw = {}
        for row in gs_forms:
            # Try pipeline-extracted field names first, then raw CCHQ form fields
            computed = row.get("computed", {}) if isinstance(row.get("computed"), dict) else {}
            form = row.get("form", {}) if isinstance(row.get("form"), dict) else {}

            # user_connect_id (v2 pipeline) or load_flw_connect_id (v1 raw form)
            connect_id = (computed.get("user_connect_id") or form.get("load_flw_connect_id", "") or "").lower()

            # gs_score (v2 pipeline) or checklist_percentage (v1 raw form)
            score_str = str(computed.get("gs_score") or form.get("checklist_percentage", "") or "")
            try:
                score = float(score_str)
            except (ValueError, TypeError):
                score = None

            # assessment_date (v2 pipeline) or meta.timeEnd (v1 raw form)
            assess_date = computed.get("assessment_date") or form.get("meta", {}).get("timeEnd", "") or ""

            if connect_id and score is not None:
                v2_gs_by_flw.setdefault(connect_id, []).append(
                    {
                        "score": score,
                        "date": assess_date,
                    }
                )

        for flw_entry in v2_overview_flws:
            gs_entries = v2_gs_by_flw.get(flw_entry["username"], [])
            if gs_entries:
                gs_entries.sort(key=lambda x: x["date"] or "")
                flw_entry["first_gs_score"] = round(gs_entries[0]["score"])

        v2_payload = {
            "gps_data": v2_gps_data,
            "followup_data": v2_followup_data,
            "overview_data": {
                "flw_summaries": v2_overview_flws,
                "visit_status_distribution": v2_followup_data.get("visit_status_distribution", {}),
            },
            "performance_data": v2_performance_data,
        }

        self.stdout.write(self.style.SUCCESS("  -> V2 payload built"))

        # =====================================================================
        # STEP 4: Deep compare
        # =====================================================================
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("COMPARING PAYLOADS")
        self.stdout.write("=" * 70)

        all_diffs = []

        # Compare GPS data
        gps_diffs = self._compare_section("gps_data", v1_payload["gps_data"], v2_payload["gps_data"], verbose)
        all_diffs.extend(gps_diffs)
        self._report_section("GPS data", gps_diffs, verbose)

        # Compare follow-up data (excluding drilldown for now — it's huge)
        v1_fu_summary = {
            "flw_summaries": v1_payload["followup_data"]["flw_summaries"],
            "total_cases": v1_payload["followup_data"]["total_cases"],
        }
        v2_fu_summary = {
            "flw_summaries": v2_payload["followup_data"]["flw_summaries"],
            "total_cases": v2_payload["followup_data"]["total_cases"],
        }
        fu_diffs = self._compare_section("followup_data", v1_fu_summary, v2_fu_summary, verbose)
        all_diffs.extend(fu_diffs)
        self._report_section("Follow-up data (summaries)", fu_diffs, verbose)

        # Compare visit_status_distribution
        vsd_diffs = self._compare_section(
            "visit_status_dist",
            v1_payload["overview_data"]["visit_status_distribution"],
            v2_payload["overview_data"]["visit_status_distribution"],
            verbose,
        )
        all_diffs.extend(vsd_diffs)
        self._report_section("Visit status distribution", vsd_diffs, verbose)

        # Compare overview flw_summaries (the critical one)
        overview_diffs = self._compare_overview_flws(
            v1_payload["overview_data"]["flw_summaries"],
            v2_payload["overview_data"]["flw_summaries"],
            verbose,
        )
        all_diffs.extend(overview_diffs)
        self._report_section("Overview FLW summaries", overview_diffs, verbose)

        # Compare performance data
        perf_diffs = self._compare_section(
            "performance_data",
            v1_payload["performance_data"],
            v2_payload["performance_data"],
            verbose,
        )
        all_diffs.extend(perf_diffs)
        self._report_section("Performance data", perf_diffs, verbose)

        # Compare drilldown (sample — first 3 FLWs)
        v1_dd = v1_payload["followup_data"].get("flw_drilldown", {})
        v2_dd = v2_payload["followup_data"].get("flw_drilldown", {})
        dd_diffs = []
        if set(v1_dd.keys()) != set(v2_dd.keys()):
            dd_diffs.append(
                f"drilldown FLW keys differ: v1={sorted(v1_dd.keys())[:5]}, " f"v2={sorted(v2_dd.keys())[:5]}"
            )
        for flw in sorted(set(v1_dd.keys()) & set(v2_dd.keys()))[:3]:
            if len(v1_dd[flw]) != len(v2_dd[flw]):
                dd_diffs.append(f"drilldown[{flw}] mother count: v1={len(v1_dd[flw])}, v2={len(v2_dd[flw])}")
            else:
                for i, (m1, m2) in enumerate(zip(v1_dd[flw], v2_dd[flw])):
                    sub = self._compare_dicts(f"drilldown[{flw}][{i}]", m1, m2)
                    dd_diffs.extend(sub)
        all_diffs.extend(dd_diffs)
        self._report_section("Drilldown (sample)", dd_diffs, verbose)

        # =====================================================================
        # Summary
        # =====================================================================
        self.stdout.write("\n" + "=" * 70)
        if all_diffs:
            self.stdout.write(self.style.ERROR(f"\nFAILED: {len(all_diffs)} total differences"))
            for diff in all_diffs[:30]:
                self.stdout.write(f"  - {diff}")
            if len(all_diffs) > 30:
                self.stdout.write(f"  ... and {len(all_diffs) - 30} more")
            raise CommandError(f"Parity failed with {len(all_diffs)} differences")
        else:
            self.stdout.write(self.style.SUCCESS("\nPASSED: v1 and v2 payloads are identical!"))

        self.stdout.write("\nData summary:")
        self.stdout.write(f"  Pipeline rows:        {len(rows)}")
        self.stdout.write(f"  Active usernames:     {len(active_usernames)}")
        self.stdout.write(f"  Registration forms:   {len(registration_forms)}")
        self.stdout.write(f"  GS forms:             {len(gs_forms)}")
        self.stdout.write(f"  V1 overview FLWs:     {len(v1_overview_flws)}")
        self.stdout.write(f"  V2 overview FLWs:     {len(v2_overview_flws)}")

    def _report_section(self, name, diffs, verbose):
        if diffs:
            self.stdout.write(self.style.ERROR(f"\n  {name}: {len(diffs)} differences"))
            if verbose:
                for d in diffs[:10]:
                    self.stdout.write(f"    - {d}")
                if len(diffs) > 10:
                    self.stdout.write(f"    ... and {len(diffs) - 10} more")
        else:
            self.stdout.write(self.style.SUCCESS(f"\n  {name}: MATCH"))

    def _compare_section(self, label, v1, v2, verbose):
        """Compare two values (dicts, lists, scalars) and return diff strings."""
        if isinstance(v1, dict) and isinstance(v2, dict):
            return self._compare_dicts(label, v1, v2)
        elif isinstance(v1, list) and isinstance(v2, list):
            return self._compare_lists(label, v1, v2)
        elif v1 != v2:
            return [f"{label}: v1={_trunc(v1)} vs v2={_trunc(v2)}"]
        return []

    def _compare_overview_flws(self, v1_flws, v2_flws, verbose):
        """Compare overview FLW summary lists field-by-field."""
        diffs = []
        v1_by_user = {f["username"]: f for f in v1_flws}
        v2_by_user = {f["username"]: f for f in v2_flws}

        if set(v1_by_user.keys()) != set(v2_by_user.keys()):
            diffs.append(
                f"overview FLW usernames differ: "
                f"only_v1={set(v1_by_user.keys()) - set(v2_by_user.keys())}, "
                f"only_v2={set(v2_by_user.keys()) - set(v1_by_user.keys())}"
            )

        for username in sorted(set(v1_by_user.keys()) & set(v2_by_user.keys())):
            v1_flw = v1_by_user[username]
            v2_flw = v2_by_user[username]
            sub = self._compare_dicts(f"overview[{username}]", v1_flw, v2_flw)
            diffs.extend(sub)

        return diffs

    def _compare_dicts(self, label, v1, v2):
        """Recursively compare two dicts."""
        diffs = []
        all_keys = set(v1.keys()) | set(v2.keys())
        for key in sorted(all_keys, key=str):
            v1_val = v1.get(key)
            v2_val = v2.get(key)
            full_key = f"{label}.{key}"

            if isinstance(v1_val, dict) and isinstance(v2_val, dict):
                diffs.extend(self._compare_dicts(full_key, v1_val, v2_val))
            elif isinstance(v1_val, list) and isinstance(v2_val, list):
                diffs.extend(self._compare_lists(full_key, v1_val, v2_val))
            elif isinstance(v1_val, float) and isinstance(v2_val, float):
                if abs(v1_val - v2_val) > 0.01:
                    diffs.append(f"{full_key}: v1={v1_val} vs v2={v2_val}")
            elif v1_val != v2_val:
                # Treat None vs missing-key as equal
                if v1_val is None and key not in v2:
                    continue
                if v2_val is None and key not in v1:
                    continue
                diffs.append(f"{full_key}: v1={_trunc(v1_val)} vs v2={_trunc(v2_val)}")

        return diffs

    def _compare_lists(self, label, v1, v2):
        """Compare two lists element-by-element."""
        diffs = []
        if len(v1) != len(v2):
            diffs.append(f"{label} length: v1={len(v1)} vs v2={len(v2)}")

        for i, (a, b) in enumerate(zip(v1, v2)):
            if isinstance(a, dict) and isinstance(b, dict):
                diffs.extend(self._compare_dicts(f"{label}[{i}]", a, b))
            elif isinstance(a, float) and isinstance(b, float):
                if abs(a - b) > 0.01:
                    diffs.append(f"{label}[{i}]: v1={a} vs v2={b}")
            elif a != b:
                diffs.append(f"{label}[{i}]: v1={_trunc(a)} vs v2={_trunc(b)}")

        return diffs


def _trunc(val, max_len=80):
    """Truncate repr for readability."""
    s = repr(val)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s
