# Overture data labs owns: one snapshot per release, switched by a manifest

**Status:** phase 1 built (2026-10-24). The design as built differs from the first draft in three places, each noted where it applies: divisions are not tiled, a failed barrier read raises but grouping still degrades (now logged), and the refresh copies with boto3 rather than `aws s3 sync`.

## The problem

Microplans reads four Overture themes:

- buildings (`footprints.py`)
- admin divisions (`boundaries.py`)
- roads and rail, from the transportation segment theme (`barriers.py`)
- water (`barriers.py`)

Today every one of those reads goes to Overture's public bucket in us-west-2. The
release is pinned in code (`overture.OVERTURE_RELEASE`). Labs keeps its own
copy of buildings only, for 11 countries (`EXTRACT_REGIONS`).

The flaw is that **labs depends on data it does not hold**. Overture deletes old
releases about two months after publishing them, so the pin is a timer. In
October it ran out:

- Every live read failed with `No files found that match the pattern`.
- Barriers swallowed the error, so work-area grouping ran without roads or
  rivers and nobody noticed.
- The fix took two PRs, two deploys and a manual Fargate run.

Automating the pin bump alone would not fix this. It only moves the timer: if
the bump job breaks and nobody repairs it within those two months, labs fails
the same way. A design is sound when a failed refresh makes our data **stale**,
not **broken**.

## Principles

1. **Labs reads only data it holds.** A release labs reads lives in labs' own
   bucket, and only labs deletes it. Overture's bucket is a source we copy
   from, never something a page reads.
2. **One release at a time, across every theme.** Buildings, boundaries and
   barriers always come from the same release, so a ward's geometry and its
   buildings agree.
3. **A release becomes current only after it is checked.** The switch is one
   atomic write, made after the copy and the checks pass.
4. **Failures are loud and safe.** A failed refresh alerts someone and changes
   nothing. A read that cannot find its data raises; it never returns "no
   barriers".
5. **No deploy to change data.** Moving to a new release, or back to the old
   one, is a data change, not a code change.

## Design

Each Overture release becomes a **snapshot** in labs' bucket with two layers.
A **manifest** says which snapshot is current.

```
s3://labs-jj-exports-dev-858923557655-us-east-1-an/overture/
  snapshots/<release>/raw/theme=.../type=.../*.parquet     # byte-for-byte copy of Overture's release
  snapshots/<release>/tiles/<theme>/x=<lon>/y=<lat>/*.parquet  # our 1-degree tiles, coverage area only
  snapshots/<release>/READY.json                           # written last: counts, checks, coverage
  CURRENT.json                                             # {"release": "...", "previous": "..."}
```

### The raw layer: the whole release, every theme

This is an exact copy of Overture's `release/<R>/`. It exists so that:

- labs never depends on Overture keeping a release;
- an area outside our tile coverage still reads data labs holds, from our own
  region;
- tiles are cut from a same-region copy, and can be cut again for a new
  country without going back to Overture.

The copy is the whole release (622 GB, 1,020 files), not only the four themes we
read. Picking themes would be one more thing to keep in step. Using a new theme
(places, addresses) should not need a new copy job.

### The tile layer: what pages actually read

The tile layer covers buildings, roads and water, cut into 1-degree tiles over
the **coverage area**: every country we hold admin boundaries for
(`overture.REGIONS`), growing to all of Africa. This generalises today's building
extract to the two barrier themes. An area is covered when it falls inside a
region whose tiles of that kind `READY.json` lists.

**Divisions are not tiled** (a change from the first draft). Their reads are by
country, name or id rather than by area, so a tile router has nothing to route
on. They are also cached for 30 days, and the whole theme is about 6 GB. They
read the raw snapshot, which is still data labs holds, in our own region.

We keep tiles alongside the raw layer because the raw layer, even in our own
region, is organised for the whole planet. A read scans about 512 planet-wide
files and prunes them by their footer statistics. A tile read opens the two or
three files for the place: 2 to 5 s today, against about 20 s cold for a live
read.

Each theme's tiles keep only the columns its reader uses:

- **buildings:** the existing extract columns, unchanged.
- **segments:** only roads in `ROAD_CLASSES` and rail, with `geometry` and
  `bbox`. The barrier filter is applied at cut time.
- **water:** only `WATER_CLASSES`, with `geometry` and `bbox`.

A feature that crosses tile edges (a long road, a district) is written to every
tile its bbox touches. Readers dedupe by Overture `id`.

### The manifest

`CURRENT.json` holds two releases:

```json
{"release": "2026-11-18.0", "previous": "2026-10-21.0", "switched_at": "...", "switched_by": "overture_refresh"}
```

It is written only by the refresh job (or by hand to roll back), and only to name
a snapshot whose `READY.json` exists. Readers resolve it through
`overture.active_release()`:

- read from S3 and cached in Redis for 10 minutes;
- if S3 cannot be read, the last value stays cached;
- the code constant is the floor when there is nothing cached at all.

Everything that today reads `OVERTURE_RELEASE` reads `active_release()`. That
includes the boundary and footprint **cache keys**, which already hash the
release, so a switch naturally misses the old cache instead of serving the
previous release's geometry. `boundaries.DIVISION_AREA` and
`footprints.OVERTURE_BUILDINGS` are computed at import time today. They become
calls, or a switch would not reach a long-running worker.

### The read path

