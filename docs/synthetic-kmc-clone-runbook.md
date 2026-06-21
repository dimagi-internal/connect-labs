# KMC Synthetic Clone Runbook

Clone the 11 production KMC opportunities into a single labs-only "KMC (Synthetic)" program
using the two-phase profile/generate workflow.

---

## Preferred: local generation + MCP repoint (fast, no prod DB)

Phase 2 is pure compute + GDrive I/O — it does **not** need prod Connect. Run the
heavy generation on a fast local machine and keep all DB writes server-side via the
`connect_labs` MCP. This avoids the slow, timeout-prone server-side generation (a
large cohort can drop the MCP transport mid-run).

Only the **GDrive service-account** creds are needed locally (no prod DB):

```bash
LABS_SYNTHETIC_GDRIVE_SA_KEY=<json-or-path> \
LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID=<folder-id> \
  python manage.py synthetic_generate_opps --spec kmc.yaml --no-register
```

This reads the Phase-1 bundles, regenerates fixtures locally, uploads each to a new
GDrive folder, and prints one `source_opp -> gdrive_folder_id` line per opp. Then,
over the `connect_labs` MCP, per line:

- **Overwrite an existing cloned opp in place** (same labs opp id, matched by
  `cloned_from`): `synthetic_repoint_by_source(source_opportunity_id=<src>, gdrive_folder_id=<folder>)`
- **Register a brand-new opp**: `synthetic_create_labs_only(gdrive_folder_id=<folder>, label=..., program_id=...)`

`synthetic_repoint_by_source` is the server-side half: it does the `cloned_from`
lookup + pointer update with the labs DB, so the generating machine never needs DB
access. Use this for regenerating the existing KMC opps (10012–10022) after a
generator change (e.g. enabling `mirror`).

---

## Quick start (spec-driven — server-side, needs the labs DB)

Describe the cohort once in a YAML spec, then run two commands. Hand the **same file** to
both — Step 1 records the resolved `bundle_root` back into it.

**`kmc.yaml`:**
```yaml
program_id: 10010                       # optional — auto-allocated + written back if omitted
program_name: "KMC (Synthetic)"
org_name: "Dimagi-KMC (Synthetic)"
bundle_root: "gdrive:"                  # Step 1 rewrites this to gdrive:<folder_id>
opportunity_ids: [523, 524, 675, 874, 938, 1234, 1236, 1487, 1488, 1739, 1790]
```

**Run it:**
```
# Step 1 — safe mode (the only manual, prod-touching step): profile -> GDrive
synthetic_clone_profile(spec_yaml=<contents of kmc.yaml>)
#   -> returns the spec with bundle_root resolved; use that for Step 2

# Step 2 — build all 11 opps from the GDrive bundles
synthetic_clone_generate(spec_yaml=<spec returned by Step 1>)
```

Or via management command (the spec file is updated in place):
```bash
python manage.py synthetic_profile_opps  --spec kmc.yaml --base-url https://connect.dimagi.com
python manage.py synthetic_generate_opps --spec kmc.yaml
```

Change the cohort (add an opp, set a different `program_id`) by editing `kmc.yaml` and
re-running. Resume a partial failure or recreate the data by re-running Step 2 (add
`--fresh` to rebuild) — no prod access needed. The sections below document the lower-level
per-opp tools that the spec-driven flow is built on.

---

## KMC opportunity IDs

```
523  524  675  874  938  1234  1236  1487  1488  1739  1790
```

---

## Where bundles live: local vs. GDrive (durable)

The Phase-1 → Phase-2 handoff is a **profile bundle** per opp. Choose where it's stored
with the `out_dir` / `bundle_root` string:

| Value | Backend | When |
|-------|---------|------|
| a path, e.g. `/tmp/kmc-bundles` | local disk | local testing |
| `gdrive:` | a new timestamped Drive run folder | **the labs server run (recommended)** |
| `gdrive:<folder_id>` | an existing Drive run folder | resuming / recreating |

**On the labs server, use `gdrive:`.** Labs runs multiple web containers with ephemeral
disks, so a local `out_dir` written by Phase 1 may not exist on the container that runs
Phase 2. GDrive bundles are durable and container-independent, and they let you **resume a
partial failure or recreate the data without re-touching production** (see below). The
bundle holds only aggregate stats + program config — strictly less sensitive than the
per-visit fixtures already in Drive.

When you pass `gdrive:`, Phase 1 **returns the resolved `bundle_root`** (a
`gdrive:<run_folder_id>`). Copy that value — it's what you pass to Phase 2.

---

## Phase 1 — Profile (safe mode, prod-touching)

Reads real exports from production and writes **aggregate statistics only** — no
row-level beneficiary data persists. Requires a valid Connect OAuth token
with access to each opportunity.

### Via MCP tool (one opp at a time)

```
synthetic_profile_opp(source_opportunity_id=523, out_dir="/tmp/kmc-bundles")
```

Repeat for each of the 11 IDs, or use the bulk variant:

```
# Server run — durable bundles in Drive (note the returned bundle_root):
synthetic_profile_opps_bulk(
    source_opportunity_ids=[523,524,675,874,938,1234,1236,1487,1488,1739,1790],
    out_dir="gdrive:"        # -> returns bundle_root="gdrive:<run_folder_id>"
)
# Local testing instead: out_dir="/tmp/kmc-bundles"
```

### Via management command

