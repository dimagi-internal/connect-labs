# Audit & QA Review

The Audit module lets program managers and supervisors review field worker (FLW) visit images for quality assurance. You can sample visits from CommCare, assess images against program standards, and optionally use AI to pre-screen before human review.

---

## How It Works

```mermaid
flowchart LR
    A[Select FLWs\nand date range] --> B[Labs extracts\nvisit images]
    B --> C{AI pre-screen?\nOptional}
    C -->|Yes| D[AI flags\nsuspect images]
    C -->|No| E[Human review\nbulk assessment]
    D --> E
    E --> F[Pass / Fail\nper image]
    F --> G[Session complete\nwith overall result]
```

---

## Creating an Audit Session

Navigate to **Audit** in the top menu, then click **Create Audit Session**.

**Step 1 — Choose your scope:**

- Select the **opportunity** from the search table — the table shows the opportunity name, its **Program**, and other details so you can confirm you are selecting the right one
- Set a **date range** for visits to review
- Set how many visits to sample — either a fixed number or a percentage of total visits

**Step 2 — Preview and confirm:**

- Labs shows how many visits match your criteria before you commit, including a list of matched field workers shown by their **real display names** (not internal ID codes)
- Adjust filters if needed, then click **Create**

**Step 5 — Audit Field Configuration:**

This step appears once you have selected your opportunities. It has two sections:

- **Select the image types to audit** — an auto-detected picker lists the image question types available for the selected opportunity. Each image type is shown by its full question path (for example, `household_visit/child_screening/muac_photo`) so you can confirm exactly which form field you are including. Select one or more types to include in this session.
- **AI reviewer per image type** — when you tick an image type, an AI reviewer dropdown appears directly beneath it. You can select a different reviewer for each image type, or leave the dropdown blank to skip AI review for that type. Each reviewer only appears for the image types it is designed for. This replaces the previous single-reviewer dropdown — each photo type now runs only the reviewer you choose for it.
- **Reviewer settings** — some reviewers require one extra setting, which appears immediately under the reviewer dropdown when that reviewer is selected:
    - **Scale Image Validation** asks you to choose a **Manual Scale Value** field — a dropdown of your opportunity's form fields that tells the reviewer which recorded weight to compare against the scale photo.
    - **MUAC OverZoom** requires no extra settings.
    If a reviewer needs a setting and you leave it blank, the wizard will stop you before creating the session.
- **Context fields** (collapsed by default) — optionally associate any supporting form fields (such as a recorded measurement value) with an image type so that human reviewers can see the relevant data alongside each photo. These associations have no effect on AI review.

!!! tip "Quick-create links"
    If you regularly audit the same image types, you can pre-select them by adding `?image_paths=<full/path1>,<full/path2>` to the audit-creation URL. The picker will open with those types already selected, saving setup time.

**Choosing AI assistance (within Step 5):**

Once you select an image type, an **AI Review Agent** dropdown appears beneath it. Select an agent for that image type if you want AI assistance, or leave it blank to skip AI review for that type. Because each image type has its own dropdown, you can — for example — run **MUAC OverZoom** on MUAC photos and **Scale Image Validation** on weight photos in the same session, with no risk of the wrong reviewer running on the wrong photo type.

When an agent is selected, you will also see per-verdict **"Auto-tag results before I review"** checkboxes — one for each possible verdict the agent can produce. These work the same way as in the review queue:

- **Ticked** — the AI pre-tags matching images with that result before you open the review queue.
- **Unticked** — the AI still badges every image with its classification, but leaves the Pass/Fail decision to you.

The default is **flag-only** (all checkboxes unticked), so nothing is pre-tagged unless you opt in.

!!! tip "Not sure whether to pre-tag?"
    Start with the default flag-only setting. Review a session to see how well the AI's classifications match your program standards, then enable pre-tagging for the verdicts you consistently agree with.

!!! tip "Large audits"
    Creating a session with many visits runs in the background. You'll see a progress indicator — come back in a few minutes for large samples.

---

## Reviewing Images

Once a session is created, open it to start the bulk assessment.

=== "Standard Review"

    Images are shown one at a time alongside the related visit data — FLW name, visit date, and patient name.

    - Mark each image **Pass** or **Fail**
    - Add optional notes
    - Your progress saves automatically

