"""Record the manager-flow prepend: Northern Wk4 in_progress → bulk
Mark No Issue → Create Audit on the flagged FLW → audit detail → back
to review → Create Task with Coaching → task detail w/ OCS conversation.

Output: a single ``.webm`` in /tmp/par_preview/video_manager/ that's
later encoded + concatenated with the drill-through recording.

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
    click_row_button,
    row_button_labels,
    scroll_row_into_view,
    scroll_through_page,
    snap,
)

HERE = Path(__file__).resolve().parent
OUT_DIR = Path("/tmp/par_preview/video_manager")
MANIFEST = Path("/tmp/par_preview/manager_snapshot_manifest.json")
FLAGGED_FLW = "jumoke_n"


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

        # Scene 0: arrive at Wk4 in_progress weekly review.
        print("Scene 0: Wk4 in_progress weekly review")
        page.goto(wk4_url, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2_500)
        snap(rec, "wk4_in_progress")

        # Scene 1: title-row "Mark all non-flagged FLWs as No Issue" toolbar
        # button. Triggers 9 parallel decision POSTs then window.location.reload();
        # wait for the post-reload remount, then assert 9 "No Issues" pills
        # are rendered in the Decision column.
        print("Scene 1: Bulk Mark all non-flagged FLWs as No Issue")
        page.click("button:has-text('Mark all non-flagged FLWs as No Issue')")
        page.wait_for_function(
            "() => (document.body.innerText.match(/No Issues/g) || []).length >= 9",
            timeout=30_000,
        )
        page.wait_for_load_state("networkidle", timeout=30_000)
        page.wait_for_timeout(1_500)
        snap(rec, "after_bulk_mark")

        # Scene 2: scroll to flagged FLW, click Create Audit.
        print(f"Scene 2: Click Create Audit for {FLAGGED_FLW}")
        page.wait_for_selector(f"text={FLAGGED_FLW}", timeout=15_000)
        page.wait_for_timeout(800)
        scroll_row_into_view(page, FLAGGED_FLW)
        page.wait_for_timeout(1_200)
        print(f"  pre-click row buttons: {row_button_labels(page, FLAGGED_FLW)}")
        if not click_row_button(page, FLAGGED_FLW, "Create Audit"):
            raise RuntimeError(
                f"Create Audit button not found on {FLAGGED_FLW}'s row. "
                "Did a previous recorder run already create the audit? "
                "Re-run regenerate.py with cleanup_first=true."
            )
        page.wait_for_url("**/audit/**", timeout=30_000)
        page.wait_for_load_state("networkidle", timeout=30_000)
        page.wait_for_timeout(2_500)
        snap(rec, "audit_pass_clean")

        # Scene 3: smooth-scroll through audit page so all 5 pass thumbnails read.
        print("Scene 3: Audit detail — 5 good-pool pass images")
        scroll_through_page(page)
        page.wait_for_timeout(1_500)

        # Scene 4: back to Wk4 review.
        print("Scene 4: Back to Wk4 weekly review")
        page.goto(wk4_url, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2_500)
        snap(rec, "back_after_audit")

        # Scene 5: Create Task with Coaching for the flagged FLW. Per PR #272
        # the click creates the task + decision then navigates directly to
        # /tasks/<id>/edit/ — no separate View Task hop. The task page renders
        # with the coaching prompt as the description (no OCS conversation
        # yet — the manager fires that next via Initiate AI Assistant).
        print(f"Scene 5: Create Task with Coaching for {FLAGGED_FLW}")
        scroll_row_into_view(page, FLAGGED_FLW)
        page.wait_for_timeout(1_200)
        if not click_row_button(page, FLAGGED_FLW, "Create Task with Coaching"):
            raise RuntimeError(f"Create Task with Coaching button not found on {FLAGGED_FLW}'s row.")
        page.wait_for_url("**/tasks/**", timeout=30_000)
        page.wait_for_load_state("networkidle", timeout=30_000)
        page.wait_for_timeout(2_500)
        snap(rec, "task_page_arrived")

        # Scene 6: open the "Initiate AI Assistant" modal. The bot dropdown
        # gets populated via /tasks/api/ocs/bots/ which returns the synthetic
        # "MUAC Coaching" entry for synthetic opps (see OCSBotsListAPIView
        # short-circuit in commcare_connect/tasks/views.py). The prompt
        # textarea pre-fills from this.taskForm.description via showAIModal()
        # in task_create_edit.html.
        print("Scene 6: Open Initiate AI Assistant modal")
        page.click("button:has-text('Initiate AI Assistant')")
        page.wait_for_function(
            "() => { const ta = document.querySelector('textarea[placeholder=\\\"Instructions for the bot...\\\"]'); return ta && ta.value && ta.value.length > 50; }",
            timeout=15_000,
        )
        # Wait for the synthetic bot to appear in the dropdown, then select it.
        page.wait_for_function(
            "() => [...document.querySelectorAll('select')].some(s => [...s.options].some(o => o.value === 'synthetic-muac-coaching'))",
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
        page.wait_for_load_state("networkidle", timeout=15_000)
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
