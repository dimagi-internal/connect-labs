# Synthetic Opportunities — Operator Guide

> **Status (2026-07-25 doc-regen):** covers the GDrive-fixture *read* path only. The 'no writes are intercepted' claim predates labs-only opps (id ≥ 10,000), whose LabsRecord reads AND writes are served locally by `labs/synthetic/local_records_backend.py` — see CLAUDE.md § Synthetic / labs-only opportunities.

Labs can serve fake `/export/opportunity/<id>/...` data for opportunities
registered as "synthetic". Use this for demos, grant prototyping, and
visualization iteration before real FLW data is collected.

## How it works (one paragraph)

Every call that would otherwise hit Connect's export API goes through
`get_export_client(opp_id, access_token)`. If the opp is registered in
`/labs/synthetic/`, the factory returns a `SyntheticExportClient` that reads
fixture JSON from the opp's Google Drive folder. Writes (`LabsRecord` updates,
audit reviews, workflow state changes) are unaffected — they still land in
prod. Clean up by deleting the demo opp's `LabsRecord`s manually.

## One-time setup

1. Obtain the `LABS_SYNTHETIC_GDRIVE_SA_KEY` env var value (service account JSON
   from 1Password item `connect-labs GCP service account key (connect-labs-sa)`).
   Confirm it is set in the target environment.
2. Obtain a Drive folder to host synthetic opps ("labs-synthetic parent") and
   share it with the service account email as **Editor**.
3. Set `LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID` env var to that folder's ID.

## Creating a synthetic opp

Synthetic data is never real. A synthetic opp is made in two steps: **profile** a
real opportunity into a saved statistical profile, then **generate** a synthetic
data set from that profile into a labs-only opp. Real rows are read only while
profiling, on the server; they never land in a folder a synthetic opp serves.

### Start here: one call

`synthetic_clone_opp(source_opportunity_ids=[...])` clones one or more real
opportunities in one background job. It profiles each one on the server and
generates a labs-only clone with case timelines (the default). The clones are
filed under a new program and made visible in your labs lists. It returns a
`task_id`; follow it with `synthetic_job_status(task_id)`, which says in plain
words where the job is ("Step 1 of 2: measuring the real opportunities") and
returns the new opportunity ids when it is done. You need access to each source
opportunity.

It is the only cloning tool on the safe MCP address (`/mcp/no_user_visit/`). The
step-by-step tools below stay on the full address.

### Where the work runs, and limits

**All synthetic work runs on the background worker, never in the web request.** That
covers profiling, generation, cloning and fidelity scoring. A generation tool called
over MCP still returns its result as before: it queues a job and waits for it, up to
8 minutes. Pass `wait=false` to get a `task_id` at once.

**Limits:**
- At most 2 synthetic jobs run at once across the whole system (`SYNTHETIC_JOB_SLOTS`).
  The rest wait their turn on the queue, so audits and AI reviews keep the worker's
  other slots.
- Each person may have 2 profiling jobs in flight and profile 40 opportunities a day.
  On the safe address, one call may profile at most 10 opportunities.
- An identical request returns the job already running.

### The flow, step by step: profile on the server, then generate

Both steps are `connect_labs` MCP tools, so they run inside labs with your own
Connect access.

1. **Profile.** `synthetic_profile_opp(source_opportunity_id, out_dir="gdrive:")`
   (or `synthetic_profile_opps_bulk` / `synthetic_clone_profile` for several opps)
   queues a job that reads the opportunity's exports with your token and writes a
   profile bundle (`manifest.yaml`, the app structure and the scrubbed opportunity
   detail) to Drive. It returns a `task_id`; poll `synthetic_profile_status(task_id)`
   until it reports the `bundle_dir`. Only you can poll your own job. Profiling a
   large opportunity takes several minutes.
2. **Generate.** `synthetic_generate_opp(bundle_dir, program_id)` (or
   `synthetic_generate_opps_bulk` / `synthetic_clone_generate`) generates the
   fixtures from the profile, uploads them to a new
   `opp-<id>-<timestamp>-generated` folder, and registers a labs-only opp that you
   own. It makes no production calls.

What a profile holds: per-field distributions (including the real minimum,
maximum and spread, which is expected), FLW personas, timing, and, with
`case_timelines=true`, a pool of **modelled cases** so a clone follows cases over
time the way the source does. Every worker keeps its number of cases and their
lengths, and each case's growth, visit spacing, per-case constants (birth weight,
date of birth) and outcomes are drawn from models fitted to the real cases. Outcomes
are tied to the case's growth, so a slow grower dies as often as in the source. No
real case is in the pool:

- The real cases are used only to fit the models, at profile time.
- A categorical answer seen in fewer than 5 cases is never modelled, nor is a field
  recorded in fewer than 5 cases.
