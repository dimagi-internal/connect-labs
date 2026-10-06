"""IPTsc School Delivery dashboard -- monitoring and evaluation for school-based malaria chemoprevention.

IPTsc gives SPAQ to school children as THREE doses on THREE consecutive days
(Day 1 SP+AQ, Days 2 and 3 AQ), each one directly observed. The questions this
page answers are the ones a programme lead asks of a short, strictly scheduled
course: did every registered child get dose 1, 2 AND 3, on consecutive days,
actually swallowed; was anyone harmed; and is the data real.

Three forms (deliver app "IPTsc School Delivery v4 - SCDI"):

* ``Day 1 - Registration & Dose 1`` -- creates the ``child`` case, Connect
  deliver unit ``iptsc_dose_1``.
* ``Follow-up Dose`` -- doses 2 and 3 (and recorded-but-unpaid extras), deliver
  units ``iptsc_dose_1/2/3``.
* ``Adverse Event Log`` -- NO deliver unit, so it never reaches the Connect
  visit export. It is read from CommCare HQ (``ae_log``), which is why the page
  needs a CommCare HQ sign-in as well as Connect.

The child is the unit. ``form.child_id`` carries the child case id on BOTH
dosing forms (the new case on Day 1, the session case on follow-ups), so it is
the linking key. Do NOT key on the Connect ``entity_id``: the follow-up form
builds its visit identifier from ``entity_name_1`` ("... - Dose 1") for every
dose, so it never names doses 2 and 3.

Children who were registered but never came back exist only as CommCare cases,
so ``child_cases`` (the HQ ``child`` case list) is what makes a missed dose
visible at all -- a visit export only shows what happened, never what didn't.

All indicator logic lives in the render as pure, unit-tested functions
(``__tests__/iptsc_render.test.mjs``). The pipelines only extract. At the
opportunity's volume (tens to low thousands of visits) one visit-level stream
is cheap; if it grows past ~50k visits, switch ``doses`` to ``load: on_demand``
and fetch the child timeline per child.

Design: docs/plans/2026-10-06-iptsc-dashboard-design.md
"""

from pathlib import Path

RENDER_CODE = (Path(__file__).parent / "iptsc_dashboard_render.js").read_text(encoding="utf-8")

DEFINITION = {
    "name": "IPTsc School Delivery Dashboard",
    "description": (
        "Monitoring and evaluation for IPTsc school dosing: whether each child received "
        "dose 1, 2 and 3 on consecutive days, who is missing a dose, reach by school, "
        "safety and adverse events, protocol compliance and data authenticity checks per worker."
    ),
    "version": 1,
    "templateType": "iptsc_dashboard",
    "statuses": [
        {"id": "active", "label": "Active", "color": "green"},
    ],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
        "templateType": "iptsc_dashboard",
        # The Adverse Event Log and the child case list come from CommCare HQ.
        "auth_requires": ["connect", "commcare_hq"],
        # Lagos, UTC+1, no daylight saving: "same day / next day" is a WAT
        # calendar day, the school day the dose was given on.
        "utc_offset_hours": 1,
        # A rate whose denominator is below these reads "too few to say".
        "min_children": 10,
        "min_doses": 20,
    },
    "pipeline_sources": [],
}

# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------

