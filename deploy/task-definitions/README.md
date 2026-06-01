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
