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
ANY status, single fetch):
    username, opportunity_id, form_display_name, status, entity_id,
    rutf_enrollment, muac_photo

Field/form mapping (verified against real submitted data and the RUTF app's
own question list, NOT guessed):
    - "Screening " (note trailing space — the app's own form name, module:
      Initial Screening) registers and screens ONE child per submission --
      the real per-child signal, tracked as ``total_children_screened``.
      ``form.screening_outcome.rutf_enrollment`` is "yes" when the child is
      diagnosed SAM and enrolled into the RUTF/OTP program (a "no" outcome
      opens no case at all, so ``entity_id`` is null/absent on those rows).
    - "Visit Form" is the RUTF follow-up visit form for an already-enrolled
      child (contains a ``visit_2_or_greater`` question group that only fires
      on repeat visits). Every submission counts as one visit, not deduped by
      child.
    - ``total_visits`` sums BOTH "Screening " and "Visit Form" submissions,
      any status -- every service-delivery contact with a child that day,
      initial or follow-up. Program 217's ``total_health_service_delivery_
      visits`` equivalent counts every submission of its single combined
      visit form regardless of status; RUTF splits that same kind of contact
      across two separate forms (an initial Screening, then Visit Form for
      every visit after), so a faithful equivalent has to add both together.
      **Originally (through 2026-09-17) this only counted "Visit Form" and
      read as 0 on any day an FLW had done nothing but screenings -- e.g. 6
      real Screening submissions but 0 Visit Form ones, making a fully
      active day look like zero activity.** Fixed by counting both forms.
      ``total_sam_followup_visits`` (approved-only "Visit Form" submissions)
      is unchanged -- specifically about follow-up cadence for already-
      enrolled children, not overall activity.
    - ``muac_photo`` is read only at Screening (form.anthropometric_appetite.
      muac_measurement.muac_photo) -- non-empty means a photo was captured at
      the child's initial visit. The follow-up Visit Form captures MUAC again
      at a different nested path, not read here (scoped to Screening-time
      MUAC only, i.e. a registration-time data-quality signal, the closest
      analog to Program 217's muac_measured).

**"Register a New Family" (household registration) was dropped entirely on
2026-09-17.** It used to feed ``total_households_registered`` (distinct
households) and ``total_children_registered`` (summed
``under_five_children_count``), but as of 2026-09-16 that form had never once
produced a Connect visit for the live opportunity (2230) despite confirmed
real CommCare HQ submissions -- a Connect-side gap outside Labs/this
pipeline, not something this computation can fix. Since the data can only be
pulled through Connect (never CCHQ, per explicit product decision) and
Connect isn't receiving it, these two indicators were permanently
silently-zero and were removed rather than kept as dead rows. If Connect
starts creating visits for this form in the future, re-add them the same way
this file computed them before (distinct ``entity_id`` for households,
summed ``form.household_form.under_five_children_count`` for children).

SAM % and MUAC % are both measured against ``total_children_screened``
(children actually assessed at Screening).
"""

from __future__ import annotations

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

    screening_rows = [r for r in approved_rows if r.get("form_display_name") == SCREENING_FORM_NAME]
    all_screening_rows = [r for r in rows if r.get("form_display_name") == SCREENING_FORM_NAME]
    approved_followup_rows = [r for r in approved_rows if r.get("form_display_name") == FOLLOWUP_FORM_NAME]
    all_followup_rows = [r for r in rows if r.get("form_display_name") == FOLLOWUP_FORM_NAME]

    total_children_screened = len(screening_rows)  # every screening submission counts -- not deduped
    total_sam_children_registered = len(
        {r["entity_id"] for r in screening_rows if r.get("entity_id") and r.get("rutf_enrollment") == "yes"}
    )
    total_children_muac_measured = len([r for r in screening_rows if r.get("muac_photo")])
    # Any status, incl. unapproved -- every contact with a child this day, initial (Screening) or
    # follow-up (Visit Form). See the module docstring for why both forms must be summed here.
    total_visits = len(all_screening_rows) + len(all_followup_rows)
    total_sam_followup_visits = len(approved_followup_rows)  # approved-only Visit Form -- not deduped

    return {
        "total_children_screened": total_children_screened,
        "total_sam_children_registered": total_sam_children_registered,
        "total_children_muac_measured": total_children_muac_measured,
        "total_visits": total_visits,
        "total_sam_followup_visits": total_sam_followup_visits,
    }