# One row per dosing form (Day 1 or Follow-up). Day 1 and Follow-up keep the same
# question under different group names, so each shared measure lists both paths
# (Day 1 first) and the engine takes the first that is present.
DOSES_SCHEMA = {
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "linking_field": "child_id",
    "fields": [
        # -- identity and timing
        {"name": "child_id", "paths": ["form.child_id", "form.case.@case_id"], "aggregation": "first"},
        {"name": "form_name", "path": "form.@name", "aggregation": "first"},
        {"name": "time_start", "path": "form.meta.timeStart", "aggregation": "first"},
        {"name": "time_end", "path": "form.meta.timeEnd", "aggregation": "first"},
        {
            "name": "gps_raw",
            "paths": ["form.identity.gps", "form.confirm.gps", "form.meta.location.#text", "form.meta.location"],
            "aggregation": "first",
        },
        {
            "name": "latitude",
            "paths": ["form.identity.gps", "form.confirm.gps", "form.meta.location.#text", "form.meta.location"],
            "aggregation": "first",
            "transform": "gps_lat",
        },
        {
            "name": "longitude",
            "paths": ["form.identity.gps", "form.confirm.gps", "form.meta.location.#text", "form.meta.location"],
            "aggregation": "first",
            "transform": "gps_lon",
        },
        # -- Day 1: eligibility and schedule
        {"name": "registration_allowed", "path": "form.registration_allowed", "aggregation": "first"},
        {"name": "weekday_name", "path": "form.weekday_name", "aggregation": "first"},
        {"name": "holiday_intervening", "path": "form.elig.holiday_intervening", "aggregation": "first"},
        {"name": "supervisor_override", "path": "form.elig.supervisor_override", "aggregation": "first"},
        {"name": "supervisor_audio", "path": "form.elig.supervisor_audio", "aggregation": "first"},
        {"name": "availability_next2", "path": "form.elig.availability_next2", "aggregation": "first"},
        {"name": "eligible_to_proceed", "path": "form.eligible_to_proceed", "aggregation": "first"},
        # -- Day 1: consent and demographics
        {"name": "consent_3day", "path": "form.consent.consent_3day", "aggregation": "first"},
        {"name": "consent_photo", "path": "form.consent.consent_photo", "aggregation": "first"},
        {"name": "child_name", "path": "form.identity.case_name", "aggregation": "first"},
        {"name": "dob_age_recorded", "path": "form.demo.dob_age_recorded", "aggregation": "first"},
        {"name": "dob", "path": "form.demo.dob", "aggregation": "first"},
        {"name": "age_years", "path": "form.demo.age_years", "aggregation": "first"},
        {"name": "age_over10_confirm", "path": "form.demo.age_over10_confirm", "aggregation": "first"},
        {"name": "sex", "path": "form.demo.sex", "aggregation": "first"},
        {"name": "school", "path": "form.demo.school", "aggregation": "first"},
        {"name": "ward_village", "path": "form.demo.ward_village", "aggregation": "first"},
        {"name": "district", "path": "form.demo.district", "aggregation": "first"},
        {"name": "state", "path": "form.demo.state", "aggregation": "first"},
        {"name": "caregiver_phone", "path": "form.demo.caregiver_phone", "aggregation": "first"},
        {"name": "weight_kg", "path": "form.demo.child_weight_kg", "aggregation": "first"},
        {"name": "dose_band", "path": "form.demo.dose_band", "aggregation": "first"},
        # -- Day 1: safety screen
        {"name": "acute_illness", "path": "form.safety.acute_illness", "aggregation": "first"},
        {"name": "allergy", "path": "form.safety.allergy", "aggregation": "first"},
        {"name": "recent_antimalarial", "path": "form.safety.recent_antimalarial", "aggregation": "first"},
        {"name": "cotrimoxazole", "path": "form.safety.cotrimoxazole", "aggregation": "first"},
        {"name": "pregnancy_screen", "path": "form.safety.pregnancy_screen", "aggregation": "first"},
        {"name": "dose_eligible", "path": "form.safety.dose_eligible", "aggregation": "first"},
        {"name": "band1020_review_ok", "path": "form.dosing.band1020_review_ok", "aggregation": "first"},
        {"name": "over40_supervisor_ok", "path": "form.dosing.over40_supervisor_ok", "aggregation": "first"},
        # -- Follow-up: schedule state at the time of the visit
        {"name": "expected_dose", "path": "form.expected_dose", "aggregation": "first"},
        {"name": "prior_doses", "path": "form.prior_doses", "aggregation": "first"},
        {"name": "due_status", "path": "form.due_status", "aggregation": "first"},
        {"name": "can_dose", "path": "form.can_dose", "aggregation": "first"},
        {"name": "max_reached", "path": "form.max_reached", "aggregation": "first"},
        {"name": "missed_supervisor_ok", "path": "form.missed.missed_supervisor_ok", "aggregation": "first"},
        # -- Follow-up: pre-dose check
        {"name": "issue_since_last", "path": "form.predose.issue_since_last", "aggregation": "first"},
        {"name": "issue_severity", "path": "form.predose.issue_severity", "aggregation": "first"},
        {"name": "issue_symptoms", "path": "form.predose.issue_symptoms", "aggregation": "first"},
        {"name": "well_enough", "path": "form.predose.well_enough", "aggregation": "first"},
        {"name": "severe_supervisor_ok", "path": "form.predose.severe_supervisor_ok", "aggregation": "first"},
        # -- Both: identity evidence
        {
            "name": "child_said_name",
            "paths": ["form.identity.child_said_name", "form.confirm.child_said_name"],
            "aggregation": "first",
        },
        {
            "name": "flw_name_audio",
            "paths": ["form.identity.flw_name_audio", "form.confirm.flw_name_audio"],
            "aggregation": "first",
        },
        {
            "name": "child_name_audio",
            "paths": ["form.identity.child_name_audio", "form.confirm.child_name_audio"],
            "aggregation": "first",
        },
        # -- Both: the dose itself
        {"name": "dose_prepared", "path": "form.dosing.dose_prepared_confirm", "aggregation": "first"},
        {"name": "dose_photo", "path": "form.dosing.dose_photo", "aggregation": "first"},
        {"name": "dot", "paths": ["form.dosing.dose1_dot", "form.dosing.dose_dot"], "aggregation": "first"},
        {
            "name": "not_given_reason",
            "paths": ["form.dosing.dose1_not_given_reason", "form.dosing.dose_not_given_reason"],
            "aggregation": "first",
        },
        {"name": "swallowed", "path": "form.dosing.swallowed", "aggregation": "first"},
        {"name": "redose_given", "path": "form.dosing.redose_given", "aggregation": "first"},
        {"name": "redose_swallowed", "path": "form.dosing.redose_swallowed", "aggregation": "first"},
        # -- Both: 30-minute observation
        {"name": "obs_result", "path": "form.observation.obs_result", "aggregation": "first"},
        {"name": "obs_responsible", "path": "form.observation.obs_responsible", "aggregation": "first"},
        {"name": "obs_symptoms", "path": "form.observation.obs_symptoms", "aggregation": "first"},
        {"name": "obs_action", "path": "form.observation.obs_action", "aggregation": "first"},
        # -- Both: close-out (the case state the form wrote)
        {
            "name": "dose_completed",
            "paths": ["form.closeout.dose1_completed", "form.closeout.dose_completed"],
            "aggregation": "first",
        },
        {
            "name": "doses_after",
            "paths": ["form.closeout.updated_doses", "form.closeout.doses_given_count"],
            "aggregation": "first",
        },
        {"name": "course_status", "path": "form.closeout.course_status", "aggregation": "first"},
        {"name": "next_dose_due", "path": "form.closeout.next_dose_due_date", "aggregation": "first"},
        {"name": "return_confirm", "path": "form.closeout.return_confirm", "aggregation": "first"},
        {"name": "revisit_possible", "path": "form.closeout.revisit_possible", "aggregation": "first"},
    ],
    "window_fields": [
        {
            # How far this dose was given from the same child's previous dose:
            # doses 2 and 3 should be at the school where dose 1 was.
            "name": "dist_prev_dose_m",
            "operation": "lag_haversine",
            "partition_by": "child_id",
            "order_by": "time_end",
            "lat_field": "latitude",
            "lon_field": "longitude",
        },
    ],
}

