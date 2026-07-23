"""Seed the create-survey-solicitation review-stage fixture into the LOCAL labs DB.

The deployed-labs seeder (``ensure_demo.py``) writes to labs prod via the
``connect_labs`` MCP. For a LOCAL render (against ``runserver`` with un-merged
affordance code) the records must live in the local ``LabsLocalRecord`` table
instead — labs-only records (program id >= 10_000) are served by
``labs.synthetic.local_records_backend``, not over HTTP.

This script:
  1. Registers program 10008 as a labs-only ``SyntheticOpportunity`` locally so
     ``LabsRecordAPIClient`` routes its reads/writes to the local backend.
  2. Sweeps any prior seeded R6 solicitations (source_group_id == 4492).
  3. Creates the matured review-stage call — criteria LOCKED before publish — with
     three firm responses (two pre-scored competitors + Sahel), and stamps Sahel's
     response with per-criterion ``ai_proposed_scores`` so the reviewer's
     anti-anchoring reveal has something to reveal on camera.
  4. Writes demo-vars.json for the recorder's ${review_solicitation_id} /
     ${sahel_response_id} late-binding.

Run (from the repo root, settings default to config.settings.local)::

    python scripts/walkthroughs/create-survey-solicitation/seed_local_db.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

from connect_labs.labs.synthetic.models import SyntheticOpportunity  # noqa: E402
from connect_labs.solicitations.data_access import (  # noqa: E402
    RESPONSE_TYPE,
    SOLICITATION_TYPE,
    SolicitationsDataAccess,
)

PROGRAM_ID = 10008
SOURCE_GROUP_ID = 4492
SOURCE_PLAN_ID = 4494
OUTPUTS_PATH = Path(__file__).resolve().parent / "demo-vars.json"

QUESTIONS = [
    {
        "id": "q_method",
        "type": "textarea",
        "required": True,
        "text": (
            "Describe your field methodology for a two-arm matched-ward household coverage survey: "
            "sampling execution at the work-area level, household identification, and how you keep "
            "intervention and comparison protocols identical."
        ),
    },
    {
        "id": "q_staffing",
        "type": "textarea",
        "required": True,
        "text": (
            "How many enumerators and supervisors can you field in Kaura LGA, what is your supervision "
            "ratio, and what relevant household-survey experience does this team have?"
        ),
    },
    {
        "id": "q_qa",
        "type": "textarea",
        "required": True,
        "text": (
            "What is your data-quality plan: back-checks, GPS verification of visited households, and "
            "how you flag and re-visit suspect submissions?"
        ),
    },
]

CRITERIA = [
    {
        "id": "c_method",
        "name": "Survey Methodology & Sampling Rigor",
        "weight": 30,
        "description": "Fidelity to the two-arm matched-ward design at work-area level.",
        "scoring_guide": "Strong responses execute the sampling frame as designed and keep arm protocols identical.",
        "linked_questions": ["q_method"],
    },
    {
        "id": "c_staffing",
        "name": "Field Team Capacity & Experience",
        "weight": 25,
        "description": "Enumerator/supervisor capacity in Kaura and comparable survey experience.",
        "scoring_guide": "Strong responses name real team sizes, supervision ratios, and prior comparable surveys.",
        "linked_questions": ["q_staffing"],
    },
    {
        "id": "c_quality",
        "name": "Data Quality Assurance",
        "weight": 20,
        "description": "Back-checks, GPS verification, and suspect-data handling.",
        "scoring_guide": "Strong responses commit to concrete back-check rates and re-visit rules.",
        "linked_questions": ["q_qa"],
    },
    {
        "id": "c_independence",
        "name": "Independence & Conflict Management",
        "weight": 15,
        "description": "No delivery-side ties that could bias measured outcomes.",
        "scoring_guide": "Strong responses disclose relationships and separate measurement staff from delivery.",
        "linked_questions": [],
    },
    {
        "id": "c_timeline",
        "name": "Timeline & Deliverables",
        "weight": 10,
        "description": "Credible plan to complete within the Sep-Nov window.",
        "scoring_guide": "Strong responses map enumerator-days to the ~12,000-household scale.",
        "linked_questions": [],
    },
]

# AI-proposed per-criterion scores for Sahel — hidden until Maya records her own.
SAHEL_AI_SCORES = {
    "c_method": {"score": 9, "rationale": "Executes the sampling frame as designed; arm blinding for field teams."},
    "c_staffing": {
        "score": 8,
        "rationale": "24 enumerators, 6:1 supervision, named 2025 vitamin-A endline of ~9,500 HH.",
    },
    "c_quality": {"score": 9, "rationale": "15% independent back-checks within 72h, doorstep GPS with polygon flags."},
    "c_independence": {"score": 8, "rationale": "Separates measurement from delivery; discloses relationships."},
    "c_timeline": {
        "score": 7,
        "rationale": "Plausible for the window; enumerator-day mapping could be more explicit.",
    },
}

RESPONSES = [
    {
        "key": "sahel_response_id",
        "org": "Sahel Field Research",
        "email": "bids@sahelfieldresearch.org",
        "ai_proposed_scores": SAHEL_AI_SCORES,
        "answers": {
            "q_method": (
                "We execute the sampling frame exactly as designed: enumerator teams work the pre-drawn "
                "work areas in both Attakar (intervention) and Gura (comparison), using identical listing "
                "and consent scripts in both arms. Households are identified by rooftop-listing against the "
                "plan's work-area boundaries, then confirmed on the ground; substitutions follow the plan's "
                "alternate list only, logged with a reason code. Arm assignment is never disclosed to field "
                "teams — supervisors carry the same protocol book in both wards, and any deviation is recorded "
                "in the field log and reported in the weekly methods memo."
            ),
            "q_staffing": (
                "We can field 24 enumerators and 4 supervisors in Kaura LGA (a 6:1 supervision ratio), drawn "
                "from the team that completed a 2025 vitamin-A endline of ~9,500 households across three Kano "
                "LGAs. All enumerators are Hausa-speaking, tablet-equipped, and trained on household coverage "
                "modules; supervisors have run matched-cluster fieldwork before."
            ),
            "q_qa": (
                "Our QA plan: independent back-checks on 15% of completed households (re-visit within 72 hours "
                "by a different enumerator), GPS capture at the doorstep with automated flags for points outside "
                "the assigned work-area polygon, and a daily anomaly review — duration outliers, straight-lining, "
                "and duplicate coordinates — with mandatory re-visits for any flagged submission."
            ),
        },
    },
    {
        "key": "competitor_a_response_id",
        "org": "Nasarawa Data Collective",
        "email": "proposals@nasarawadata.ng",
        "answers": {
            "q_method": (
                "We would conduct a household survey across the two study wards using our standard cluster "
                "methodology, adapting the sampling approach as field conditions require. Our teams are "
                "experienced in Northern Nigeria and would coordinate with community leaders to identify "
                "households efficiently."
            ),
            "q_staffing": (
                "We maintain a roster of enumerators across several states and would assign 12-16 to this "
                "engagement, with supervision arranged per our standard practice. Recent work includes market "
                "surveys and a WASH assessment."
            ),
            "q_qa": (
                "Supervisors spot-check questionnaires daily and our office team reviews "
                "submissions for completeness before delivery."
            ),
        },
    },
    {
        "key": "competitor_b_response_id",
        "org": "Horizon Field Metrics",
        "email": "bd@horizonfieldmetrics.com",
        "answers": {
            "q_method": (
                "Horizon proposes a rapid coverage assessment using LQAS-style sampling in place of the full "
                "two-arm frame, which we believe delivers comparable insight at lower cost. We would treat the "
                "two wards as a single survey domain."
            ),
            "q_staffing": (
                "8 enumerators and 1 coordinator, primarily deployed from our Abuja office "
                "with local guides hired on arrival."
            ),
            "q_qa": "Data is reviewed at the end of fieldwork prior to submission of the final dataset.",
        },
    },
]

COMPETITOR_REVIEWS = {
    "competitor_a_response_id": {
        "score": 64,
        "recommendation": "needs_revision",
        "criteria_scores": {"c_method": 6, "c_staffing": 7, "c_quality": 5, "c_independence": 7, "c_timeline": 6},
        "notes": (
            "Capable generalist team, but the methodology answer proposes adapting the sampling approach "
            "in the field — the matched-ward frame must be executed as designed. QA relies on spot-checks "
            "with no committed back-check rate."
        ),
    },
    "competitor_b_response_id": {
        "score": 41,
        "recommendation": "rejected",
        "criteria_scores": {"c_method": 3, "c_staffing": 4, "c_quality": 4, "c_independence": 7, "c_timeline": 5},
        "notes": (
            "Proposes replacing the two-arm matched design with a single-domain LQAS assessment — this "
            "does not answer the study as designed. Team size is thin for ~12,000 households in the window."
        ),
    },
}


def main() -> int:
    # (1) Register program 10008 as labs-only so the API client uses the local backend.
    opp, created = SyntheticOpportunity.objects.get_or_create(
        opportunity_id=PROGRAM_ID,
        defaults={
            "labs_only": True,
            "enabled": True,
            "program_id": PROGRAM_ID,
            "org_name": "Kaura Health",
            "program_name": "R6 — Attakar × Gura (Vitamin-A)",
        },
    )
    if not created:
        opp.labs_only = True
        opp.enabled = True
        opp.program_id = PROGRAM_ID
        opp.save()
    print(f"[seed_local] SyntheticOpportunity {PROGRAM_ID} labs_only registered (created={created})")

    da = SolicitationsDataAccess(program_id=str(PROGRAM_ID), access_token="local-dummy-token")

    # (2) Sweep prior seeded R6 solicitations to stay idempotent.
    from connect_labs.labs.synthetic import local_records_backend as backend

    doomed = backend.get_records(program_id=PROGRAM_ID, type=SOLICITATION_TYPE)
    swept = []
    for s in doomed:
        if (s.data or {}).get("source_group_id") == SOURCE_GROUP_ID:
            resp_rows = backend.get_records(type=RESPONSE_TYPE, labs_record_id=s.pk)
            child_ids = [r.pk for r in resp_rows]
            for r in resp_rows:
                rev_rows = backend.get_records(type="solicitation_review", labs_record_id=r.pk)
                child_ids += [rv.pk for rv in rev_rows]
            backend.delete_records(record_ids=[s.pk] + child_ids)
            swept.append(s.pk)
    print(f"[seed_local] swept prior R6 solicitations: {swept}")

    # (3) Create the matured, LOCKED review-stage call.
    sol = da.create_solicitation(
        {
            "title": "Solicitation for R6 — Attakar × Gura",
            "description": (
                "Independent household coverage survey across the R6 matched wards — enumerators visit "
                "sampled households in Attakar (intervention) and Gura (comparison) to measure vitamin-A "
                "coverage outcomes."
            ),
            "scope_of_work": "Coverage areas drawn from plan group 'R6 — Attakar × Gura'.",
            "solicitation_type": "rfp",
            "status": "closed",
            "application_deadline": "2026-08-15",
            "expected_start_date": "2026-09-01",
            "expected_end_date": "2026-11-30",
            "estimated_scale": "~12,000 households across 56 sampled settlements",
            "contact_email": "maya.okafor@kaura-health.gov.ng",
            "questions": QUESTIONS,
            "evaluation_criteria": CRITERIA,
            "plans": [
                {
                    "plan_id": SOURCE_PLAN_ID,
                    "name": "R6 — Attakar × Gura",
                    "wards": ["Attakar", "Gura"],
                    "work_area_count": 840,
                }
            ],
            "source_program_id": PROGRAM_ID,
            "source_group_id": SOURCE_GROUP_ID,
            "source_plan_ids": [SOURCE_PLAN_ID],
            "criteria_locked": True,
            "criteria_locked_at": datetime.now(timezone.utc).isoformat(),
            "is_public": True,
        }
    )
    review_sol_id = int(sol.pk)
    print(f"[seed_local] created LOCKED review-stage solicitation {review_sol_id}")

    outputs = {"review_solicitation_id": review_sol_id}
    for idx, spec_r in enumerate(RESPONSES):
        data = {
            "solicitation_id": review_sol_id,
            "responses": spec_r["answers"],
            "status": "submitted",
            "submitted_by_name": spec_r["org"],
            "submitted_by_email": spec_r["email"],
            "org_name": spec_r["org"],
            "llo_entity_id": "individual",
            "submission_date": (datetime.now(timezone.utc) - timedelta(days=9 - 3 * idx)).isoformat(),
            "selected_plan_ids": [SOURCE_PLAN_ID],
            "selected_plan_names": ["R6 — Attakar × Gura"],
        }
        if spec_r.get("ai_proposed_scores"):
            data["ai_proposed_scores"] = spec_r["ai_proposed_scores"]
        resp = da.create_response(solicitation_id=review_sol_id, llo_entity_id="individual", data=data)
        outputs[spec_r["key"]] = int(resp.pk)
        print(f"[seed_local]   response {resp.pk} — {spec_r['org']}")

    for key, review in COMPETITOR_REVIEWS.items():
        da.create_review(
            response_id=outputs[key],
            data={
                "response_id": outputs[key],
                "llo_entity_id": "individual",
                "score": review["score"],
                "recommendation": review["recommendation"],
                "criteria_scores": review["criteria_scores"],
                "notes": review["notes"],
                "reviewer_username": "maya.okafor",
                "review_date": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
            },
        )
        print(f"[seed_local]   pre-scored review for {key} ({review['score']}/100)")

    OUTPUTS_PATH.write_text(json.dumps(outputs, indent=1))
    print(f"[seed_local] OK — wrote {OUTPUTS_PATH.name}: {outputs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
