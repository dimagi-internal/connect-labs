"""Record the manager-flow prepend: Northern Wk4 in_progress → auto-flags
appear on mount → Create Audit (Audit Last 7 days) on the flagged FLW →
audit detail → back to review → Create Task (Coach on Flag implications)
→ task detail w/ OCS conversation.

Output: a single ``.webm`` in /tmp/par_preview/video_manager/ that's
later encoded + concatenated with the drill-through recording.

UI history:

  - PR #281 (Decisions → Flags) removed the "Mark all non-flagged FLWs
    as No Issue" toolbar button. Flags now auto-apply on mount via
    view.ensureAutoFlags, so the manager arrives at a fully-flagged
    page; nothing to bulk-mark. Per-row actions became two split-button
    menus.
  - PRs #285 + #286 simplified those menus to a small fixed catalog:
    Create Audit has {New Audit, Audit Last 7 days}; Create Task has
    {New Task, plus Coach on Flag implications when the row carries any
    flag}. There's no longer a flag-specific audit variant. The
    recorder picks ``Audit Last 7 days`` (mirrors a manager scoping the
    audit to recent visits during the live review) and ``Coach on Flag
    implications`` (the prompt is composed from the row's actual
    flag_label values, so it stays specific to whatever flags tripped).

All Playwright primitives + the cursor overlay live in
``scripts/walkthroughs/_lib/``; this file is just the scene sequence.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from walkthroughs._lib import config as wcfg  # noqa: E402
from walkthroughs._lib.recorder import (  # noqa: E402
    RecorderSession,
    click_menu_item,
    click_row_button,
    goto_and_settle,
    row_button_labels,
    scroll_row_into_view,
    scroll_through_page,
    snap,
)

HERE = Path(__file__).resolve().parent
OUT_DIR = Path("/tmp/par_preview/video_manager")
MANIFEST = Path("/tmp/par_preview/manager_snapshot_manifest.json")
FLAGGED_FLW = "jumoke_n"

# Menu-item labels from chc_nutrition's Actions cell (post PR #286).
# `Audit Last 7 days` is the non-default audit option; `Coach on Flag
# implications` only appears when the row carries any flag, which it
# does for FLAGGED_FLW by mount-time (auto-flag for sam_low/mam_low).
AUDIT_MENU_ITEM = "Audit Last 7 days"
COACHING_MENU_ITEM = "Coach on Flag implications"


def main() -> None:
    ids = wcfg.read_run_ids(HERE, required=["wk4_run_id", "opp_id", "workflow_def_id"])
    wk4_url = (
        f"{wcfg.LABS_BASE_URL}/labs/workflow/{ids['workflow_def_id']}"
        f"/run/?run_id={ids['wk4_run_id']}&opportunity_id={ids['opp_id']}"
    )
    print(f"Wk4 in_progress run {ids['wk4_run_id']} | " f"opp {ids['opp_id']} | flw {FLAGGED_FLW}")

    with RecorderSession(
        out_dir=OUT_DIR,
        manifest_path=MANIFEST,
        # Manager flow records all images coming in live (no need to pre-warm
        # the GDrive cache — the freshness of the in_progress data IS the demo).
        prewarm=False,
    ) as rec:
        page = rec.page

        # Scene 0: arrive at Wk4 in_progress weekly review. Use the
        # tolerant goto helper — networkidle never settles on labs because
        # of background PAR snapshot polling.
        print("Scene 0: Wk4 in_progress weekly review")
        goto_and_settle(
            page,
            wk4_url,
            timeout=60_000,
            wait_for_selector=f"text={FLAGGED_FLW}",
            settle_seconds=2.5,
        )
        snap(rec, "wk4_in_progress")

        # Scene 1: wait for the auto-applied flag pills to render. The
        # framework calls view.ensureAutoFlags on mount, POSTs the
        # computed flags to /flags/, and the table re-renders with one
        # pill per active flag after the post-write refetch. Wait for
        # any of the current canonical labels (PR #285) to appear so we
        # capture the post-mount state. 45s timeout because the
        # ensureAutoFlags POST + refetch round-trip on a freshly-loaded
        # synthetic run can take 15-25s on first hit.
        print("Scene 1: Auto-flags appear on mount")
        page.wait_for_function(
            "() => /SAM rate < 1%|MAM rate < 3%|Gender split outside 40-60%/" ".test(document.body.innerText)",
            timeout=45_000,
        )
        page.wait_for_timeout(1_500)
        snap(rec, "flags_auto_applied")

        # Scene 2: scroll to flagged FLW, open Create Audit menu, click
        # `Audit Last 7 days`. The menu trigger is a MenuButton with
        # label "Create Audit"; the items are the fixed catalog from
        # PR #286 — {New Audit, Audit Last 7 days}.
        print(f"Scene 2: Create Audit → {AUDIT_MENU_ITEM} for {FLAGGED_FLW}")
        page.wait_for_selector(f"text={FLAGGED_FLW}", timeout=15_000)
        page.wait_for_timeout(800)
        scroll_row_into_view(page, FLAGGED_FLW)
        page.wait_for_timeout(1_200)
        print(f"  pre-click row buttons: {row_button_labels(page, FLAGGED_FLW)}")
        if not click_row_button(page, FLAGGED_FLW, "Create Audit"):
            raise RuntimeError(
                f"Create Audit menu trigger not found on {FLAGGED_FLW}'s row. "
                "Did a previous recorder run already create the audit? "
                "Re-run regenerate.py with cleanup_first=true."
            )
        # The menu opens as an absolute-positioned panel — give it a beat
        # to mount before we look inside.
        page.wait_for_timeout(600)
        if not click_menu_item(page, AUDIT_MENU_ITEM):
            raise RuntimeError(
                f"Menu item {AUDIT_MENU_ITEM!r} not found after opening Create Audit menu for {FLAGGED_FLW}."
            )
        page.wait_for_url("**/audit/**", timeout=30_000)
        # Wait for the audit's assessment count header instead of networkidle —
        # bulk-assessment images stream from GDrive and never let networkidle fire.
        page.wait_for_selector("text=Total Assessments", timeout=30_000)
        page.wait_for_timeout(2_500)
        snap(rec, "audit_pass_clean")

        # Scene 3: smooth-scroll through audit page so all 5 pass thumbnails read.
        print("Scene 3: Audit detail — 5 good-pool pass images")
        scroll_through_page(page)
        page.wait_for_timeout(1_500)

        # Scene 4: back to Wk4 review.
        print("Scene 4: Back to Wk4 weekly review")
        goto_and_settle(
            page,
            wk4_url,
            timeout=60_000,
            wait_for_selector=f"text={FLAGGED_FLW}",
            settle_seconds=2.5,
        )
        snap(rec, "back_after_audit")

        # Scene 5: open Create Task menu, click `Coach on Flag implications`.
        # Per PR #286 the coaching item only shows up when the row carries
        # any flag — for FLAGGED_FLW the auto-flag is sam_low/mam_low.
        # The render-time onClick builds {description, coaching_prompt}
        # from the row's actual flag_label values; task_single_create stores
        # coaching_prompt in task.data and the AI modal pre-fills from
        # there (PR #282). Note: trigger label is "Create Task" (not "Send
        # Task" — renamed in PR #285).
        print(f"Scene 5: Create Task → {COACHING_MENU_ITEM} for {FLAGGED_FLW}")
        scroll_row_into_view(page, FLAGGED_FLW)
        page.wait_for_timeout(1_200)
        if not click_row_button(page, FLAGGED_FLW, "Create Task"):
            raise RuntimeError(f"Create Task menu trigger not found on {FLAGGED_FLW}'s row.")
        page.wait_for_timeout(600)
        if not click_menu_item(page, COACHING_MENU_ITEM):
            raise RuntimeError(
                f"Menu item {COACHING_MENU_ITEM!r} not found after opening "
                f"Create Task menu for {FLAGGED_FLW}. Auto-flags may not have "
                "populated the row, so the conditional coaching item never rendered."
            )
        page.wait_for_url("**/tasks/**", timeout=30_000)
        # Wait for the task page header — networkidle won't fire because of
        # the long-polling AI session check on the task page.
        page.wait_for_selector("text=Initiate AI Assistant", timeout=30_000)
        page.wait_for_timeout(2_500)
        snap(rec, "task_page_arrived")

        # Scene 6: open the "Initiate AI Assistant" modal. The bot dropdown
        # gets populated via /tasks/api/ocs/bots/ which returns the synthetic
        # "MUAC Coaching" entry for synthetic opps (see OCSBotsListAPIView
        # short-circuit in commcare_connect/tasks/views.py). The prompt
        # textarea pre-fills from this.taskForm.coaching_prompt (PR #282)
        # — the long-form bot opener — via showAIModal() in
        # task_create_edit.html, falling back to description for tasks
        # created before that PR.
        print("Scene 6: Open Initiate AI Assistant modal")
        page.click("button:has-text('Initiate AI Assistant')")
        page.wait_for_function(
            "() => { const ta = document.querySelector("
            "'textarea[placeholder=\\\"Instructions for the bot...\\\"]'"
            "); return ta && ta.value && ta.value.length > 50; }",
            timeout=15_000,
        )
        # Wait for the synthetic bot to appear in the dropdown, then select it.
        page.wait_for_function(
            "() => [...document.querySelectorAll('select')].some("
            "s => [...s.options].some(o => o.value === 'synthetic-muac-coaching'))",
            timeout=10_000,
        )
        page.evaluate(
            "() => { const sel = [...document.querySelectorAll('select')]"
            ".find(s => [...s.options].some(o => o.value === 'synthetic-muac-coaching'));"
            " if (sel) { sel.value = 'synthetic-muac-coaching';"
            " sel.dispatchEvent(new Event('input', {bubbles:true}));"
            " sel.dispatchEvent(new Event('change', {bubbles:true})); } }"
        )
        page.wait_for_timeout(1_500)
        snap(rec, "ai_modal_prompt_prefilled")

        # Scene 7: Manager taps a small edit to the prompt. Visually conveys
        # "manager is tailoring this" without rewriting the whole thing.
        print("Scene 7: Edit the prompt slightly")
        page.evaluate(
            "() => { const ta = document.querySelector('textarea[placeholder=\\\"Instructions for the bot...\\\"]');"
            " if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); } }"
        )
        page.keyboard.type(" Please be friendly.", delay=60)
        page.wait_for_timeout(1_500)
        snap(rec, "ai_modal_prompt_edited")

        # Scene 8: click the modal's "Initiate AI" button (not the outer
        # "Initiate AI Assistant" — selector scoped via exact text match).
        # The synthetic short-circuit in task_initiate_ai writes the canned
        # _coaching_conversation onto task.data.ocs_conversation and returns
        # success; the modal's success path reloads the page after 2s and
        # the "Coaching Conversation" block renders.
        print("Scene 8: Click Initiate AI → coaching conversation appears")
        page.evaluate(
            "() => { const btns = [...document.querySelectorAll('button')]"
            ".filter(b => b.innerText.trim() === 'Initiate AI' && !b.disabled);"
            " if (btns[0]) btns[0].click(); }"
        )
        page.wait_for_function(
            "() => document.body.innerText.includes('Coaching Conversation')",
            timeout=30_000,
        )
        # The post-reload page has a long-poll on /tasks/<id>/ai/sessions/
        # that prevents networkidle. The 'Coaching Conversation' selector
        # is the real signal of "we're ready to scroll + capture".
        page.wait_for_timeout(2_000)
        page.evaluate(
            "() => { const els = [...document.querySelectorAll('*')]"
            ".filter(e => e.innerText && e.innerText.includes('Coaching Conversation'));"
            " if (els[0]) els[0].scrollIntoView({block: 'center'}); }"
        )
        page.wait_for_timeout(3_000)
        snap(rec, "task_ocs_conversation")

    print(f"\nManifest: {MANIFEST}")


if __name__ == "__main__":
    main()
