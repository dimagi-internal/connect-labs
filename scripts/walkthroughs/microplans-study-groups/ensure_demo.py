"""Pre-render ensure-and-sweep for the microplans-study-groups walkthrough.

The walkthrough films a **fully fresh two-arm study built on camera** on the
labs-only Kaura programme (program ``10008``):

* **Scene 1** clicks "+ New study" — minting a brand-new ``kind=study`` group
  ("Untitled study", 0 plans) and routing to its empty manage page.
* **Scenes 2-6** open the single-plan editor scoped to that study (``?group=<id>``),
  pick the Attakar intervention ward, compare surrounding boundaries, add Gura as
  the control, and click **Generate plan** — which creates a NEW plan (a fresh id
  each render) filed into the study and lands on its ``/plan/<id>/review/`` page.
* **Scene 7** advances a plan draft → in_review → approved from the program
  workspace lifecycle board, walking the study to completion.

Because scenes 1 and 6 each MINT records (a new study group + a new generated
plan) and the spec has no in-camera teardown, every prior render leaves residue:

* an empty **"Untitled study"** group (e.g. ``4556``) + its generated member plan
  (e.g. ``4557``) — these are the "wall of Untitled study cards" a judge flagged
  on the shared portfolio, and the wrong study scene 1's next render lands on;
* orphan loose ``Attakar`` / ``Gura`` drafts (e.g. ``4465`` Gura) left over from
  prior generate passes.

This script, run by the canopy ``setup:`` block **before** the recorder starts,
makes program 10008 idempotent:

1. **Sweep** every residue **Untitled / empty-named study group** (``kind=study``,
   name in {"Untitled study", ""}) EXCEPT the load-bearing R6 group
   (``KEEP_GROUP_IDS``): delete its member plans (minus protected ids) then the
   group itself.
2. **Sweep** orphan loose residue plans — plans named exactly ``Attakar`` or
   ``Gura`` that are NOT protected and NOT inside a kept group — the leftovers a
   prior render's Generate created.
3. **Ensure the scene-7 lifecycle plan** (``LIFECYCLE_PLAN_ID``, a stable loose
   ``Attakar`` draft) exists and is back in ``draft`` — if a prior render's scene 7
   advanced it to in_review/approved, walk it back so scene 7 can re-advance it
   this render. Scene 7 targets this plan by id
   (``button[data-transition="<LIFECYCLE_PLAN_ID>"]``).
4. **Assert** the load-bearing R6 — Attakar × Gura study group ``4492`` and its
   approved plan ``4494`` survive (scene 1's portfolio shows the finished study;
   the solicitation demo solicits from it), and that the lifecycle plan is draft.

Conservative by construction: it only ever deletes Untitled/empty study groups and
loose ``Attakar``/``Gura`` residue plans, and NEVER touches the protected ids
(R6 group/plan, the R1-R5 study-design rounds, Kwaki, or the lifecycle plan).
Re-runnable; a no-op once the program is clean.

**Transport — the MCP, not AWS/ECS.** Records on program 10008 are labs-only
synthetic records (opp id >= 10_000) behind the local-records backend. As of
PR #678 the ``connect_labs`` MCP tools route labs-only opps to that backend and
grant access to opted-in callers, so this script talks to the MCP over HTTP (same
transport as the create-survey-solicitation seeder). No AWS session required.

Requirements:
- A labs MCP token: ``LABS_MCP_TOKEN`` env, or a ``connect_labs`` server in
  ``~/.claude.json``. The caller must have ``view_synthetic_opps`` enabled
  (``synthetic_set_my_visibility``) — labs-only access is gated on the opt-in.

Usage::

    python scripts/walkthroughs/microplans-study-groups/ensure_demo.py

Env overrides: LABS_MCP_URL, LABS_MCP_TOKEN.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

# Make the shared seeder MCP client importable whether run as a module or a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _mcp_client import call, session, token  # noqa: E402

PROGRAM_ID = 10008

# The load-bearing R6 — Attakar × Gura study (scene 1's portfolio hero + the
# solicitation demo's source). NEVER swept.
KEEP_GROUP_IDS = {4492}
KEEP_PLAN_IDS = {
    4494,  # R6 — Attakar × Gura, approved, member of group 4492
    4445,  # R6 — Attakar × Gura, draft (study-design round)
    4446,  # R1 — Tse × Danto      (study-design round)
    4447,  # R2 — Agban × Kukum    (study-design round)
    4448,  # R3 — Kadarko × Kpak   (study-design round)
    4449,  # R4 — Bondong × Manchok(study-design round)
    4450,  # R5 — Zankan × Kaura   (study-design round)
    4274,  # Kwaki/Chikuba         (unrelated demo)
}

# The stable loose draft plan scene 7 advances on the program workspace lifecycle
# board (its row-scoped data-transition button is keyed to this id). Kept across
# renders and reset to `draft` each time so the lifecycle can be re-walked.
LIFECYCLE_PLAN_ID = 4493

# Names a prior render's "+ New study" / "Generate plan" leaves behind. We only
# delete loose plans whose name is one of these AND that are not protected.
RESIDUE_PLAN_NAMES = {"Attakar", "Gura", "Untitled plan", ""}
# Study-group names a prior scene 1 leaves behind ("+ New study" creates this).
RESIDUE_GROUP_NAMES = {"Untitled study", "untitled study", ""}

# Walk-back path so an advanced lifecycle plan returns to draft.
RESET_PATH = {"approved": ["in_review", "draft"], "in_review": ["draft"]}


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _delete_plan(c, h, pid, log):
    _, err = call(c, h, "microplans_delete_plan", {"program_id": PROGRAM_ID, "plan_id": pid})
    if err:
        print(f"[ensure_demo] FAILED: microplans_delete_plan({pid})", file=sys.stderr)
        return False
    log.append(pid)
    return True


def main() -> int:
    with httpx.Client(timeout=600) as c:
        h = session(c, token())

        res, err = call(c, h, "microplans_list_plans", {"program_id": PROGRAM_ID})
        if err or not isinstance(res, dict):
            print(f"[ensure_demo] FAILED: microplans_list_plans error: {str(res)[:300]}", file=sys.stderr)
            return 1

        plans = res.get("plans") or []
        groups = res.get("groups") or []
        plan_by_id = {_to_int(p.get("id")): p for p in plans if _to_int(p.get("id")) is not None}
        kept_group_member_ids = {
            _to_int(pid)
            for g in groups
            if _to_int(g.get("group_id")) in KEEP_GROUP_IDS
            for pid in (g.get("plan_ids") or [])
        }
        protected = KEEP_PLAN_IDS | {LIFECYCLE_PLAN_ID} | kept_group_member_ids

        deleted_plans: list[int] = []
        deleted_groups: list[int] = []

        # (1) Sweep residue Untitled / empty study groups + their member plans.
        for g in groups:
            gid = _to_int(g.get("group_id"))
            if gid is None or gid in KEEP_GROUP_IDS:
                continue
            if g.get("kind") != "study":
                continue
            name = (g.get("name") or "").strip()
            if name not in RESIDUE_GROUP_NAMES:
                continue  # a real, named study — leave it (conservative)
            for pid in g.get("plan_ids") or []:
                pid = _to_int(pid)
                if pid is None or pid in protected:
                    continue
                if not _delete_plan(c, h, pid, deleted_plans):
                    return 1
            _, gerr = call(c, h, "microplans_delete_group", {"program_id": PROGRAM_ID, "group_id": gid})
            if gerr:
                print(f"[ensure_demo] FAILED: microplans_delete_group({gid})", file=sys.stderr)
                return 1
            deleted_groups.append(gid)

        # (2) Sweep orphan loose residue plans (Attakar/Gura leftovers from prior
        #     Generate passes) that aren't protected and aren't in a kept group.
        for pid, p in plan_by_id.items():
            if pid in protected or pid in deleted_plans:
                continue
            name = (p.get("name") or "").strip()
            if name in RESIDUE_PLAN_NAMES:
                if not _delete_plan(c, h, pid, deleted_plans):
                    return 1

        # (3) Ensure the scene-7 lifecycle plan exists and is back in `draft`.
        life = plan_by_id.get(LIFECYCLE_PLAN_ID)
        if life is None:
            print(
                f"[ensure_demo] FAILED: scene-7 lifecycle plan {LIFECYCLE_PLAN_ID} is "
                f"missing on program {PROGRAM_ID}. It must be a stable loose Attakar "
                "draft; reseed it before rendering.",
                file=sys.stderr,
            )
            return 1
        status = (life.get("status") or "").strip()
        for to in RESET_PATH.get(status, []):
            _, terr = call(
                c, h, "microplans_transition_plan", {"program_id": PROGRAM_ID, "plan_id": LIFECYCLE_PLAN_ID, "to": to}
            )
            if terr:
                print(
                    f"[ensure_demo] FAILED: could not reset lifecycle plan " f"{LIFECYCLE_PLAN_ID} ({status} -> {to})",
                    file=sys.stderr,
                )
                return 1

        # (4) Re-read and assert the world is in the expected pre-render state.
        after, aerr = call(c, h, "microplans_list_plans", {"program_id": PROGRAM_ID})
        if aerr or not isinstance(after, dict):
            print(f"[ensure_demo] FAILED: re-read microplans_list_plans error: {str(after)[:300]}", file=sys.stderr)
            return 1
        after_plans = {_to_int(p.get("id")): p for p in (after.get("plans") or [])}
        after_groups = {_to_int(g.get("group_id")) for g in (after.get("groups") or [])}

        problems = []
        if 4492 not in after_groups:
            problems.append("R6 group 4492 missing")
        if 4494 not in after_plans:
            problems.append("R6 plan 4494 missing")
        life_after = after_plans.get(LIFECYCLE_PLAN_ID)
        if life_after is None:
            problems.append(f"lifecycle plan {LIFECYCLE_PLAN_ID} missing")
        elif (life_after.get("status") or "") != "draft":
            problems.append(f"lifecycle plan {LIFECYCLE_PLAN_ID} not in draft (is {life_after.get('status')})")
        # No Untitled study groups should survive the sweep.
        surviving_untitled = [
            _to_int(g.get("group_id"))
            for g in (after.get("groups") or [])
            if g.get("kind") == "study"
            and _to_int(g.get("group_id")) not in KEEP_GROUP_IDS
            and (g.get("name") or "").strip() in RESIDUE_GROUP_NAMES
        ]
        if surviving_untitled:
            problems.append(f"Untitled study groups survived: {surviving_untitled}")
        if problems:
            print(f"[ensure_demo] FAILED: post-sweep assertions: {'; '.join(problems)}", file=sys.stderr)
            return 1

        print(
            f"[ensure_demo] OK — program {PROGRAM_ID}: swept {len(deleted_groups)} residue "
            f"Untitled study group(s) {deleted_groups} + {len(deleted_plans)} residue plan(s) "
            f"{deleted_plans}; R6 group 4492/plan 4494 intact; scene-7 lifecycle plan "
            f"{LIFECYCLE_PLAN_ID} reset to draft. Scene 1 mints a fresh study on camera.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
