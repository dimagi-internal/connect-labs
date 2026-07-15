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
    E --> F[Pass / Fail /\nDuplicate/Fake\nper image]
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

**Step 3 — Sampling and filters:**

After setting your date range and sample size, two additional filter sections appear below the sampling configuration:

- **Deliver Unit Type** — a checkbox list of the form names used to submit visits. Tick one or more to include only visits submitted with those forms. Leave all unticked to include all forms.
- **Visit Type** — a checkbox list of visit statuses (for example, Pending, Approved, Rejected, Over Limit). Tick one or more to include only visits with those statuses. Leave all unticked to include all statuses.

Both filters are applied when you click **Update Preview**, so you can see exactly how many visits match before you proceed.

**Step 5 — Audit Field Configuration:**

This step appears once you have selected your opportunities. It has two sections:

- **Select the image types to audit** — an auto-detected picker lists the image question types available for the selected opportunity. Each image type is shown by its full question path (for example, `household_visit/child_screening/muac_photo`) so you can confirm exactly which form field you are including. Select one or more types to include in this session.
- **AI reviewer per image type** — when you tick an image type, an AI reviewer dropdown appears directly beneath it. You can select a different reviewer for each image type, or leave the dropdown blank to skip AI review for that type. Each reviewer only appears for the image types it is designed for. This replaces the previous single-reviewer dropdown — each photo type now runs only the reviewer you choose for it.
- **Reviewer settings** — some reviewers require one extra setting, which appears immediately under the reviewer dropdown when that reviewer is selected:
    - **Scale Image Validation** asks you to choose a **Manual Scale Value** field — a dropdown of your opportunity's form fields that tells the reviewer which recorded weight to compare against the scale photo.
    - **MUAC OverZoom** requires no extra settings.
    If a reviewer needs a setting and you leave it blank, the wizard will stop you before creating the session.
- **Context fields** (collapsed by default) — optionally associate any supporting form fields (such as a recorded measurement value) with an image type so that human reviewers can see the relevant data alongside each photo. These associations have no effect on AI review.
- **Exclude already-audited images** — a checkbox option that controls whether photos previously judged in a completed audit session are included in this new session. Leave it **unchecked** (the default) to include all matching images as normal. **Check it** and the new session will skip any photo that has already received a verdict in an earlier completed audit — so reviewers only assess images that have never been audited before. The number of images skipped is recorded in the creation log.

**Pass Threshold:**

Also on the metadata step of the wizard, you can set a **Pass Threshold** using a slider. The slider ranges from **75% to 100%** and defaults to **100%**.

The threshold controls how the overall audit result is calculated when a reviewer completes the session:

- If the percentage of assessments that passed meets or exceeds the threshold, the audit is marked **Pass**.
- If it falls below the threshold, the audit is marked **Fail**.

The pass percentage is calculated by dividing the number of images marked Pass by the FLW's **total image count** for the session — not just the images assessed so far. This means the percentage shown in the FLW Summary table accurately reflects progress against the full sample at all times.

At the default of 100%, any single failed assessment will fail the entire audit — the same behaviour as before this option was introduced. Lowering the threshold allows a small number of failures without failing the whole audit.

The configured threshold is shown as small italic text — *Pass Threshold : x%* — underneath the FLW Summary table on the review page, so reviewers can see the standard that applies to the session they are working in.

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

The bulk assessment page header identifies the field worker being reviewed as **FLW Name : `<name>`**, using the FLW's real display name so you can always confirm whose images you are looking at.

=== "Standard Review"

    Images are shown one at a time alongside the related visit data — FLW name, visit date, and patient name.

    Each image tile also shows the **entity ID** for the visit — for example, the specific child a home visit was recorded for. This appears below the question tag on the tile, marked with a child icon. The same information is shown next to the question tag when you open an image in the full-screen lightbox view. The entity ID is displayed in full (it wraps to a second line rather than being cut off with "..."), so you always see the complete identifier.

    !!! tip "Older audit sessions"
        Entity IDs are shown for all sessions, including those created before this feature was introduced. The page fetches any missing IDs automatically the first time you open an older session.

    Each image has three assessment options:

    - **Pass** and **Fail** appear side by side as before.
    - **Duplicate/Fake** appears as a full-width button below Pass and Fail (shown in orange with an exclamation icon). Use this when an image appears to be a duplicate submission or a fabricated photo rather than a genuine field visit. The image card border, corner badge, and lightbox all use the same orange treatment when this option is selected.

    If a photo was already given a verdict in an earlier completed audit session, it shows an **Audited** badge on the image tile — for example, **Audited: Passed**, **Audited: Failed**, or **Audited: Dup·Fake**. Hover over the badge to see the date of the earlier audit. This badge only reflects *other* completed audits, not the current session. You can still assess the image normally — the badge is informational only.

    Add optional notes to any image, then move to the next. Your progress saves automatically.