# Every child as CommCare HQ holds it now -- including the ones no follow-up form
# ever reached, which is the only way the page can see a missed dose.
CHILD_CASES_SCHEMA = {
    "data_source": {"type": "cchq_cases", "case_type": "child"},
    "grouping_key": "entity_id",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "case_id", "path": "case.case_id", "aggregation": "first"},
        {"name": "owner_id", "path": "case.owner_id", "aggregation": "first"},
        {"name": "closed", "path": "case.closed", "aggregation": "first"},
        {"name": "date_opened", "path": "case.date_opened", "aggregation": "first"},
        {"name": "registration_date", "path": "case.properties.registration_date", "aggregation": "first"},
        {"name": "doses_given_count", "path": "case.properties.doses_given_count", "aggregation": "first"},
        {"name": "last_dose_number", "path": "case.properties.last_dose_number", "aggregation": "first"},
        {"name": "last_dose_date", "path": "case.properties.last_dose_date", "aggregation": "first"},
        {"name": "last_dose_status", "path": "case.properties.last_dose_status", "aggregation": "first"},
        {"name": "next_dose_due_date", "path": "case.properties.next_dose_due_date", "aggregation": "first"},
        {"name": "course_status", "path": "case.properties.course_status", "aggregation": "first"},
        {"name": "ae_last_severity", "path": "case.properties.ae_last_severity", "aggregation": "first"},
        {"name": "consent_3day", "path": "case.properties.consent_3day", "aggregation": "first"},
        {"name": "school", "path": "case.properties.school", "aggregation": "first"},
        {"name": "sex", "path": "case.properties.sex", "aggregation": "first"},
        {"name": "age_years", "path": "case.properties.age_years", "aggregation": "first"},
        {"name": "dose_band", "path": "case.properties.dose_band", "aggregation": "first"},
    ],
}

