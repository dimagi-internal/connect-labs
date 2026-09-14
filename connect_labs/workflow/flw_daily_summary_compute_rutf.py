"""Pure computation functions for the FLW Daily Summary Report — RUTF variant
(Program 263, "RUTF - NG - Program 1 - Sept 26").

Sibling of flw_daily_summary_compute.py (Program 217's CHC version), same
"plain counts only, no thresholds" philosophy, but for a different app with a
different case/form model — RUTF has no households-vs-children split, no
MUAC/deworming eligibility-by-age split, and no work-area/ward concept, so
this is a much smaller function than the CHC one it's modeled after.

Every function here takes plain dicts/lists and returns plain dicts — no
Django, no network, no pipeline objects — so the logic can be unit-tested
directly. See connect_labs/workflow/templates/flw_daily_summary_report_rutf.py
for the template that fetches pipeline rows and calls
compute_flw_daily_summary_rutf per opportunity per FLW per day.

Field expectations on each `approved_rows` row (from the RUTF `approved_visits`
pipeline, filters={"status": ["approved"]} — approved only, both forms below):
    username, opportunity_id, form_display_name, entity_id

Field/form mapping (verified 2026-09-14 against real submitted data on the
RUTF Internal Test opportunity, id 2092, via an existing pipeline built for
that opp — "RUTF FLW Service Delivery Indicators", pipeline id 19854 — NOT
guessed from the blank app schema):
    - "Register a New Family" is the registration form; a distinct
      ``entity_id`` on an approved submission of this form is one child
      registered.
    - "Screening " (note trailing space — the app's own form name) is the SAM
      screening form; ``form.screening_outcome.rutf_enrollment`` is "yes" when
      the child is diagnosed SAM and enrolled into the RUTF/OTP program (a
      "no" outcome opens no case at all, so ``entity_id`` is null/absent on
      those rows and they don't count).
    - "Visit Form" is the RUTF follow-up visit form for an already-enrolled
      child (contains a ``visit_2_or_greater`` question group that only fires
      on repeat visits) — every approved submission of this form is one SAM
      follow-up visit, not deduped by child (mirrors Program 217's
      ``total_health_service_delivery_visits``, which also counts every
      submission rather than distinct children).
"""

from __future__ import annotations

REGISTRATION_FORM_NAME = "Register a New Family"
SCREENING_FORM_NAME = "Screening "
FOLLOWUP_FORM_NAME = "Visit Form"


def compute_flw_daily_summary_rutf(approved_rows: list[dict]) -> dict:
    """Compute every RUTF (Program 263) daily summary indicator for ONE FLW's
    approved visits on ONE day, for ONE opportunity.

    ``approved_rows`` must already be filtered to this FLW, this opportunity,
    and this day's window (already approved-only — the RUTF approved_visits
    pipeline filters status server-side, same convention as Program 217's).
    """
    registration_rows = [r for r in approved_rows if r.get("form_display_name") == REGISTRATION_FORM_NAME]
    screening_rows = [r for r in approved_rows if r.get("form_display_name") == SCREENING_FORM_NAME]
    followup_rows = [r for r in approved_rows if r.get("form_display_name") == FOLLOWUP_FORM_NAME]

    total_children_registered = len({r["entity_id"] for r in registration_rows if r.get("entity_id")})
    total_sam_children_registered = len(
        {r["entity_id"] for r in screening_rows if r.get("entity_id") and r.get("rutf_enrollment") == "yes"}
    )
    total_sam_followup_visits = len(followup_rows)  # not deduped -- every submission counts

    return {
        "total_children_registered": total_children_registered,
        "total_sam_children_registered": total_sam_children_registered,
        "total_sam_followup_visits": total_sam_followup_visits,
    }