```
theme read for area A:
  R = active_release()
  if A is inside READY(R).coverage -> tiles/R/<theme>/   (sub-second to a few seconds)
  else                             -> raw/R/             (same region, about 5-20 s, unmeasured)
  no fallback to Overture's bucket at read time.
```

Removing the public-bucket fallback is deliberate. It is what let October fail
in a way nobody saw: an expired release only showed up as slow reads in some
places and missing barriers in others. If data labs is meant to hold is missing,
the read raises with the snapshot path in the message.

`barriers.py` stops catching query errors, and a failed read raises like
`boundaries.py`. The actual October damage was one step further on:
`_query_barriers` returned "no barriers" on error, and `fetch_barriers` cached
that as the area's permanent answer. Raising caches nothing. The callers that
choose to group without barriers when they cannot get them still do (that was
already a product decision), but now log a warning instead of passing silently.
Showing the planner that barriers were skipped is a UI follow-up.

### Refresh: one job, resumable, never urgent

There are two triggers, both into the same job:

- **Automatic:** a Celery beat task runs daily and compares
  `available_releases()` with `CURRENT.json`. When Overture has published a
  newer release, it starts the job.
- **By hand:** `manage.py overture_refresh <release>`.

The job runs as a standalone Fargate task (`labs/ecs_tasks.py`, 4 vCPU /
30 GB). Its steps:

1. **Copy raw.** A boto3 server-side copy (the image has no `aws` CLI; the
   bytes go bucket to bucket and never through the task) of every object not
   already held at the same size. Then check that the object count and total
   bytes match Overture's listing.
2. **Cut tiles** for every theme over the coverage area, from the raw copy. The
   job skips tiles that already exist, so a rerun resumes.
3. **Check.**
   - Every theme has a non-empty tile for each coverage country.
   - Each country's building count, within its fixed sample area, is within
     ±25% of the current release. (October's largest real change was +23%, in
     Freetown.)
   - For a fixed sample box per country (`REGIONS[...]["sample"]`, a city):
     buildings and major roads are non-empty in the candidate's tiles, and the
     country's regions resolve in its raw divisions. These are direct counts
     against the candidate snapshot, not the reader functions, which always
     read the active one.
4. **Mark ready.** Write `snapshots/<R>/READY.json` with the counts, the check
   results and the tile coverage list.
5. **Switch.** Write `CURRENT.json` with `release = R`, and set `previous` to
   the old current release.
6. **Prune.** Delete snapshots that are neither `release` nor `previous`.

Steps 1 to 4 can fail and be rerun as often as needed without touching what
labs reads. If the job fails it:

- emails the labs alarms list (`infra/labs-monitoring.yml` SNS) with the step
  and the error;
- leaves `CURRENT.json` alone.

Labs keeps reading the current snapshot, which it holds, **with no deadline**.

### Seeing that it is working

A daily check alerts when the current release is more than 45 days older than
Overture's newest. That is the one condition that means "refresh is broken",
and it fires long before staleness matters for planning.

The Labs Admin page gets one line: the current release, when it switched, the
previous release, and the newest release Overture has published.

### Rollback and the kill switch

- **Roll back:** set `CURRENT.json` back to `previous`. Its snapshot is still
  held. No deploy.
- **Pause refreshes:** a `paused: true` field in `CURRENT.json` that the daily
  trigger respects.

## What changes for existing code

| Today | After |
|---|---|
| `OVERTURE_RELEASE` constant read everywhere | `active_release()`; the constant is only the cold-start floor |
| `theme_path()` points at Overture's bucket | `theme_path()` points at the snapshot's raw layer |
| `EXTRACT_REGIONS` (11 countries, buildings only, per-region release) | `REGIONS` (bbox + sample) for coverage; which regions are tiled is `READY.json`, for buildings, roads and water |
| `stale_extracts` / `uncut_regions` / `verify_release` | gone: a snapshot is ready or not, all at once |
| `microplans_build_extract` | removed; `overture_refresh --steps cut --kinds buildings` |
| barriers swallow errors | barriers raise |
| `overture/<region>/<release>/` building extracts | moved under `snapshots/<release>/tiles/buildings/`, then the old prefix is deleted |

## Phases

1. **Snapshot of the current release.**
   - Copy `2026-10-21.0` raw.
   - Cut road and water tiles for the 11 countries; the October building extracts,
     already on this release, are copied into the snapshot as its building tiles.
   - Write `READY.json` and `CURRENT.json`.
   - Switch the read path (`active_release()`, the snapshot `theme_path`, the
     tile router for all themes, barriers raise).
   - Measure tile, raw-snapshot and public reads for the same areas, inside and
     outside coverage, and record them in this doc.
2. **`overture_refresh` run by hand** on the next Overture release (expected
   mid to late November). Fix whatever it finds.
3. **Daily trigger, staleness alarm and the Labs Admin line**, once phase 2 has
   run cleanly.
4. **Coverage to all of Africa** for every theme, if phase 1's raw-snapshot
   timings leave planners waiting outside the 11 countries.

## Costs

The first month's bill should confirm one assumption: copying from Overture's
bucket is free because it is AWS Open Data. Steady state is about $30 a month:
two snapshots of about 620 GB each (about $28.50), plus tiles. That is small next to
what October cost to fix. No RDS involvement: DuckDB reads the data in place,
and the only new runtime state is a Redis cache key.