=== "AI-Assisted Review"

    Before you start, click **Run AI Review** to have AI pre-screen all images in the session. AI review processes multiple images at the same time, so a session of around 30 images typically completes in about 2 minutes.

    The AI reviewer assigned to each image type during session creation runs only on images of that type. If you assigned different reviewers to different photo types, each photo is assessed only by the reviewer you chose for it.

    | Agent | When it appears | What it does |
    | --- | --- | --- |
    | **Scale Image Validation** | A weight-related image type is selected and this agent is chosen for it | Compares scale photos against the reading entered by the FLW and flags mismatches |
    | **MUAC OverZoom** | A MUAC image type is selected and this agent is chosen for it | Classifies photos for excessive zoom and flags images the agent identifies as hyperzoomed |

    If no agent is selected for an image type, that type's photos are not pre-screened by AI — the workflow behaves exactly as standard review for those images.

    AI results appear alongside each image as suggestions — you make the final Pass/Fail/Duplicate/Fake call. Images flagged by the AI are highlighted so you can prioritize reviewing them first.

    Each image tile also shows the **entity ID** for the visit — for example, the specific child a home visit was recorded for. This appears below the question tag on the tile, marked with a child icon. The same information is shown next to the question tag when you open an image in the full-screen lightbox view. The entity ID is displayed in full (it wraps to a second line rather than being cut off with "..."), so you always see the complete identifier.

    !!! tip "Older audit sessions"
        Entity IDs are shown for all sessions, including those created before this feature was introduced. The page fetches any missing IDs automatically the first time you open an older session.

    If a photo was already given a verdict in an earlier completed audit session, it shows an **Audited** badge on the image tile — for example, **Audited: Passed**, **Audited: Failed**, or **Audited: Dup·Fake**. Hover over the badge to see the date of the earlier audit. This badge only reflects *other* completed audits, not the current session. You can still assess the image normally — the badge is informational only.

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

### Bulk actions

At the top of the bulk assessment page, several bulk action buttons let you apply results to multiple images at once:

| Button | What it does |
| --- | --- |
| **Pass All Pending** | Marks every image that has not yet been assessed as Pass |
| **Fail All Pending** | Marks every image that has not yet been assessed as Fail |
| **Mark All Duplicate/Fake** | Marks **every currently-visible image** as Duplicate/Fake, including images that already have a Pass or Fail result |
| **Clear All Assessments** | Removes all results from every image in the current view |

!!! warning "Mark All Duplicate/Fake overrides existing results"
    Unlike the Pass/Fail pending buttons, **Mark All Duplicate/Fake** replaces any existing assessment on an image. Use it when you have determined that an entire batch of images from a session is invalid.

**Keyboard shortcuts** (work in both review modes):

| Key | Action         |
| --- | -------------- |
| `P` | Mark Pass      |
| `F` | Mark Fail      |
| `→` | Next image     |
| `←` | Previous image |

### FLW Summary table

The FLW Summary table on the review page shows a row for each field worker in the session. The columns include:

- **% Passed** — the number of images marked Pass divided by the FLW's **total image count** for the session. This gives an accurate picture of overall performance even before all images have been reviewed.
- **Duplicate/Fake** — the count of images marked Duplicate/Fake for that FLW. This column was previously labelled "Incomplete."

The Pass Threshold in effect is shown as small italic text — *Pass Threshold : x%* — beneath the table.

### Completing a Review

When you click **Complete Review**, Labs saves the audit and calculates the overall result. If the same audit session was open in two browser tabs and both tabs submit **Complete Review**, only the first submission is accepted. The second tab will show the message:

> **"This audit has already been saved. Refresh the page to see the updated audit."**

If you see this message, simply refresh the page to view the saved audit result. No action or re-submission is needed.

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

If a photo still has trouble loading, the screen retries it automatically a few times. If it cannot load after those retries, the
