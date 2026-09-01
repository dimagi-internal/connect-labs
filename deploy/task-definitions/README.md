# ECS task definitions (version-controlled)

These JSON files are the source of truth for the labs ECS services:

| File          | Service          | Container command                                                    |
| ------------- | ---------------- | -------------------------------------------------------------------- |
| `web.json`    | `labs-jj-web`    | `bash /app/docker/start` (gunicorn ASGI — `config.asgi:application`) |
| `worker.json` | `labs-jj-worker` | `bash /app/docker/start_celery` (celery worker + beat)               |

`deploy-labs.yml` **registers these on every deploy** (`register-task-definition`
from the file) and points the service at that exact revision. So changing CPU,
memory, an env var, the container command, or a secret reference is a normal PR
edit here — applied on the next deploy, reviewable in the diff.

## Are these secret? No — and that's the point

Every real secret is a **reference**, not a value: it lives in the `secrets[]`
block as a `valueFrom` AWS Secrets Manager ARN (e.g. `DATABASE_URL`,
`DJANGO_SECRET_KEY`, the OAuth client secrets, API keys, the GDrive SA key). The
JSON only names the secret and where to find it; the value never touches the repo.

The `environment[]` block holds **non-secret config only** — bucket names,
internal Redis/DB hostnames, URLs, `DJANGO_SETTINGS_MODULE`, `WEB_CONCURRENCY`,
and `MAPBOX_TOKEN` (a public `pk.*` token that already ships in the frontend JS).

**Rule:** never put a secret value in `environment[]`. If you need a new secret,
create it in Secrets Manager and add a `secrets[]` entry pointing at its ARN.

## `WEB_LIMIT_CONCURRENCY` — the web-tier overload valve (ships off)

`web.json` carries `WEB_LIMIT_CONCURRENCY: "0"`, which means **off** — the
pre-existing, unbounded behaviour. Set it to a positive integer and each uvicorn
worker answers 503 once it is at the limit, instead of accepting work the tier
cannot serve. There are `WEB_CONCURRENCY` worker processes, so the tier-wide
ceiling is the product of the two.

It is deliberately kept in the file at `0` rather than omitted, so the knob is
discoverable and enabling it is a one-value diff.

Before picking a number, read the docstring in `config/uvicorn_worker.py`. The
short version: uvicorn trips on `max(open sockets, in-flight requests)`, and
under keep-alive "open sockets" includes idle ones — so this is a blunt overload
valve, **not** a DB-connection governor, and a value tight enough to bound RDS
connections would 503 real users. Context and the open policy decision are in
#1152; the incident that motivated it is #1060.

## `TELEMETRY_SAMPLE_RATE` / `TELEMETRY_SAMPLE_PATH_PREFIX` — the unbiased sample

Request telemetry is otherwise threshold-gated: a line exists only because the
request took ≥ 3 s. That is fine for finding an incident and **invalid for comparing
anything across load levels**, because the gate truncates the distribution
differently at every level — busy periods push ordinary requests just over the floor
and pile them against it. Two opposite wrong conclusions about the same endpoint came
out of that stream in one day (#1386).

These two settings log a fixed fraction of requests _regardless of duration_, which
is a fair draw. They ship scoped to the endpoint under investigation:

|                                |                                                                                              |
| ------------------------------ | -------------------------------------------------------------------------------------------- |
| `TELEMETRY_SAMPLE_RATE`        | `1.0` — every matching request. `0.0` disables the sample entirely (pre-existing behaviour). |
| `TELEMETRY_SAMPLE_PATH_PREFIX` | `/audit/image/` — empty means every path.                                                    |

**Volume, before you widen it.** Measured from ALB access logs, 2026-08-30 → 09-01:
the labs target group serves ~28,000 requests/day, of which `/audit/image/` is ~6,200
— the highest-count single path on the tier. At `1.0` that is ~6,200 JSON lines/day,
about 2 MB/day of CloudWatch ingest. A census is affordable _for one path_; an empty
prefix at `1.0` turns the diagnostic stream into a request log, which is exactly what
the module docstring says it must not become (the ALB already logs URL, status and
latency far more cheaply — what it cannot log is the ms split).

Rate `1.0` rather than a fraction because the scarce population is the busy one: a
15-minute CPU pin contains only ~65 image requests, so at 10% a pinned band yields
about six samples and settles nothing.

Query the sampled population with `filter sampled = 1` — **never**
`filter reason = "sample"`, which drops sampled-and-also-slow requests and
re-introduces the bias in the query.

## Why version-control them

Drift. The container command used to live only in the AWS console. When
`docker/start` was switched to ASGI for the FastMCP server, the registered web
command stayed on the old WSGI gunicorn, and the deploy just reused the in-AWS
task def — so `/mcp/` 404'd in production for everyone until the command was
fixed by hand. Registering from the repo makes that class of silent divergence
impossible: the running services always match what's reviewed here.

## Command points at `/app/docker/*`, not `/start`

The start scripts also exist at the image root (`/start`, `/start_celery`) baked
into the **pre-built base image**, which is built by a separate workflow and can
lag the repo. `COPY . /app` in the app `Dockerfile` runs on every deploy, so
`/app/docker/start` is always the current repo script. Pointing the command there
means a `docker/start` change takes effect on the next deploy without rebuilding
the base image — the script is the single source of truth for how each service
starts.

## Editing

1. Edit the JSON here (or `docker/start` / `docker/start_celery` for the command body).
2. PR + merge to `main`.
3. Deploy (`gh workflow run deploy-labs.yml --ref main`). The register step picks
   up the change.

The `image` stays `:latest`; the deploy builds and pushes `:latest` before the
register step, and the hard-cutover `--force-new-deployment` pulls it.