=== "AI-Assisted Review"

    Before you start, click **Run AI Review** to have AI pre-screen all images in the session. AI review processes multiple images at the same time, so a session of around 30 images typically completes in about 2 minutes.

    The AI reviewer assigned to each image type during session creation runs only on images of that type. If you assigned different reviewers to different photo types, each photo is assessed only by the reviewer you chose for it.

    | Agent | When it appears | What it does |
    | --- | --- | --- |
    | **Scale Image Validation** | A weight-related image type is selected and this agent is chosen for it | Compares scale photos against the reading entered by the FLW and flags mismatches |
    | **MUAC OverZoom** | A MUAC image type is selected and this agent is chosen for it | Classifies photos for excessive zoom and flags images the agent identifies as hyperzoomed |

    If no agent is selected for an image type, that type's photos are not pre-screened by AI — the workflow behaves exactly as standard review for those images.

    AI results appear alongside each image as suggestions — you make the final Pass/Fail call. Images flagged by the AI are highlighted so you can prioritize reviewing them first.

    ### Choosing how the AI applies its verdicts

    Next to each AI Review Agent dropdown (in Step 5 of the wizard), each possible AI verdict has a checkbox — for example, "Automatically pre-tag photos flagged as hyperzoomed as Fail" or "Automatically pre-tag readings that match the scale as Pass". You can tick any combination of these:

    - **Ticked** — the AI pre-tags matching images with that result before you open the review queue.
    - **Unticked** — the AI still badges every image with its classification, but leaves the Pass/Fail decision to you.

    The default is **flag-only** (all checkboxes untinted), so nothing is pre-tagged unless you opt in. This means the AI's assessments are always visible, but automated pre-tagging only happens when you have explicitly chosen it.

    Regardless of your checkbox settings, you can always bulk-apply any verdict with one click — for example, **Fail all Hyperzoomed (N)** — directly from the review queue.

    !!! tip "Not sure whether to pre-tag?"
    Start with the default flag-only setting. Review a session to see how well the AI's classifications match your program standards, then enable pre-tagging for the verdicts you consistently agree with.

    **AI classification labels** appear at the bottom of each image tile (below the **Add Note** field) once the AI has reviewed the photo. The label shows the agent name and its classification for that image:

    | Agent | Possible label |
    | --- | --- |
    | **MUAC OverZoom** | "MUAC OverZoom: Hyperzoomed" or "MUAC OverZoom: Not Hyperzoomed" |
    | **Scale Image Validation** | "Scale Validation: Passed" or "Scale Validation: Failed" |

    If the AI encountered a problem reviewing a specific image, the label turns red and shows the error message. Images that have not yet been reviewed by the AI show no label.

    These labels let you see at a glance what the AI classified every image as — not just the ones that were flagged — without relying solely on any pre-tag badge.

    !!! tip "MUAC OverZoom pre-tagging"
    When the MUAC OverZoom agent is used and the pre-tag checkbox for hyperzoomed images is ticked, images it identifies as hyperzoomed arrive in your review queue already marked **Fail** with a red **Hyperzoomed** badge. If the checkbox is unticked, those images are still badged with the AI classification label but appear as normal pending photos for your human review. In both cases, you can confirm each result or override it if you disagree.

**Keyboard shortcuts** (work in both review modes):

| Key | Action         |
| --- | -------------- |
| `P` | Mark Pass      |
| `F` | Mark Fail      |
| `→` | Next image     |
| `←` | Previous image |

### Exporting the Image List

On the Bulk Assessment page, click **Export CSV** to download a spreadsheet of every image in the session. The file includes:

| Column | What it contains |
| --- | --- |
| **Filename** | The name of the image file |
| **Visit date** | The date the visit took place |
| **Visit number** | The visit identifier |
| **Form link** | A direct link to view the full form submission in CommCareHQ |

This is useful when you want to share the image list with colleagues, track review progress in a spreadsheet, or look up the original form submission without searching CommCareHQ manually.

### If an image does not load

The review screen loads images in a controlled stream — a handful at a time — rather than all at once. This prevents request overloads on large sessions and means most photos appear reliably without any action on your part.

If a photo still has trouble loading, the screen retries it automatically a few times. If it cannot load after those retries, the tile shows a clear **"Image failed to load"** message with a **Retry** button. Click **Retry** to attempt loading that photo again — a single click is usually enough to recover from a temporary connection hiccup.

Once a photo has loaded, your browser keeps it cached, so scrolling through the grid or resizing your window will not cause it to reload.

!!! tip "Persistent failures"
    If a photo continues to fail after retrying, check your internet connection and try refreshing the page. If the problem affects many images, contact your program administrator.

---

## Tracking Audit Creation Progress

When you create an audit session that includes an AI reviewer, the work happens in the background. The progress indicator now reflects what is actually happening in real time:

- **The progress bar fills gradually** as the AI works through images. It only turns green and shows as complete when every image has been reviewed — it no longer jumps to full as soon as the AI step begins.
- **The audit list shows a live image count** — for example, "Reviewed 45/136 images (12 passed, 3 failed)" — that updates every couple of seconds while reviewing is in progress.
- **The counter next to the bar** shows the image count during the AI-review step (for example, "45/136") rather than a stage number.

This means you can check the audit list at any point and see exactly how far along the AI review is before you open the session.

