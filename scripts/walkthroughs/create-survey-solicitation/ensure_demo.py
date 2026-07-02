"""Pre-render ensure-and-sweep for the create-survey-solicitation walkthrough.

The walkthrough films a **fully fresh lifecycle on camera**: scene 2 clicks
"Create Solicitation" (minting a brand-new ``type=solicitation`` record on program
10008), scene 3 submits a response, scene 4 reads the response id off the page, and
scene 5 awards it — all threaded through the canopy ``capture`` action
(``${solicitation_id}`` / ``${response_id}``). There is no fixed canonical record.

What this script does, per render, **before** the recorder starts:

1. **Ensure** the R6 — Attakar × Gura study group **4492** and its plan **4494**
   exist on program 10008 (the study-design demo seeds them). If either is
   missing, ERROR loudly — scenes 1-2 depend on them (the portfolio's "ready to
   solicit" group card and the create form's snapshotted coverage map).
2. **Sweep** EVERY ``type=solicitation`` record on program 10008 whose
   ``data.source_group_id == 4492`` (every call the walkthrough has ever minted),
   **and** all of their responses (``delete_solicitation`` with ``force=True``
   cascades the child responses + reviews). Nothing is kept — each render mints
   its own fresh call, so the prior render's call + responses are cleared before
   the next is minted, keeping the portfolio from accumulating duplicate R6 calls.

**Transport — the MCP, not AWS/ECS.** Records on program 10008 are labs-only
synthetic records (opp id >= 10_000) living in the labs prod DB behind the
local-records backend. As of PR #678 the ``connect_labs`` MCP tools route
labs-only opps to that backend and grant access to opted-in callers, so this
script talks to the MCP over HTTP (same transport as the verified-monitoring
``regenerate.py`` seeder) instead of firing a one-off ``aws ecs run-task``. No AWS
session is required.

Requirements:
- A labs MCP token: ``LABS_MCP_TOKEN`` env, or a ``connect_labs`` server configured
  in ``~/.claude.json`` (Claude Code writes it there). The caller's user must have
  ``view_synthetic_opps`` enabled (``synthetic_set_my_visibility``) — labs-only
  access is gated on the opt-in, not on AWS.

Usage::

    python scripts/walkthroughs/create-survey-solicitation/ensure_demo.py

Env overrides: LABS_MCP_URL, LABS_MCP_TOKEN.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

# Make the shared seeder MCP client importable whether run as a module or a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _mcp_client import call, session, token  # noqa: E402

PROGRAM_ID = 10008
SOURCE_GROUP_ID = 4492  # R6 — Attakar × Gura study group scenes 1-2 solicit from
SOURCE_PLAN_ID = 4494  # the R6 plan snapshotted as the coverage area

# Setup-outputs contract: the recorder reads this JSON (spec `setup.outputs`) and
# resolves ${review_solicitation_id} / ${sahel_response_id} in scene URLs.
OUTPUTS_PATH = Path(__file__).resolve().parent / "demo-vars.json"

# ---------------------------------------------------------------------------
# Review-stage fixture — the "two weeks later" half of the story.
#
# Scenes 1-6 film a fully fresh lifecycle (mint the call, apply with the coach)
# on a solicitation created ON CAMERA. Scenes 7-8 need the part no single take
# can produce live: a call whose response window has PLAYED OUT — three firms
# applied, two already scored — so Maya's review of Sahel and the award are a
# genuine choice among scored alternatives. This seeder builds that later-stage
# call fresh each render (and the sweep clears it next render, same as the
# on-camera one — both carry source_group_id=4492).
# ---------------------------------------------------------------------------

QUESTIONS = [
    {
        "id": "q_method",
        "text": (
            "Describe your field methodology for a two-arm matched-ward household "
            "coverage survey: sampling execution at the work-area level, household "
            "identification, and how you keep intervention and comparison protocols identical."
        ),
        "type": "textarea",
        "required": True,
    },
    {
        "id": "q_staffing",
        "text": (
            "How many enumerators and supervisors can you field in Kaura LGA, what is "
            "your supervision ratio, and what relevant household-survey experience does "
            "this team have?"
        ),
        "type": "textarea",
        "required": True,
    },
    {
        "id": "q_qa",
        "text": (
            "What is your data-quality plan: back-checks, GPS verification of visited "
            "households, and how you flag and re-visit suspect submissions?"
        ),
        "type": "textarea",
        "required": True,
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

RESPONSES = [
    {
        "key": "sahel_response_id",
        "org": "Sahel Field Research",
        "email": "bids@sahelfieldresearch.org",
        "answers": {
            "q_method": (
                "We execute the sampling frame exactly as designed: enumerator teams work the "
                "pre-drawn work areas in both Attakar (intervention) and Gura (comparison), "
                "using identical listing and consent scripts in both arms. Households are "
                "identified by rooftop-listing against the plan's work-area boundaries, then "
                "confirmed on the ground; substitutions follow the plan's alternate list only, "
                "logged with a reason code. Arm assignment is never disclosed to field teams — "
                "supervisors carry the same protocol book in both wards, and any deviation is "
                "recorded in the field log and reported in the weekly methods memo."
            ),
            "q_staffing": (
                "We can field 24 enumerators and 4 supervisors in Kaura LGA (a 6:1 supervision "
                "ratio), drawn from the team that completed a 2025 vitamin-A endline of ~9,500 "
                "households across three Kano LGAs. All enumerators are Hausa-speaking, "
                "tablet-equipped, and trained on household coverage modules; supervisors have "
                "run matched-cluster fieldwork before and understand why the comparison arm's "
                "protocol discipline matters as much as the intervention arm's."
            ),
            "q_qa": (
                "Our QA plan: independent back-checks on 15% of completed households (re-visit "
                "within 72 hours by a different enumerator), GPS capture at the doorstep with "
                "automated flags for points outside the assigned work-area polygon, and a "
                "daily anomaly review — duration outliers, straight-lining, and duplicate "
                "coordinates — with mandatory re-visits for any flagged submission before it "
                "enters the dataset."
            ),
        },
    },
    {
        "key": "competitor_a_response_id",
        "org": "Nasarawa Data Collective",
        "email": "proposals@nasarawadata.ng",
        "answers": {
            "q_method": (
                "We would conduct a household survey across the two study wards using our "
                "standard cluster methodology, adapting the sampling approach as field "
                "conditions require. Our teams are experienced in Northern Nigeria and would "
                "coordinate with community leaders to identify households efficiently."
            ),
            "q_staffing": (
                "We maintain a roster of enumerators across several states and would assign "
                "12-16 to this engagement, with supervision arranged per our standard "
                "practice. Recent work includes market surveys and a WASH assessment."
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
                "Horizon proposes a rapid coverage assessment using LQAS-style sampling in "
                "place of the full two-arm frame, which we believe delivers comparable insight "
                "at lower cost. We would treat the two wards as a single survey domain."
            ),
            "q_staffing": (
                "8 enumerators and 1 coordinator, primarily deployed from our Abuja office "
                "with local guides hired on arrival."
            ),
            "q_qa": ("Data is reviewed at the end of fieldwork prior to submission of the final " "dataset."),
        },
    },
]

# Pre-scored competitor reviews — Maya scores Sahel ON CAMERA in scene 7.
COMPETITOR_REVIEWS = {
    "competitor_a_response_id": {
        "score": 64,
        "recommendation": "needs_revision",
        "criteria_scores": {"c_method": 6, "c_staffing": 7, "c_quality": 5, "c_independence": 7, "c_timeline": 6},
        "notes": (
            "Capable generalist team, but the methodology answer proposes adapting the "
            "sampling approach in the field — the matched-ward frame must be executed as "
            "designed. QA relies on spot-checks with no committed back-check rate."
        ),
    },
    "competitor_b_response_id": {
        "score": 41,
        "recommendation": "rejected",
        "criteria_scores": {"c_method": 3, "c_staffing": 4, "c_quality": 4, "c_independence": 7, "c_timeline": 5},
        "notes": (
            "Proposes replacing the two-arm matched design with a single-domain LQAS "
            "assessment — this does not answer the study as designed. Team size is thin "
            "for ~12,000 households in the stated window."
        ),
    },
}


def _ids(records, key="id"):
    out = set()
    for r in records or []:
        v = r.get(key) if isinstance(r, dict) else None
        if v is not None:
            try:
                out.add(int(v))
            except (TypeError, ValueError):
                pass
    return out


def _sgid(sol: dict):
    data = sol.get("data") or {}
    v = data.get("source_group_id", sol.get("source_group_id"))
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def main() -> int:
    with httpx.Client(timeout=600) as c:
        h = session(c, token())

        # (1) Ensure the study-design demo seeds (group 4492 + plan 4494) exist.
        plans_res, perr = call(c, h, "microplans_list_plans", {"program_id": PROGRAM_ID})
        if perr or not isinstance(plans_res, dict):
            print(
                f"[ensure_demo] FAILED: microplans_list_plans({PROGRAM_ID}) error: " f"{str(plans_res)[:300]}",
                file=sys.stderr,
            )
            return 1
        plan_ids = _ids(plans_res.get("plans"))
        group_ids = _ids(plans_res.get("groups"), key="group_id")
        group_present = SOURCE_GROUP_ID in group_ids
        plan_present = SOURCE_PLAN_ID in plan_ids
        if not group_present or not plan_present:
            print(
                f"[ensure_demo] FAILED: seed missing on program {PROGRAM_ID} "
                f"(group {SOURCE_GROUP_ID} present={group_present}, "
                f"plan {SOURCE_PLAN_ID} present={plan_present}). "
                "Run the study-design demo seeder.",
                file=sys.stderr,
            )
            return 1

        # (2) Sweep every R6 call this walkthrough has minted + their responses.
        sols_res, serr = call(c, h, "list_solicitations", {"program_id": PROGRAM_ID})
        if serr or not isinstance(sols_res, dict):
            print(f"[ensure_demo] FAILED: list_solicitations error: {str(sols_res)[:300]}", file=sys.stderr)
            return 1
        doomed = [s for s in (sols_res.get("solicitations") or []) if _sgid(s) == SOURCE_GROUP_ID]
        deleted = []
        for s in doomed:
            sid = s.get("id")
            _, derr = call(
                c, h, "delete_solicitation", {"solicitation_id": sid, "program_id": PROGRAM_ID, "force": True}
            )
            if derr:
                print(f"[ensure_demo] FAILED: delete_solicitation({sid}) error", file=sys.stderr)
                return 1
            deleted.append(sid)

        # Re-read to confirm nothing source_group_id==4492 survives.
        after_res, aerr = call(c, h, "list_solicitations", {"program_id": PROGRAM_ID})
        remaining = (
            [s.get("id") for s in (after_res.get("solicitations") or []) if _sgid(s) == SOURCE_GROUP_ID]
            if isinstance(after_res, dict)
            else ["<reread-failed>"]
        )
        if remaining:
            print(
                f"[ensure_demo] FAILED: {len(remaining)} source_group_id={SOURCE_GROUP_ID} "
                f"solicitations survived the sweep: {remaining}",
                file=sys.stderr,
            )
            return 1

        # (3) Build the review-stage fixture: a matured R6 call with three firm
        # responses, two of them already scored. Scenes 7-8 review + award on it.
        OUTPUTS_PATH.unlink(missing_ok=True)
        sol_res, cerr = call(
            c,
            h,
            "create_solicitation",
            {
                "program_id": str(PROGRAM_ID),
                "title": "Solicitation for R6 — Attakar × Gura",
                "description": (
                    "Independent household coverage survey across the R6 matched wards — "
                    "enumerators visit sampled households in Attakar (intervention) and Gura "
                    "(comparison) to measure vitamin-A coverage outcomes."
                ),
                "scope_of_work": "Coverage areas drawn from plan group 'R6 — Attakar × Gura'.",
                "solicitation_type": "rfp",
                "status": "active",
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
                "source_group_id": SOURCE_GROUP_ID,
                "source_plan_ids": [SOURCE_PLAN_ID],
            },
        )
        if cerr or not isinstance(sol_res, dict) or not sol_res.get("id"):
            print(f"[ensure_demo] FAILED: create_solicitation error: {str(sol_res)[:400]}", file=sys.stderr)
            return 1
        review_sol_id = int(sol_res["id"])

        outputs = {"review_solicitation_id": review_sol_id}
        for spec_r in RESPONSES:
            resp_res, rerr = call(
                c,
                h,
                "create_response",
                {
                    "solicitation_id": review_sol_id,
                    "program_id": str(PROGRAM_ID),
                    "responses": spec_r["answers"],
                    "status": "submitted",
                    "submitted_by_name": spec_r["org"],
                    "submitted_by_email": spec_r["email"],
                    "org_name": spec_r["org"],
                    "selected_plan_ids": [SOURCE_PLAN_ID],
                    "selected_plan_names": ["R6 — Attakar × Gura"],
                },
            )
            if rerr or not isinstance(resp_res, dict) or not resp_res.get("id"):
                print(
                    f"[ensure_demo] FAILED: create_response({spec_r['org']}) error: {str(resp_res)[:400]}",
                    file=sys.stderr,
                )
                return 1
            outputs[spec_r["key"]] = int(resp_res["id"])

        for key, review in COMPETITOR_REVIEWS.items():
            rev_res, verr = call(
                c,
                h,
                "create_review",
                {
                    "public_record_acknowledged": True,
                    "response_id": outputs[key],
                    "llo_entity_id": "individual",
                    "program_id": PROGRAM_ID,
                    "score": review["score"],
                    "recommendation": review["recommendation"],
                    "criteria_scores": review["criteria_scores"],
                    "notes": review["notes"],
                    "reviewer_username": "maya.okafor",
                },
            )
            if verr or not isinstance(rev_res, dict):
                print(f"[ensure_demo] FAILED: create_review({key}) error: {str(rev_res)[:400]}", file=sys.stderr)
                return 1

        OUTPUTS_PATH.write_text(json.dumps(outputs, indent=1))

        print(
            f"[ensure_demo] OK — group {SOURCE_GROUP_ID}/plan {SOURCE_PLAN_ID} present on "
            f"program {PROGRAM_ID}; swept {len(deleted)} prior R6 solicitation(s) "
            f"{deleted} + their responses. Scene 3 mints a fresh call on camera; "
            f"review-stage call {review_sol_id} seeded with 3 responses "
            f"(Sahel {outputs['sahel_response_id']}, competitors scored 64/41) → {OUTPUTS_PATH.name}.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
