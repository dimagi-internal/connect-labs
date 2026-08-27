# Bootstrapping the targeting dataset

Everything needed to recreate this from an empty database — on a new machine, a
different user account, or the deployed environment.

There are two paths, and the fast one is almost always right.

**Restore a snapshot (~2 minutes).** A 45 MB ZIP in Drive holds every boundary
and every indicator value. Restoring it produces a database identical to the one
it was exported from — same counts, same values, same answers — without a single
API call.

**Rebuild from source (30-45 minutes).** Re-fetches everything from the public
APIs. Needed to _make_ a snapshot, or to pick up newly published data. Not
needed to stand up an environment.

Prefer the snapshot. Rebuilding is not just slower: WorldPop enforces an
undocumented daily quota per client, so a second environment re-fetching what a
first already has can exhaust it for both.

---

## Prerequisites, either path

Assumes the repo is checked out and Postgres/PostGIS is running (`inv up`).

### The three things a worktree lacks

`make` targets handle these, but they are worth knowing because each fails in a
way that does not name its real cause:

| missing                                            | symptom                                          |
| -------------------------------------------------- | ------------------------------------------------ |
| the venv (it lives in the main checkout)           | `pytest: command not found`                      |
| `.env` (untracked, so absent in a fresh worktree)  | `ImproperlyConfigured`                           |
| `GDAL_LIBRARY_PATH` / `GEOS_LIBRARY_PATH` on macOS | "Set the GDAL_LIBRARY_PATH environment variable" |

`make manage` and `make test` resolve all three. Use them rather than calling
`python manage.py` directly.

### `.env` needs a Mapbox token

The map renders nothing without it, and the failure is quiet — the page loads,
the table works, the map is an empty box with a note.

```bash
op read "op://Employee/Connect Labs .env/MAPBOX_TOKEN"   # then add to .env:
# MAPBOX_TOKEN=pk.eyJ1...
```

It is a public `pk.` token. If `.env` came from the main checkout it may already
have it — check with `grep MAPBOX_TOKEN .env`.

### Front-end bundles

`connect_labs/static/bundles/` is gitignored, so a fresh checkout has no
Tailwind and every labs page renders as unstyled HTML:

```bash
npm ci
inv build-js
```

### Migrations

```bash
make manage CMD="migrate indicators"
```

---

## Fast path: restore a snapshot

```bash
make manage CMD="targeting_import --from-manifest"
make manage CMD="targeting_status"
```

`--from-manifest` reads the Drive file id pinned in
[`fixtures/snapshot.json`](fixtures/snapshot.json) and needs
`LABS_SYNTHETIC_GDRIVE_SA_KEY` in `.env` — the same service-account key the
synthetic fixtures use. Without it, or without a published snapshot, use a file
directly:

```bash
make manage CMD="targeting_import --path targeting-snapshot-20260827.zip"
```

Import is idempotent and upserts on natural keys, so it is safe to re-run, safe
to interrupt, and safe against a database whose primary keys differ from the
exporter's. Re-running an updated snapshot over an older one corrects the values
in place.

### Publishing a new snapshot

After a load that adds or corrects data:

```bash
make manage CMD="targeting_export --to-drive <folder_id>"
```

Then pin the returned file id, `created_at` and counts in
`fixtures/snapshot.json` **in the same commit**, so the pointer and the data
cannot drift apart.

### What a snapshot is

| member           | holds                                                      |
| ---------------- | ---------------------------------------------------------- |
| `manifest.json`  | counts, licences, SHA-256 per member, coordinate precision |
| `values.csv`     | every indicator value with its full provenance — 0.9 MB    |
| `boundaries.csv` | boundary attributes, indexed into the geometry             |
| `geometry.bin`   | the polygons as concatenated WKB — the other 44 MB         |

Checksums are verified on import; a corrupt or tampered member is refused rather
than half-loaded, and a snapshot from a future schema is refused rather than
silently misread.

Two deliberate properties worth knowing:

- **Coordinates are quantized to 6 decimal places** (~11 cm at the equator,
  against source boundaries digitised nearer 100 m). This halves the file, and
  is the only lossy step — declared in the manifest as `coordinate_precision`.
- **Licences travel on every row.** The manifest lists them and sets
  `contains_non_commercial`. Everything currently in play (geoBoundaries and
  World Bank CC BY 4.0, WorldPop CC BY 4.0, DHS and HAPI open API, IGME
  CC BY 3.0 IGO) permits redistribution. Check that flag before sharing a
  snapshot outside Dimagi.

---

## Slow path: rebuild from source

### The data

One command, correct order, idempotent:

```bash
make manage CMD="bootstrap_targeting --skip-worldpop"
```

**~25–40 minutes**, mostly geoBoundaries downloads. `--skip-worldpop` is
recommended for a first run: WorldPop takes hours and has an undocumented daily
quota (see below). Everything except `pop_u1` comes from elsewhere, and births
fall back to the fertility method, so the surface is fully usable without it.

Then, when you want the last piece — ideally on a fresh day:

```bash
make manage CMD="load_indicators --stage population --source worldpop --missing-only"
make manage CMD="load_indicators --stage births"
```

### Check it

```bash
make manage CMD="targeting_status"
```