!!! tip "Large audits"
    For sessions with many images, the live count gives you a reliable sense of how much longer to wait. You do not need to keep the page open — the job continues in the background and the count will be up to date when you return.

### Program Audit Creator progress

When you run the **Program Audit Creator** to generate audits across multiple opportunities at once, each opportunity row in the list shows its own live progress. Here is what you will see:

- **Activity starts immediately.** As soon as an opportunity's job begins, its row updates straight away — showing "Creating audits · preparing…", then "fetching visits", then "extracting images". There is no silent waiting period at the start.
- **Two clearly labelled steps.** Each row moves through two named steps rather than a generic stage counter:
    - **Step 1 of 2 · Creating audits** — shows live detail such as how many field worker sessions have been created so far.
    - **Step 2 of 2 · AI review** — shows how many images have been reviewed out of the total (for example, "45/136 images reviewed").
- **No top-level progress bar.** The previous bar at the top of the page that stayed empty throughout the run and then jumped to complete has been removed. Each opportunity row has its own bar that fills in real time, and the page header continues to show how many opportunities have finished overall.

You do not need to keep the page open — jobs run in the background and each row's count will be current when you return.

---

## Deleting Audit Sessions

You can delete multiple audit sessions at once directly from the sessions list.

1. On the **Audit** sessions list page, tick the checkbox next to each session you want to delete.
2. A **Delete Selected (N)** button appears next to the Filter button, where **N** is the number of sessions you have checked.
3. Click **Delete Selected (N)** to remove those sessions.

!!! warning "Only in-progress sessions can be deleted"
    If any of the sessions you have selected has a status other than **In Progress**, the delete will be blocked and an error message will explain which sessions cannot be removed. Deselect any completed or otherwise finished sessions and try again.

---

## Session Results

After reviewing all images, click **Complete Session** to record the overall result.

The session list shows:

- Number of images reviewed
- Pass rate for the session
- Session status (In Progress / Complete)
- Link to any tasks created from this session

!!! tip "Creating follow-up tasks"
After completing a session, click **Create Task** next to any flagged visit to open a follow-up task pre-filled with the worker's details. See [Task Management](task-management.md) for how tasks work.

---

## Demoing Audit Without Real Patient Data

Synthetic opportunities include fully populated audit content — MUAC photos, pre-reviewed sessions with pass/fail results, linked follow-up tasks, and OCS coaching transcripts — so you can walk stakeholders or funders through the complete program management loop without using any real patient data.

To access a demo audit session, select a **synthetic opportunity** from the opportunity list (for example, **CHC Nutrition — Northern Cluster (demo)** or **CHC Nutrition — Southern Cluster (demo)**). Audit sessions, tasks, and coaching transcripts within synthetic opportunities are pre-filled with realistic sample data and behave exactly like live sessions, but no real FLW or patient information is involved.

Synthetic audit sessions are built to tell a coherent story out of the box:

- **Audit notes** carry in-story context (for example, "Weekly SOP audit — MUAC photo review for a flagged screening pattern…") rather than any production or recording instructions.
- **Timelines are realistic** — an "Audit Last 7 days" session spans seven separate household visits across seven workdays, each with its own timestamp. Completed sessions show an accurate "Completed on" date, and closed tasks show a closing message that matches the date in the task history.
- **The Program Admin Report grid covers four completed weeks.** Northern Cluster reads **4/4 runs, SOP MET** and Southern Cluster reads **3/4, BELOW**. The report window ends at the current date and slides forward automatically, so the grid stays current-dated without any manual updates.
- **AI coaching transcripts** unfold with varied reply gaps for a natural conversation feel.

!!! note "Synthetic data is read-only for demo purposes"
You can navigate and explore all audit drill-downs in a synthetic opportunity, but changes you make (such as overriding Pass/Fail results) do not affect any real program data.

---

## Common Questions

**Why are some visits missing?**
Visits only appear if they have images attached to the question types you selected. If a FLW didn't upload a photo for that question, their visits won't be included.

**Can I pause and come back?**
Yes — your progress saves automatically. Open the session anytime to continue where you left off.

**What does the AI check for?**
The AI looks at image quality (blur, brightness, framing), whether the measurement shown is within expected ranges, and whether required items are visible. It does not access patient health records — only the images themselves.

**What is the MUAC OverZoom agent?**
When a MUAC image type is selected, you can choose the **MUAC OverZoom** agent from the AI reviewer dropdown that appears beneath that image type. It automatically identifies photos taken with excessive zoom and badges them with its classification. If you have ticked the pre-tag checkbox for hyperzoomed images, those images are also pre-tagged **Fail** with a red **Hyperzoomed** badge before your review begins. If the checkbox is unticked, the badge still shows the AI's classification but no Pass/Fail is applied automatically.
