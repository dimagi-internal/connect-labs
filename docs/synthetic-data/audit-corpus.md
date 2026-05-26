# Synthetic Audit Corpus — MUAC Photos

This is the source-of-truth catalog for the MUAC photo corpus that the
**synthetic generator** uses to populate `AuditSession` records with real
visit images. When you call the synthetic generator with the
`completed_pass_clean`, `completed_fail_tape_usage`, etc. audit archetypes
(see `commcare_connect/labs/synthetic/archetypes.py`), the resulting
audit page on labs renders these actual JPG files via the bulk-assessment
view — not blank placeholders.

## Where the images live

Stock images sit in a shared Google Drive folder identified by the
`LABS_SYNTHETIC_STOCK_IMAGES_FOLDER_ID` setting. The image server
(`commcare_connect/labs/synthetic/image_server.py`) maps blob ids of the
shape `synth-muac-<pool>-<NNN>` onto filenames in that folder:

| Blob id pattern         | Filename pattern        | Purpose                            |
| ----------------------- | ----------------------- | ---------------------------------- |
| `synth-muac-good-NNN`   | `muac_good_NNN.jpg`     | Clean MUAC measurement (passes)    |
| `synth-muac-bad-NNN`    | `muac_bad_NNN.jpg`      | Bad MUAC photo (fails — categorized) |
| `synth-muac-NNN` (legacy) | `muac_NNN.jpg`        | Pre-split uncategorized pool       |

The pool sizes float — the image server silently 404s blob ids that aren't
in the folder. The synthetic generator round-robins through the available
ids per pool.

## Bad-photo categories

The bad pool is categorized in
`commcare_connect/labs/synthetic/generator/muac_reasons.json` (a mirror
of `reasons.json` in the same Drive folder). The source-of-truth document
the categories were derived from is
[Bad MUAC photo rationale](https://docs.google.com/document/d/1MTp7Ogx6ywg8e2Lu6I9BUddAMOWIRGZMCAe5Jv7hb84/edit?tab=t.4ekg38rl0ie2).

| Category       | Photos | Description                                                                       |
| -------------- | ------ | --------------------------------------------------------------------------------- |
| `tape_usage`   | 3      | MUAC tape is improperly applied — overflows the reticle, misaligned, or twisted.  |
| `framing`      | 2      | Photo framing makes the reading impossible to verify — too zoomed or no arrows.   |
| `equipment`    | 3      | The MUAC strip itself is damaged — faded arrows, worn edges, missing tick marks.  |
| `misleading`   | 3      | Photo appears fraudulent — tape on a finger, hand, or surface, not a child's arm. |
| `content_free` | 2      | Image is unusable due to motion blur, focus, or obstruction.                      |

Totals as of writing: **8 good photos, 13 bad photos** in the corpus. The good
pool size is mirrored in
`commcare_connect/labs/synthetic/archetypes.py::_GOOD_POOL_SIZE` — bump it
when you add more good photos.

> **Verify the live corpus** with the `synthetic_image_server_status` MCP
> tool. Its `listing_files` is everything the service account can actually
> see in the stock folder, and is the source of truth for image existence.

## Adding more photos

1. Drop the new file in the Drive folder (`muac_good_NNN.jpg` or
   `muac_bad_NNN.jpg` — keep numbering contiguous).
2. If it's a bad photo, **add an entry to `muac_reasons.json`** with its
   category and a one-line `reason` describing what's wrong with it.
   That mirror is what the synthetic generator reads at archetype-build
   time to pick category-specific photos.
3. Update the table above with the new total.

## How the synthetic generator uses the corpus

The **audit archetypes** in `commcare_connect/labs/synthetic/archetypes.py`
declare an image spec — counts of good/bad/pending photos and an optional
`bad_category` — and the generator's `_pick_blob_ids` deterministically
picks photos from the corpus to attach to each generated `AuditSession`.

Archetype catalog (excerpt — see archetypes.py for the full set):

| Audit archetype             | Photos                  | Story                                            |
| --------------------------- | ----------------------- | ------------------------------------------------ |
| `completed_pass_clean`      | 5 good                  | Auditor reviewed and passed everything.          |
| `completed_fail_tape_usage` | 5 bad (tape_usage primary) | Tape misapplication — coaching task created.   |
| `completed_fail_misleading` | 5 bad (misleading primary) | Fraud suspected — suspension task created.    |
| `completed_mixed_tape_usage`| 3 good + 2 bad          | Mostly clean but some tape issues — warning.    |
| `in_review_partial`         | 1 pass + 1 fail + 3 pending | Audit not yet finished — drives the "⏳ open" cell on the Program Admin Report. |

Because the corpus has only 2-3 photos per bad category, a 5-bad archetype
naturally tops up from other bad categories after exhausting the primary —
this is intentional. The narrative "all bad" reads the same regardless of
which specific failure modes you see.

## Related code

- `commcare_connect/labs/synthetic/archetypes.py` — audit + task archetypes
- `commcare_connect/labs/synthetic/image_server.py` — blob_id ↔ filename resolver
- `commcare_connect/labs/synthetic/generator/images.py` — assigns blob_ids to live synthetic visits
- `commcare_connect/labs/synthetic/generator/muac_reasons.json` — bad-photo catalog
- `commcare_connect/audit/views.py:BulkAssessmentDataView` — reads `visit_images` and renders thumbnails
- `commcare_connect/mcp/tools/program_admin_demo_v2.py` — driver that calls `build_audit_data` per FLW
