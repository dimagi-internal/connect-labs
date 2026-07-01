# Plan: Synthetic Images, Tasks, and OCS Transcripts

**Goal:** Extend the synthetic data system so that a fully synthetic opp can demonstrate the complete program management loop — not just the dashboard, but the audit drill-down (with MUAC photos), task follow-ups, and OCS coaching transcripts.

**Context:** We built `synthetic_profile_from_prod` and `synthetic_generate_from_manifest` in the prior session. The generator produces visits with MUAC values, gender, and health status fields. But audits show broken images (the image endpoint hits prod, where synthetic blob_ids don't exist), tasks don't exist, and OCS transcripts are empty.

---

## Part 1: Synthetic Audit Images

**Problem:** Audit creates a review of sampled visits. Each visit can have photos (MUAC tape on child's arm). The image proxy at `/audit/image/<opp_id>/<blob_id>/` calls `download_image_from_connect(blob_id, opp_id)` which hits Connect prod. Synthetic blob_ids 404.

### 1a. Stock image set in GDrive

Create a folder `stock-images/muac/` under the synthetic GDrive parent with 10-15 stock MUAC measurement photos. These can be:
- AI-generated illustrations of a MUAC tape on a child's arm (no real children)
- Or sourced from existing Dimagi training materials (check with the CHC team)
- Named `muac_001.jpg` through `muac_015.jpg`

The folder ID gets stored as a setting: `LABS_SYNTHETIC_STOCK_IMAGES_FOLDER_ID`.

### 1b. Generator: populate visit `images` arrays

Currently visits have `"images": []`. Change `engine.py` to:

- For visits where `form.case.update.soliciter_muac_cm` is present (i.e., MUAC was measured), add an `images` entry:
  ```json
  {
    "blob_id": "synth-muac-003",
    "name": "muac_photo_<uuid>.jpg"
  }
  ```
- Assign blob_ids round-robin from the stock set (e.g., `synth-muac-001` through `synth-muac-015`)
- Also set the corresponding form_json path for the MUAC photo question (`form.muac_group.muac_display_group_1.muac_photo`) to the filename, so `extract_images_with_question_ids` can match the image to its question

### 1c. Image intercept in serving path

Modify `ExperimentAuditImageConnectView.get()` in `audit/views.py`:

```python
def get(self, request, opp_id, blob_id):
    from connect_labs.labs.synthetic.registry import get_synthetic_opp
    
    synthetic = get_synthetic_opp(opp_id)
    if synthetic and blob_id.startswith("synth-"):
        # Serve from stock images in GDrive
        return self._serve_synthetic_image(synthetic, blob_id)
    
    # ... existing prod path ...
```

The `_serve_synthetic_image` method:
1. Maps `synth-muac-003` → `muac_003.jpg` in the stock images folder
2. Downloads from GDrive via the service account (cached in-process)
3. Returns as `image/jpeg`

Cache strategy: stock images are immutable, so cache in-process with a dict keyed by blob_id. First request hits GDrive, subsequent requests are instant.

### 1d. Manifest extension (optional)

Add an optional `image_config` section to the manifest:
```yaml
image_config:
  muac_photo:
    question_path: form.muac_group.muac_display_group_1.muac_photo
    stock_folder_id: <gdrive-folder-id>  # or use the default
    probability: 0.85  # 85% of MUAC visits have a photo
```

If omitted, the generator uses sensible defaults (photo on every MUAC visit, stock folder from env).

---

## Part 2: Synthetic Tasks

**Problem:** The task management system (`tasks/` app) stores tasks as LabsRecords. Synthetic opps have no tasks, so the task panel in workflows is empty.

### 2a. Generator: produce task LabsRecords

Add a `tasks` section to the manifest:
```yaml
tasks:
  - flw_id: flw_014  # Nuhu D. (struggling)
    title: "Follow up on missed MUAC measurements"
    priority: high
    status: completed
    created_week: 3
  - flw_id: flw_016  # Patience G. (struggling)
    title: "Review flagged visits — possible data quality issue"
    priority: medium
    status: in_progress
    created_week: 4
```

The generator creates LabsRecords with `type=task` via the existing `task_create_synthetic` MCP tool (already built — it's registered in the tools). Wire it into the generation pipeline so tasks are created alongside fixture data.

### 2b. Task timing

Tasks should have realistic timestamps relative to the manifest timeline. A task created in `week: 3` gets `created_at` in the 3rd week of the timeline. Completed tasks get `completed_at` 2-5 days later.

### 2c. Profiler: detect task patterns

Extend `synthetic_profile_from_prod` to also analyze existing LabsRecord tasks for the opp (if any) and include a `tasks` section in the output manifest.

---

## Part 3: Synthetic OCS Transcripts

**Problem:** The OCS coaching system creates chat transcripts between a bot and an FLW. These are stored as task LabsRecords with an embedded `ocs_conversation` array. Without synthetic transcripts, the coaching panel in workflows is empty.

### 3a. Transcript templates

Create 5-8 template coaching conversations covering common scenarios:
- FLW with high flag rate → bot asks about measurement technique
- FLW with missing visits → bot follows up on attendance
- FLW with low approval rate → bot provides refresher guidance
- New hire onboarding check-in
- Positive reinforcement for top performer

Each template is a list of `{role: "bot"|"flw", text: "...", ts: "<relative>"}` messages. The generator fills in FLW-specific details (name, specific metrics) and absolute timestamps.

### 3b. Generator: produce OCS transcripts

Map coaching arcs from the manifest to transcript templates:
```yaml
coaching_arcs:
  - flw_id: flw_014
    week_triggered: 3
    persona: struggling_flagged
    target_behavior: "Improve MUAC measurement technique"
    transcript: []  # auto-generated from template if empty
```

If `transcript` is empty, the generator selects a template based on `persona` and fills it in. If `transcript` is provided, it's used verbatim (for hand-crafted demos).

The generator calls `task_create_synthetic` (already exists) with the `ocs_conversation` parameter to create the LabsRecord.

### 3c. OCS transcript rendering

Verify that the existing task detail panel in workflows renders the `ocs_conversation` correctly when loaded from a synthetic task. The rendering code already exists (it was built for the MBW workflow) — this is just a verification step.

---

## Implementation Order

| Step | Depends on | Effort | Impact |
|------|-----------|--------|--------|
| 1a. Stock images in GDrive | — | Small (source/generate 15 photos) | Prerequisite for 1b-1c |
| 1b. Generator: visit images | 1a | Medium (engine.py changes) | Visits have image refs |
| 1c. Image intercept | 1a | Medium (view + cache) | Audit shows photos |
| 2a. Generator: tasks | — | Small (wire existing MCP tool) | Tasks panel populated |
| 2b. Task timing | 2a | Small | Realistic timestamps |
| 3a. Transcript templates | — | Medium (write 5-8 conversations) | Prerequisite for 3b |
| 3b. Generator: OCS transcripts | 3a, 2a | Medium (template fill + MCP call) | Coaching panel populated |
| 1d. Manifest image_config | 1b | Small | Optional configurability |
| 2c. Profiler: tasks | 2a | Small | Auto-profile tasks |
| 3c. OCS rendering verification | 3b | Small | Confirm it works |

**Recommended order:** 2a → 1a → 1b → 1c → 3a → 3b → verify everything end-to-end.

Tasks (2a) first because it's the smallest and uses an existing MCP tool. Images (1a-1c) next because it's the most visible improvement. OCS transcripts (3a-3b) last because they depend on task infrastructure.

---

## Testing

For each part, the verification loop is:
1. Generate synthetic data with the new features
2. Browse the relevant UI in labs (audit view, task panel, coaching transcript)
3. Screenshot/record video
4. Iterate until it looks right
5. Disable synthetic and verify live data is restored

## Open Questions

- **Stock images:** AI-generated vs sourced from training materials? AI-generated is faster but may look artificial. Training material photos are realistic but need permission clearance.
- **OCS transcript quality:** Should transcripts be AI-generated at manifest time (more natural, unique per FLW) or template-filled (deterministic, reproducible)? Template-filled is simpler and matches the manifest's deterministic-from-seed philosophy.
- **Task cleanup:** When `synthetic_disable` is called, should synthetic tasks/transcripts (LabsRecords) also be deleted? Currently they persist in prod. Need a cleanup step.