# The Adverse Event Log has no deliver unit, so only CommCare HQ has it.
AE_LOG_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "Adverse Event Log",
        "app_id_source": "opportunity",
    },
    "grouping_key": "case_id",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "child_id", "path": "form.case.@case_id", "aggregation": "first"},
        {"name": "event_datetime", "path": "form.ae.event_datetime", "aggregation": "first"},
        {"name": "which_dose", "path": "form.ae.which_dose", "aggregation": "first"},
        {"name": "time_since_dose", "path": "form.ae.time_since_dose", "aggregation": "first"},
        {"name": "reporter", "path": "form.ae.reporter", "aggregation": "first"},
        {"name": "symptoms", "path": "form.ae.symptoms", "aggregation": "first"},
        {"name": "severity", "path": "form.ae.severity", "aggregation": "first"},
        {"name": "current_status", "path": "form.ae.current_status", "aggregation": "first"},
        {"name": "action", "path": "form.ae.action", "aggregation": "first"},
        {"name": "followup_required", "path": "form.ae.followup_required", "aggregation": "first"},
        {"name": "course_action", "path": "form.ae.course_action", "aggregation": "first"},
        {"name": "submitted_by", "path": "form.meta.username", "aggregation": "first"},
        {"name": "time_end", "path": "form.meta.timeEnd", "aggregation": "first"},
    ],
}

PIPELINE_SCHEMAS = [
    {
        "alias": "doses",
        "name": "IPTsc dosing forms",
        "description": "One row per Day 1 or Follow-up dosing form (Connect visits).",
        "schema": DOSES_SCHEMA,
    },
    {
        "alias": "child_cases",
        "name": "IPTsc child cases",
        "description": "Current state of every child case in CommCare HQ, including children with no follow-up.",
        "schema": CHILD_CASES_SCHEMA,
    },
    {
        "alias": "ae_log",
        "name": "IPTsc adverse event log",
        "description": "Adverse Event Log forms from CommCare HQ (not a Connect deliver unit).",
        "schema": AE_LOG_SCHEMA,
    },
]

TEMPLATE = {
    "key": "iptsc_dashboard",
    "name": "IPTsc School Delivery Dashboard",
    "description": (
        "School malaria chemoprevention (IPTsc): dose 1, 2 and 3 completion per child, who is "
        "missing a dose, reach by school, safety and adverse events, protocol compliance and "
        "data authenticity checks per field worker."
    ),
    "icon": "fa-school",
    "color": "green",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