```bash
export CONNECT_OAUTH_TOKEN="<your-export-scope-token>"

python manage.py synthetic_profile_opps \
    --opps 523,524,675,874,938,1234,1236,1487,1488,1739,1790 \
    --out /tmp/kmc-bundles \
    --base-url https://connect.dimagi.com
```

### Verify each bundle

Each of the 11 subdirectories (`/tmp/kmc-bundles/<opp_id>/`) must contain:

| File | Contents |
|------|----------|
| `manifest.yaml` | `opportunity_name`, `n_users`, `n_visits`, `fields[]` with `categorical`/`null_rate`, `correlation` matrix, `temporal` (hour-of-day + weekday histograms + weekly volume multipliers), `flag_reasons` distribution |
| `app_structure.json` | Deliver-app form schema from the live HQ app |
| `opportunity.json` | Scrubbed program config (name, currency, dates, budget — no beneficiary data) |

Quick check:

```bash
for id in 523 524 675 874 938 1234 1236 1487 1488 1739 1790; do
    echo -n "opp $id: "
    ls /tmp/kmc-bundles/$id/
done
```

---

## Phase 2 — Generate (full/unsafe mode, offline — no prod calls)

Reads the 11 bundles from disk, generates synthetic fixture data using the copula
engine, uploads to GDrive, and registers all 11 as labs-only opportunities under
one shared "KMC (Synthetic)" program. **Zero production network calls occur.**

### Via MCP tool

```
synthetic_generate_opps_bulk(
    bundle_root="gdrive:<run_folder_id>",   # the value Phase 1 returned (or a local path)
    program_name="KMC (Synthetic)",
    org_name="Dimagi-KMC (Synthetic)"
)
```

The tool allocates a single shared `program_id` for the cohort automatically.
Each opp gets a labs-only ID (`≥ 10_000`). Idempotent by default: passing
`fresh=True` forces regeneration even if a row already exists.

To generate a single opp (e.g. if one bundle failed in Phase 1):

```
synthetic_generate_opp(
    bundle_dir="/tmp/kmc-bundles/523",
    program_id=<shared_program_id>,
    program_name="KMC (Synthetic)",
    org_name="Dimagi-KMC (Synthetic)"
)
```

### Via management command

```bash
python manage.py synthetic_generate_opps \
    --bundles /tmp/kmc-bundles \
    --program "KMC (Synthetic)" \
    --org "Dimagi-KMC (Synthetic)"

# To force regeneration:
python manage.py synthetic_generate_opps \
    --bundles /tmp/kmc-bundles \
    --program "KMC (Synthetic)" \
    --org "Dimagi-KMC (Synthetic)" \
    --fresh
```

---

## Resume / recreate (durable bundles)

Because Phase 2 is idempotent (keyed on `cloned_from_opportunity_id`) and the bundles
persist in Drive, you can recover or rebuild **without re-touching production**:

- **Resume a partial failure** — if Phase 2 errors at opp 6 of 11, just re-run
  `synthetic_generate_opps_bulk(bundle_root="gdrive:<run_folder_id>")` with the same root.
  Already-cloned opps are skipped; the rest finish.
- **Recreate from scratch** — re-run the same call with `fresh=True` to regenerate every
  opp's fixtures from the persisted bundles (e.g. after a wipe, or to re-roll the data).
  No prod access, no re-profiling.
- **Re-profile (only if prod itself changed)** — re-run Phase 1 into a fresh `gdrive:`
  run folder, then point Phase 2 at the new `bundle_root`.

---

## Verification

### Labs picker

Open the labs synthetic UI or use the `synthetic_env_list` MCP tool. Confirm:

- One program named **"KMC (Synthetic)"** is visible.
- It contains exactly **11 opportunities** (one per source opp).
- Each opportunity shows `cloned_from_opportunity_id` set to its source ID.

### Spot-check an opp's export

```
synthetic_local_records_count(opportunity_id=<labs_opp_id>)
```

Then verify the fixture endpoints respond:

- `/export/<labs_opp_id>/app_structure/` — returns non-empty JSON
- `/export/<labs_opp_id>/user_visits/` — returns visit records

### Fidelity check

Run `synthetic_fidelity_report` on a couple of bundles **after** generation:

```
synthetic_fidelity_report(bundle_dir="/tmp/kmc-bundles/523")
synthetic_fidelity_report(bundle_dir="/tmp/kmc-bundles/1234")
```

A healthy report shows:

- **`correlation_frobenius`** — small (< 0.3 for well-correlated fields, near 0 for
  independent fields); large values indicate the copula sampler diverged from the target.
- **`marginal_deltas`** — per-field mean/std and TVD values close to 0; large TVD (> 0.1)
  on a categorical field indicates the frequency distribution shifted.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Phase 1 fails for one opp with "No user_visits" | Opp has no export data in prod | Skip that bundle; the rest succeed |
| Phase 2 `skipped=True` for all opps | Rows already exist and `fresh=False` | Pass `fresh=True` to regenerate |
| `app_structure_present=False` after generate | The opp's HQ app returned empty app_structure in Phase 1 | Re-run Phase 1 for that opp ID; check HQ app is published |
| High correlation Frobenius (> 0.5) | Very small visit count (< 50) means poor rank correlation estimates | Expected for small opps; not a bug |
| GDrive upload error | Service-account credentials not configured on the labs server | Check `GDRIVE_SERVICE_ACCOUNT_KEY` env var |
