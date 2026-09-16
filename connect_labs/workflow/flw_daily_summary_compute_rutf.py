"""Pure computation functions for the FLW Daily Summary Report — RUTF variant
(Program 263, "RUTF - NG - Program 1 - Sept 26").

Sibling of flw_daily_summary_compute.py (Program 217's CHC version), same
"plain counts only, no thresholds" philosophy, but for a different app with a
different case/form model.

Every function here takes plain dicts/lists and returns plain dicts — no
Django, no network, no pipeline objects — so the logic can be unit-tested
directly. See connect_labs/workflow/templates/flw_daily_summary_report_rutf.py
for the template that fetches pipeline rows and calls
compute_flw_daily_summary_rutf per opportunity per FLW per day.

Field expectations on each row (from the RUTF `visits` pipeline, filters={} —
ANY status, all three forms below, single fetch):
    username, opportunity_id, form_display_name, status, entity_id,
    rutf_enrollment, muac_photo, household_children_count

Field/form mapping (verified against real submitted data and the RUTF app's
own question list, NOT guessed):
    - "Register a New Family" (module: Household Management) registers a
      HOUSEHOLD (case type "household") and, via an internal repeat group
      ("service_delivery/add_child_service_delivery/child_registration_ql",
      one iteration per child), registers several children in the SAME
      submission. A distinct ``entity_id`` on this form is one HOUSEHOLD, not
      one child -- the pipeline row model (one row per form submission) has
      no visibility into the repeat group's iteration count. Instead, the
      household form's own ``under_five_children_count`` question ("How many
      children in this family are between 6 months and 5 years of age?",
      saved to the household case) is a plain number answering exactly this,
      so ``total_children_registered`` sums THAT across the day's household
      registrations rather than trying to reconstruct the repeat group.
      **As of 2026-09-16, this form has never once appeared in Connect's own
      visit data for the live opportunity (2230), despite confirmed real
      CommCare HQ submissions** -- Connect isn't creating a "visit" for it at
      all, for reasons outside Labs/this pipeline (see the "not just caching"
      note in flw_daily_summary_report_rutf.py). Both fields below will read
      0 until that's resolved; the logic is correct, the input just isn't
      reaching Connect yet.
    - "Screening " (note trailing space — the app's own form name, module:
      Initial Screening) registers and screens ONE child per submission --
      the real per-child signal, tracked separately here as
      ``total_children_screened`` (a screened child is not necessarily a
      "registered" one in the household-form sense above; the two counts are
      deliberately independent, not a subset/superset of each other).
      ``form.screening_outcome.rutf_enrollment`` is "yes" when the child is
      diagnosed SAM and enrolled into the RUTF/OTP program (a "no" outcome
      opens no case at all, so ``entity_id`` is null/absent on those rows).
    - "Visit Form" is the RUTF follow-up visit form for an already-enrolled
      child (contains a ``visit_2_or_greater`` question group that only fires
      on repeat visits). Every submission counts as one visit, not deduped by
      child (mirrors Program 217's ``total_health_service_delivery_visits``,
      which also counts every submission rather than distinct children) --
      split into an any-status total (``total_visits``, Program 217's
      ``total_health_service_delivery_visits`` equivalent) and an
      approved-only count (``total_sam_followup_visits``, Program 217's
      ``total_approved_health_service_delivery_visits`` equivalent).
    - ``muac_photo`` is read only at Screening (form.anthropometric_appetite.
      muac_measurement.muac_photo) -- non-empty means a photo was captured at
      the child's initial visit. The follow-up Visit Form captures MUAC again
      at a different nested path, not read here (scoped to Screening-time
      MUAC only, i.e. a registration-time data-quality signal, the closest
      analog to Program 217's muac_measured).

SAM % and MUAC % are both measured against ``total_children_screened``
(children actually assessed), not ``total_children_registered`` (children
counted on a household form) -- SAM status and MUAC are determined AT
Screening, on the screened population, a different population than whoever a
household form declared as under-5 that day.
"""

from __future__ import annotations

REGISTRATION_FORM_NAME = "Register a New Family"
SCREENING_FORM_NAME = "Screening "
FOLLOWUP_FORM_NAME = "Visit Form"
APPROVED_STATUS = "approved"


def compute_flw_daily_summary_rutf(rows: list[dict]) -> dict:
    """Compute every RUTF (Program 263) daily summary indicator for ONE FLW's
    visits on ONE day, for ONE opportunity.

    ``rows`` must already be filtered to this FLW, this opportunity, and this
    day's window -- ANY status (the RUTF `visits` pipeline applies no status
    filter server-side; approved-only subsets are derived here, same
    convention as Program 217's hsd_visits/approved_visits split, but from one
    fetch instead of two).
    """
    approved_rows = [r for r in rows if r.get("status") == APPROVED_STATUS]

    registration_rows = [r for r in approved_rows if r.get("form_display_name") == REGISTRATION_FORM_NAME]
    screening_rows = [r for r in approved_rows if r.get("form_display_name") == SCREENING_FORM_NAME]
    approved_followup_rows = [r for r in approved_rows if r.get("form_display_name") == FOLLOWUP_FORM_NAME]
    all_followup_rows = [r for r in rows if r.get("form_display_name") == FOLLOWUP_FORM_NAME]

    total_households_registered = len({r["entity_id"] for r in registration_rows if r.get("entity_id")})
    total_children_registered = 0
    for r in registration_rows:
        try:
            total_children_registered += int(float(r.get("household_children_count") or 0))
        except (ValueError, TypeError):
            continue
    total_children_screened = len(screening_rows)  # every screening submission counts -- not deduped
    total_sam_children_registered = len(
        {r["entity_id"] for r in screening_rows if r.get("entity_id") and r.get("rutf_enrollment") == "yes"}
    )
    total_children_muac_measured = len([r for r in screening_rows if r.get("muac_photo")])
    total_visits = len(all_followup_rows)  # any status, incl. unapproved -- Total HSD Visits equivalent
    total_sam_followup_visits = len(approved_followup_rows)  # approved-only -- not deduped

    return {
        "total_households_registered": total_households_registered,
        "total_children_registered": total_children_registered,
        "total_children_screened": total_children_screened,
        "total_sam_children_registered": total_sam_children_registered,
        "total_children_muac_measured": total_children_muac_measured,
        "total_visits": total_visits,
        "total_sam_followup_visits": total_sam_followup_visits,
    }
