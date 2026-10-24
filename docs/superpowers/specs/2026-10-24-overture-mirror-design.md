# Overture mirror: a copy of every release we read, kept in step without a deploy

**Status:** proposed (2026-10-24). Nothing here is built yet.

## Problem

Microplans reads four Overture themes: buildings (`footprints.py`), admin
divisions (`boundaries.py`), roads (transportation segment) and water
(`barriers.py`). All four come from Overture's public bucket in us-west-2. The
release is pinned in code (`overture.OVERTURE_RELEASE`). Labs keeps a same-region
copy of buildings only, for 11 countries (`EXTRACT_REGIONS`).

That has failed twice:

1. **Overture prunes old releases.** In October the pin `2026-08-19.0` stopped
   resolving. Every live read failed with `No files found that match the
   pattern`. Barriers swallow that error, so work-area grouping ran without
   roads or rivers and nobody saw it. The fix (#2703, #2707) took two PRs, two
   deploys and a manual Fargate run to re-cut the 11 extracts.
2. **Reads from outside the 11 countries are slow.** Labs runs in us-east-1 and
   Overture's bucket is in us-west-2, so every live read is cross-region. A cold
   area outside the extracts takes about 20 s with 8 threads, and 4 to 8 minutes
   on a single thread. Divisions, roads and water have no copy at all.

## Decision

Mirror whole Overture releases into the labs us-east-1 bucket. Keep the tiled
building extracts. Move the pin out of code into a manifest that a scheduled job
advances only after the new release is copied and checked.

### Measured sizes (release 2026-10-21.0, compressed Parquet)

| Theme | Planet | Africa |
|---|---|---|
| buildings | 276 GB | 54.5 GB |
| transportation | 72 GB | 8.3 GB |
| base (water) | 28.8 GB | 1.6 GB |
| divisions | 4.6 GB | 0.4 GB |
| themes labs reads | 381 GB | 65 GB |
| **whole release (all themes)** | **622 GB, 1,020 files** | — |

### Why the whole planet, every theme

- **Cost.** At $0.023/GB-month the whole release is about $14.30 a month, and
  about $29 for the few days two releases overlap during a refresh. The four
  themes labs reads alone would be about $8.80. Mirroring only Africa
  (about $1.50) would need a spatial cut, which is the same work as the extract
  job: a rewrite, not a copy. The mirror would also stop being Overture's layout,
  so `theme_path` could not just change its base.
- **Simplicity.** A byte-for-byte copy with Overture's own layout means one
  `aws s3 sync` and one changed base URL. There is no clipping, nothing to
  re-validate, and no "is this place in our copy?" routing for the raw themes.
  Copying every theme also means a new theme (places, addresses) is already in
  the mirror when someone needs it.
- **Reading the source costs nothing.** Overture's bucket is AWS Open Data, not
  requester-pays, so reading it is free. Labs tasks have public IPs and no NAT,
  so there is no processing charge either. **Check before building:** the copy is
  a cross-region server-side copy (`CopyObject` from us-west-2). The Open Data
  sponsorship should cover the transfer, but confirm it on the first month's
  bill. If it does not, cross-region transfer is about $0.02/GB, so $12 once per
  release, which is still fine.

### Why keep the building extracts

The mirror makes a live building read same-region, but it is still a glob over
about 512 planet-wide files, pruned by footer statistics. The extracts are
1-degree tiles for one country and come back in 2 to 5 s. Building reads happen
on every sample and every work area; boundaries and barriers do not. Keep the
extracts for buildings. Measure the mirror read in phase 1 to decide whether
cutting more countries is still worth doing.

## Design

### Layout

```
s3://labs-jj-exports-dev-858923557655-us-east-1-an/
  overture/<region>/<release>/...           # existing tiled building extracts (unchanged)
  overture-mirror/release/<release>/...     # byte-for-byte copy of Overture's release/<release>/
  overture-mirror/CURRENT.json              # the manifest: what labs reads
```

`CURRENT.json`:

```json
{
  "release": "2026-10-21.0",
  "mirrored_at": "2026-10-24T14:02:00Z",
  "extracts": {"nigeria": "2026-10-21.0", "liberia": "2026-10-21.0", "...": "..."},
  "checks": {"nigeria": {"buildings": 28439, "previous": 28293}, "...": {}}
}
```

### Reading (`microplans/core/overture.py`)

- `active_release()` reads `CURRENT.json`, caches it in Redis for 10 minutes and
  falls back to the code constant `OVERTURE_RELEASE`. The constant stays as the
  floor, so a broken manifest never breaks a read.
- `theme_path(theme, type_, release=None)` returns the mirror base when the
  release is mirrored, and the public bucket otherwise.
- `EXTRACT_REGIONS` keeps the bboxes. The release each region was cut from comes
  from `manifest["extracts"]`. `covering_region`, `stale_extracts` and
  `uncut_regions` keep their meanings.
- `connect()` already scopes the `labs_extract` secret (us-east-1) to the labs
  bucket, so mirror reads pick up the right region and credentials with no
  change. Public reads stay on the global us-west-2 setting.
- `barriers.py` stops swallowing a missing-release error. An expired release
  raises like `boundaries.py` does, so grouping never quietly runs without
  barriers again.

### Refreshing: one job, run monthly

A Celery beat task (`overture_refresh_check`, daily) compares
`available_releases()` with the manifest. When there is a newer release, it
starts one standalone Fargate task (`labs/ecs_tasks.py`, 4 vCPU / 30 GB, like
the October re-cut) that runs `manage.py overture_refresh <release>`:

1. **Copy.** `aws s3 sync s3://overturemaps-us-west-2/release/<R>/
   s3://<bucket>/overture-mirror/release/<R>/ --only-show-errors`. Then check
   that the object count and total bytes match the source listing.
2. **Cut.** `microplans_build_extract <every region> --release <R>`, reading
   from the mirror. That makes it same-region and faster than October's
   8 minutes.
3. **Check.** Read each extract back through `footprints._query_extract` against
   a fixed sample area per country. Compare with the previous release: more than
   25% change in either direction fails the run. (October's largest legitimate
   change was +23%, Freetown.) Also check that one division, one road set and
   one water set resolve from the mirror.
4. **Switch.** Write `CURRENT.json`. This is the only step that changes what
   labs reads, and it runs only after 1 to 3 pass.
5. **Prune.** Delete mirror releases and extract prefixes older than the
   previous one. The previous release is kept as the rollback, and rolling back
   is a manifest edit, not a deploy.

The task is idempotent: `sync` and the per-tile cut skip finished work, so a
rerun after a failure resumes. If it fails, it emails the labs alarms list
(`infra/labs-monitoring.yml` SNS) with the step and the error, and leaves the
manifest alone. Labs keeps reading the current release, which is still
mirrored, so a failed refresh costs nothing until Overture prunes the current
release, about two months later.

### Rollback and kill switch

- Rollback: edit `CURRENT.json` to the previous release (still mirrored).
- Kill switch: deleting `CURRENT.json` drops labs back to the code pin on the
  public bucket, which is today's behaviour.

## Phases

1. **Mirror plus manifest read path.** `active_release()`, mirror-aware
   `theme_path`, barriers fail loudly, and a one-off sync of `2026-10-21.0`.
   Measure live building, division and barrier reads from the mirror against the
   public bucket for an area outside the 11 countries.
2. **`overture_refresh` command plus the Fargate task**, run by hand once on the
   next release (expected late November).
3. **Daily beat check plus alarm email**, after phase 2 has run cleanly by hand.
4. **(If phase 1 shows it is still worth it)** cut building extracts for the
   rest of Africa. That is about 55 GB at our extract density, about
   $1.30 a month.

## Costs, steady state

| Item | Monthly |
|---|---|
| Mirror, one release (622 GB) | ~$14.30 |
| Overlap during refresh (a few days) | ~$1 extra per month, amortised |
| Building extracts (11 countries, 33 GB) | ~$0.76 |
| Refresh compute (one 4 vCPU task, under 1 h) | under $0.30 |
| Reading Overture's bucket | $0 (Open Data; confirm on first bill) |
| **Total** | **~$16** |

No RDS involvement: the data stays in S3 and DuckDB reads it in place. The only
new database state is a Redis cache key.
