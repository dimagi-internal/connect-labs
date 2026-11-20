# Synthetic Programmes Runbook (KMC and others)

> **The short version.** A synthetic programme is built once and then **regenerated**,
> never hand-assembled. Both are one action, run as the person who asks (labs reads the
> real opportunities with their Connect login; nobody hands an agent a token):
>
> | To | On a page | Over MCP |
> |----|-----------|----------|
> | **build** a new programme | `/labs/synthetic/clone/` -- tick opportunities, or *Start from a template* | `synthetic_clone_opp(source_opportunity_ids=[...])` or `synthetic_clone_opp(template="kmc")` |
> | **regenerate** an existing one in place | `/labs/synthetic/` -- *Regenerate* on the programme | `synthetic_programme_regenerate(program_id=10121)` |
> | **follow** the job | the job page the button opens (`/labs/synthetic/clone/<task_id>/`) | `synthetic_job_status(task_id=...)` -- the starter, or anyone who can use the programme |
> | **change what it makes** | *Definition* on the programme (its settings page) | `labs_config_set(namespace="synthetic", scope_type="program", ...)`, or `synthetic_programme_regenerate(program_id=..., definition_yaml=... / template=...)` |
>
> An agent asked to regenerate a programme whose sources it cannot read hands the person
> the page and follows the job; it does not ask for their credentials.

## Where a programme's definition lives

| What | Where | Why there |
|------|-------|-----------|
| **The definition** -- sources, names, `curate`, case timelines, invented names, demo photo cases | labs DB: the programme's `synthetic` settings (`labs/synthetic/definitions.py`, `scope_config`) | belongs to ONE programme; edited by its members and Dimagi staff; every change versioned, attributed and undoable; the Regenerate button and the MCP tool read the same row |
| **The measurements** (profile bundles) | Google Drive, a new folder per generation | large; written by step 1, read by step 2; aggregate statistics only |
| **Templates** | `connect_labs/labs/synthetic/cohorts/<name>.yaml` in the repo | reviewed, tested starting points for a FIRST build; a programme copies one when it is built and owns its copy from then on |

So "which definition file does a regeneration use?" has one answer: **the programme's
own stored definition.** A template is only read when you ask for it by name (to build a
new programme, or to put an existing one back to the template).

Every build records what it did on the programme (`last_generation`: when, by whom, the
job, the opportunities it made, any that failed), which the *Synthetic programmes* list
on `/labs/synthetic/` shows.

## Regenerating the KMC demo programme (10121)

Click **Regenerate** on *KMC (Synthetic) Oct 2026* at `/labs/synthetic/` (or
`synthetic_programme_regenerate(program_id=10121)`). It measures today's twelve real KMC
opportunities and rebuilds 10122-10133 **in place**: their ids, and every report and
link built on them, stay the same. Takes up to about 20 minutes.

What the definition decides, so nothing is hand-set afterwards:

- **Which copy is rewritten.** Each source has a clone in 10011 and in 10121;
  regenerating 10121 rewrites the one in 10121.
- **The photo cases** (`image_config.showcase`): one per coaching situation (growing
  well, weight stalled, a weighing to check, a danger sign not referred) plus the two
  photo-audit stories, with a photo at every weighing. `showcase_last_visit_on: end`
  puts each case's last weighing on its opportunity's last day of data, so after a
  regeneration they are as recent as the data. Their case ids are stable per
  (opportunity, case name), so a link to one survives a regeneration.
  `labs/synthetic/tests/test_showcase_coaching_states.py` checks each case reaches
  its coaching situation through the real pipeline paths and registry (it reads the
  `kmc` template).

After a regeneration, run the reports on the latest data (a new run of each), then
re-save the programme report. Numbers move with every regeneration, because it measures
the real opportunities as they are that day.

---

## Lower level: the two steps by hand

`synthetic_clone_opp` and `synthetic_programme_regenerate` are these two steps in one job.
Run them by hand only to debug a step, or to re-run step 2 from bundles already in Drive
without reading production again:

```
synthetic_clone_profile(spec_yaml=<a cohort spec>)                       # step 1: measure -> Drive
synthetic_clone_generate(spec_yaml=<spec step 1 returned>, fresh=true)  # step 2: build
```

A cohort spec is a definition plus `program_id` (where to build; omit to allocate a new
programme) and `bundle_root` (`gdrive:` for a new Drive folder; step 1 returns the
resolved `gdrive:<folder_id>`). The management commands take the same spec as a file:

```bash
python manage.py synthetic_profile_opps  --spec kmc.yaml --base-url https://connect.dimagi.com
python manage.py synthetic_generate_opps --spec kmc.yaml
```

Neither saves a definition on the programme: a programme built this way has no
*Regenerate* until it is given one (`synthetic_programme_regenerate(program_id=...,
template=...)`, which saves it and regenerates).

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

## Fallback: generate on a laptop, repoint over MCP

Phase 2 needs only the GDrive service account, not the labs DB. If the worker is
unavailable, generate locally and repoint server-side:

```bash
LABS_SYNTHETIC_GDRIVE_SA_KEY=<json-or-path> \
LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID=<folder-id> \
  python manage.py synthetic_generate_opps --spec kmc.yaml --no-register
```

It prints one `source_opp -> gdrive_folder_id` line per opp. For each, call
`synthetic_repoint_by_source(source_opportunity_id=<src>, gdrive_folder_id=<folder>)`
to overwrite the existing clone in place (matched by `cloned_from`), or
`synthetic_create_labs_only(...)` for a new one. An opp repointed this way is **not**
marked generated until `python manage.py synthetic_mark_generated --apply` runs
(see `docs/SYNTHETIC_OPPS.md`, "Provenance").

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
| GDrive upload error | Service-account credentials not configured on the labs server | Check the `LABS_SYNTHETIC_GDRIVE_SA_KEY` env var |