- Start dates are smoothed.
- A sampled case that lands on a real one is redrawn.

The code is `labs/synthetic/generator/fixtures/case_model.py`. `mirror=true` is the
old name for the same switch. Profiles saved before 2026-10 in mirror mode held each
real case lightly perturbed; data generated from one never counts as generated, so
re-profile them. A select whose answers
are free text or identifiers (more than 50 distinct values, or values shaped like
ids) is treated as free text and none of its values are copied.

`synthetic_generate_from_manifest` generates from a manifest you write by hand, for
an opp that has no real source at all.

### Power user: dump → profile → generate (full access only)

Server-side profiling is slow for very large opportunities, so there is a second
route **for people with full production access only**: dump the real exports to
Drive, profile the dump, and generate from the profile.

- **A dump is only ever an input to profiling.** It is a copy of real production
  rows, including personal data. Never serve it: do not point a synthetic opp at
  a dump folder, and never share one.
- An opp that does point at a dump folder (or at any folder you name yourself,
  through `/labs/synthetic/`, `synthetic_register`, `synthetic_create_labs_only`
  or `synthetic_repoint_by_source`) is **never "generated"**, whatever its label
  says.

To dump: pick the opportunity in the labs context selector, open `/labs/synthetic/`,
click "+ New synthetic opp", choose "Dump real data from Connect (profiling input
only)" and click **Start dump**. The folder it creates is
`opp-<id>-<timestamp>` (no `-generated` suffix). Profile it into a bundle with

```bash
python manage.py synthetic_profile_dump --folder <dump_folder_id> --out gdrive: --case-timelines \
    --base-url https://connect.dimagi.com   # optional: fetches the app structure a dump lacks
```

then generate from the bundle it prints, exactly as above. The bundle is the same
kind a server-side profile writes (aggregates and a pool of modelled cases); the
dump folder stays where it is, unserved.

### Provenance: which opps hold generated data

"Labs-only" and "synthetic" say where data is served from, not whether it is real.
`SyntheticOpportunity.generated_folder_id` records the folder the generator wrote
for an opp, and an opp counts as generated only while it is labs-only, enabled
and still serving that exact folder (`labs/synthetic/provenance.py`). Only
generation code sets it: the generate tools, the env ensurer and the demo seeders.
Pointing an opp at any other folder un-marks it automatically, and a clone
(`synthetic_clone_to_labs_only`) is generated only when its source is.

Opps generated before provenance existed, or generated on a laptop
(`synthetic_generate_opps --no-register`) and then repointed, are unmarked. Run
`python manage.py synthetic_mark_generated` to list them and `--apply` to mark
those whose Drive folder has the generator's `-generated` name.

## Updating fixtures

Edit the JSON files in Drive, then either:

- click **"Reload fixtures"** on that opp's row in `/labs/synthetic/`, or
- call the **`synthetic_reload_fixtures(opportunity_id)`** MCP tool.

Either one clears everything derived from the fixtures. Re-registering the opp
(`synthetic_register`, `synthetic_repoint_by_source`) and regenerating
(`synthetic_generate_from_manifest`) do this for you.

### Why a reload is needed at all

Four independently-keyed caches sit between the Drive folder and a rendered
dashboard, and **none of them is keyed on fixture content** — so replacing file
bytes at a stable folder id is invisible to all of them:

| Cache | Key |
| --- | --- |
| Registry — is this opp synthetic, which folder | `opportunity_id` |
| FixtureStore — parsed fixture JSON | `(opp_id, folder_id, endpoint_key)` |
| RawVisitCache | `(opportunity_id, pipeline_id)` |
| Computed visit / FLW / entity rows | `(opportunity_id, config_hash, …)` |

Two consequences worth knowing before you go hunting:

- **Minting a fresh pipeline does not escape the computed caches.** They key on
  `config_hash`, so two pipelines with identical schemas share rows — a new
  pipeline reads the old one's results.
- **`pipeline_preview` reporting `"from_cache": false` does not mean a dashboard
  will show fresh data.** It reads a different layer.

Before this was wired up, the only reliable way to make a regeneration visible
was an undocumented three-part incantation — new folder id *and* new pipeline
*and* changed schema content — and every intermediate state rendered cleanly
while serving superseded numbers (#1034). A confident, wrong dashboard is the
failure mode this section exists to prevent.

## Limitations

- Image endpoints (`/export/opportunity/<id>/image/`) still hit prod. Image
  IDs in synthetic data will typically 404 and render as broken images in
  audit/KMC/RUTF views. Not a blocker for the dashboards you're likely demoing.
- Pagination, `last_id` cursors, and `?images=true`-style filters are ignored —
  fixtures are returned whole in one page.
- No writes are intercepted. If a reviewer flags a synthetic visit, that
  `LabsRecord` goes to prod. Delete the demo opp's records from prod when done.