Reports boundaries, indicators, per-method country coverage, and — for anything
missing — the stage that produces it. A healthy dataset ends with
`Dataset looks complete.`

Expected on a full load:

```
ADM0 54   ADM1 778   ADM2 1518
~32,000 values across 47 indicators
national_igme 54/55 · subnational_igme 25/55 · subnational_relevelled 41/55
21/21 targetable indicators have data
```

## Run it

```bash
make manage CMD="runserver 8899"
```

Then <http://127.0.0.1:8899/labs/targeting/>. **No login is needed locally** —
the views skip the auth check when `DEBUG` is on, because the page carries only
public open data and the Connect OAuth round trip is unusable on a laptop with
an expired CLI token.

---

## Deployed (labs.connect.dimagi.com)

### What differs from local

- **The page is login-gated.** `OpenLocallyMixin` only relaxes under `DEBUG`.
- **`MAPBOX_TOKEN` must be in the ECS task definition**, not `.env`. Environment
  variables are wiped on deploy unless pinned in `deploy/task-definitions/*.json`.
- **Static bundles are built in the Docker image**, so no `inv build-js` step.

### Deploy, then load

Deploy only from `main` — the workflow refuses any other ref:

```bash
gh workflow run deploy-labs.yml --repo dimagi-internal/connect-labs \
  --ref main --field run_migrations=true
```

Then load the data **inside the running container**, not locally against the
prod database. Restore the snapshot — two minutes, no API calls, and it cannot
spend the WorldPop quota that local development also draws on:

```bash
aws ecs execute-command --profile labs --cluster labs-jj-cluster \
  --task <task-id> --container web --interactive \
  --command "python manage.py targeting_import --from-manifest"
```

`LABS_SYNTHETIC_GDRIVE_SA_KEY` is already in the task definition for the
synthetic fixtures, so no new secret is needed. Only if there is no published
snapshot:

```bash
  --command "python manage.py bootstrap_targeting --skip-worldpop"
```

### Two things that will bite

**Do not deploy while a load is running.** A schema change lands under a process
still holding the old model classes, and its next INSERT fails on a column it
does not know about. That killed a six-hour WorldPop run here. Column defaults
now make it survivable (migration `0007`), but the ordering is still: deploy,
_then_ load.

**The deploy hard-cuts over with no stability wait.** A "successful" workflow
run is not a healthy service, and a long-running MCP or ingest request during
the cutover dies with a `ReadTimeout`.

---

## Where the data comes from

Nothing needs credentials. All are public APIs.

| source                | supplies                                             | notes                               |
| --------------------- | ---------------------------------------------------- | ----------------------------------- |
| geoBoundaries         | ADM0/1/2 boundaries                                  | CC BY 4.0                           |
| DHS Program           | mortality, fertility, and 18 child-health indicators | open API, no key                    |
| UN IGME (UNICEF SDMX) | national series + subnational small-area model       | open                                |
| HDX HAPI              | population by admin unit                             | base64 app identifier, not a secret |
| WorldPop              | age–sex population, the only source of `pop_u1`      | **daily quota**                     |
| World Bank            | national fertility fallback                          | CC BY 4.0                           |

**No IHME.** Its non-commercial agreement excludes for-profit entities and their
employees, and forbids re-hosting. Nobody should register a healthdata.org
account on a dimagi.com address.

### WorldPop's daily quota

Undocumented, and the failure is confusing: requests slow to a crawl, then
return `429 "Your application is sending too many requests per day"` and
everything fails until it resets. **Repeated restarts are what exhaust it** —
each restart re-submits work. The loader now treats a 429 as terminal rather
than retrying, keeps whatever was written, and tells you to resume with
`--missing-only`. Restart a WorldPop ingest sparingly.

---

## Stage order, and why

`bootstrap_targeting` encodes this. It matters because a wrong order produces a
dataset that looks loaded but is quietly short.

```
boundaries      geoBoundaries ADM0/1, plus ADM2 where IGME models that deep
  └ mortality     DHS surveys, IGME national series, IGME subnational model
      └ calibrate     needs BOTH the raw surveys and the IGME series
      └ fertility     DHS TFR + World Bank national fallback
          └ population    HAPI (minutes) then WorldPop (hours)
              └ births        derived from population AND mortality
                  └ child_health   diarrhoea, malaria, nutrition, immunisation,
                                   WASH, households, and the gaps they imply
```

Re-running any stage is safe: values upsert on
`(indicator, boundary, year, source)`, so a re-run repairs rather than
duplicates. `--from-stage <name>` resumes partway.

---

## Recreating on a second machine

With a published snapshot — the normal case:

```bash
grep MAPBOX_TOKEN .env || echo "MAPBOX_TOKEN=$(op read 'op://Employee/Connect Labs .env/MAPBOX_TOKEN')" >> .env
npm ci && inv build-js
make manage CMD="migrate"
make manage CMD="targeting_import --from-manifest"
make manage CMD="targeting_status"
make manage CMD="runserver 8899"
```

A few minutes, nearly all of it `npm ci`.

Without one, substitute the rebuild and add half an hour:

```bash
make manage CMD="bootstrap_targeting --skip-worldpop"
```
