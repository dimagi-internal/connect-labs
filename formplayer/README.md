# Labs formplayer (Connect Web's form engine)

Connect Web (`connect_labs/connect_web/`) runs a worker's Learn and Deliver forms in
[formplayer](https://github.com/dimagi/formplayer), CommCare's form engine (commcare-core in a
Spring Boot service, the engine behind HQ Web Apps). Upstream formplayer only accepts HQ: an HQ
Django session cookie, or a request signed with HQ's key. Labs has neither, so it runs a
**patched** formplayer with a **labs mode** that authenticates requests labs signs, and acts as the
HQ mobile worker whose OAuth bearer token comes with each request.

This directory is the whole fork, and it is not a fork: **upstream pinned at one commit, plus a
short series of patches.** No copy of formplayer lives here, and no repo outside this one is
needed.

| File                 | What it is                                                                                      |
| -------------------- | ----------------------------------------------------------------------------------------------- |
| `UPSTREAM`           | Upstream repo and the full commit sha the patches apply to                                      |
| `patches/*.patch`    | `git format-patch` output, applied in order with `git am`                                       |
| `build.sh`           | Clone at the sha (with the commcare-core submodule), apply, `./gradlew bootJar`, `docker build` |
| `pin_web_secrets.py` | Adds Connect Web's secrets to `deploy/task-definitions/web.json` once they exist                |
| `DEPLOY.md`          | The ordered, copy-paste runbook for the AWS side: secrets, database, stack, image, rollback     |

The infrastructure is `infra/labs-formplayer.yml`; the image build is
`.github/workflows/build-formplayer.yml`; the end-to-end check is
`manage.py connect_web_formplayer_check`.

## The patches

1. **`0001` Labs mode** (off unless `LABS_ENABLED=true`; with it off formplayer is upstream):
   - `LabsAuthFilter`: a request signed with `LABS_AUTH_KEY` (see `0007` for the signing schemes)
     and carrying `X-Labs-HQ-Token` (the worker's HQ access token, scope `sync`) authenticates as
     the body's `username` and `domain`. A bad or missing signature or token, `restoreAs`, or HQ's
     own `X-MAC-DIGEST` alongside gets a 401 and stops there.
   - Every outbound HQ call for that request (restore, submission, case search) sends
     `Authorization: Bearer <token>` — never HQ's HMAC, never `as=`. Submissions always go to
     `/receiver/secure/<build_id>/`, because the non-secure receiver ignores the bearer.
   - `submit-all` accepts `meta_location` and writes it into an empty `meta/location` (formplayer
     ignores `<orx:pollsensor>`, so Web Apps forms otherwise submit no GPS).
   - No session cookie is needed on the routes labs calls. The token is never logged or stored.
   - The Dockerfile's launcher class is fixed for Spring Boot 3.2+ (upstream's image exits with
     `ClassNotFoundException`).
2. **`0002` Redis database index** (`REDIS_DATABASE`, default 0): upstream always used database
   0, which on labs' Redis is the Django cache and Celery broker (a cache clear there is a FLUSHDB).
3. **`0003` No origin token on bearer requests**: HQ validates `X-CommCareHQ-Origin-Token` against
   its own Redis and 403s one it doesn't know; a labs formplayer's tokens live in labs' Redis.
4. **`0004` Route to the worker's HQ server**: `X-Labs-HQ-Host` (one of `LABS_HQ_HOSTS`: www, eu,
   india by default) sends every HQ call for the request to that server.
5. **`0005` Runtime for a local-disk cache**: `dbs/` and `tmp_dbs/` exist in the image, and the
   nightly temp-db purge no longer NPEs when `tmp_dbs/` is missing (it did at 00:00 UTC on a fresh
   disk); the actuator health probes (`/health/liveness`, `/health/readiness`) are on;
   `spring.jpa.open-in-view=false`; explicit Hikari bounds (`DB_POOL_SIZE` 20,
   `DB_CONNECTION_TIMEOUT_MS` 10000); `HQ_READ_TIMEOUT_MS` for outbound HQ calls (default
   upstream's 15 minutes; labs sets 60 s).
6. **`0006` Sentry scrubber**: in labs mode every Sentry event and transaction loses
   `X-Labs-HQ-Token`, `X-Labs-Signature*`, `Authorization`, `X-MAC-DIGEST` and cookies before it
   is sent (upstream sends default PII, and even with PII off Sentry would keep the labs token);
   `SENTRY_SEND_DEFAULT_PII` makes PII configurable (labs: `false`). Sentry is off unless
   `SENTRY_DSN` is set, so this closes a latent leak.
7. **`0007` Signing v2**: `X-Labs-Timestamp` + `X-Labs-Signature-V2` = Base64 HMAC-SHA256 of
   `"v2\n" METHOD "\n" path "\n" ts "\n" sha256hex(token) "\n" X-Labs-HQ-Host-or-"" "\n"`
   followed by the signed bytes (raw body, or the multipart JSON part); the timestamp must be within
   300 s. With v2 present only v2 is checked; without it, v1 (`X-Labs-Signature`, body only) is
   accepted while `LABS_ALLOW_V1=true`. v1 can be replayed to another endpoint, with another token,
   or against another HQ server; v2 cannot. A pinned test vector is in `docs/labs-mode.md`.
8. **`0008` Per-request timing line**: each labs request logs one INFO JSON line on the
   `labs.request` logger: `{"event":"labs_request","endpoint","status","ms","lock_wait_ms",
"installed","restored","domain","sig"}`. No username or token.
9. **`0009` Sandbox per HQ server**: a labs request for a non-default HQ server keeps its user's
   sandbox under `dbs/<domain>@<host>/<user>` and its sync-time key as `<domain>@<host>`, so the
   same domain and username on www and eu never share cases or a sync token. Default-server and
   non-labs requests keep upstream's `dbs/<domain>/<user>`.

The API contract — endpoints, signing, which `app_id` to send (the released build id, not the master
app id), submission results, the log line — is `docs/labs-mode.md` inside the patches; after a
build it is at `formplayer/.build/src/docs/labs-mode.md`.

### Signing v2 rollout (deploy-order safe)

Formplayer first, labs second, then close v1:

1. Deploy formplayer with `0007` (`LabsAllowV1=true`, the default). It accepts v1 and v2, so the
   labs web tier keeps working whatever it sends.
2. Deploy labs sending `X-Labs-Timestamp` + `X-Labs-Signature-V2` (keeping `X-Labs-Signature` is
   harmless: with v2 present it is ignored). `connect_web_formplayer_check` must sign v2 too.
3. Watch the `labs_request` lines: once every one says `"sig":"v2"`, update the stack with
   `LabsAllowV1=false`. From then on a v1-only request gets 401.

Labs deployed before formplayer is also safe as long as it still sends v1 alongside v2: the old
formplayer ignores the unknown headers and checks v1.

## Security model

- **Who may call it.** Only a holder of `LABS_AUTH_KEY` can make formplayer act, and only labs'
  web tier holds it (`CONNECT_WEB_FORMPLAYER_KEY`, the same Secrets Manager secret). Formplayer is
  never public: no load balancer, and its only ingress is 8080 from the labs task security group.
  The management port (8081: `/health`, `/info`, `/metrics`) has no ingress rule at all.
- **What it may do.** Whatever the worker's HQ token allows, and nothing else: formplayer has no
  HQ credential of its own, and every HQ call carries the token labs sent. It trusts the signed
  `username` and `domain` to name the token's user and does not cross-check them — HQ authorizes
  each call by the token, so a mismatch fails at HQ.
- **What it keeps.** Form and menu sessions in its own Postgres database; per-user SQLite
  sandboxes (the worker's restore: their cases) on the task's encrypted ephemeral disk, discarded
  with the task; locks and caches in Redis. All of it is a cache of HQ state — HQ is the system of
  record — and none of it is a credential. Tokens are never written down, and never reach logs or
  Sentry (`0006`, `0008`).
- **Replay.** Signing v2 binds a signature to its endpoint, token, HQ server and a 5-minute
  window; v1 (until `LabsAllowV1=false`) binds only the body.
- **In transit.** Labs to formplayer is plain HTTP inside the VPC, carrying the worker's
  short-lived HQ token (about 15 minutes) in a header — the same posture as labs' own Redis
  traffic. Formplayer to HQ is HTTPS.
- **AUTH_KEY** (HQ's HMAC key) is still a required property upstream. It is set to a random value
  nobody else holds, so the HQ-signed path stays closed.

## Building

```bash
JAVA_HOME=/path/to/jdk-17 formplayer/build.sh --no-docker   # patch + jar: proves the overlay applies and builds
formplayer/build.sh                                         # + image labs-formplayer:<tag>
formplayer/build.sh --print-tag                             # the content-addressed tag, <upstream 12>-<patches 12>
```

The work tree is `formplayer/.build/src` (git-ignored), recreated on every run.

**Upstream's tests** (`--tests`) need a Postgres and these variables, as in upstream's own CI
(`.github/workflows/build.yml` there):

```bash
POSTGRESQL_HOST=localhost POSTGRESQL_DATABASE=<empty db> POSTGRESQL_USERNAME=<user> POSTGRESQL_PASSWROD=<pw> \
COMMCARE_HOST=http://localhost:8000 AUTH_KEY=secretkey REDIS_HOSTNAME=localhost \
  formplayer/build.sh --no-docker --tests
```

`AUTH_KEY=secretkey` is not optional: upstream's HMAC tests hard-code signatures made with it.
At `c083486c` + `0001`–`0009`, 490 pass and 2 are skipped (upstream's own skips). The PR check
(`build-formplayer.yml`, job `test`) runs only the labs classes, against Postgres 18 and Redis,
with `--test` (repeatable, a Gradle test filter):

```bash
formplayer/build.sh --no-docker --test '*LabsAuthFilterTest' --test '*LabsSandboxKeyTest'   # etc.
```

**Postgres 18 and Flyway.** Spring Boot 3.3's managed Flyway (10.10) logs `Flyway upgrade
recommended: PostgreSQL 18.6 is newer than this version of Flyway` on every boot. Formplayer's
migrations are plain DDL and run fine on 18; upgrading Flyway means overriding Boot's managed
version, which belongs in an upstream bump rather than a labs patch. Noted, not changed.

## Bumping upstream

1. Clone upstream and put the series on the new commit:
   ```bash
   git clone https://github.com/dimagi/formplayer.git fp && cd fp
   git checkout <new sha> && git submodule update --init --recursive
   git am ../connect-labs/formplayer/patches/*.patch      # on a conflict: fix, git add, git am --continue
   ```
2. Run upstream's tests with the patches applied (above) and fix what fails.
3. Re-export the series, so the patches are exactly what was tested:
   ```bash
   rm ../connect-labs/formplayer/patches/*.patch
   git format-patch <new sha> -o ../connect-labs/formplayer/patches/
   ```
4. Set `FORMPLAYER_SHA` in `UPSTREAM` to the new full sha, then `formplayer/build.sh --no-docker`.
5. PR it. The PR run of `build-formplayer.yml` builds the image without pushing; the merge pushes
   the new tag, and rolling it out is `DEPLOY.md` step 5.

A new labs change to formplayer is a new patch at the end of the series (commit it on top of the
series in a clone, then re-export as in step 3). Patches that would be useful upstream are worth
offering there: every one that lands is one fewer to carry.

## Running it

| Env var                                                 | Where it comes from (`infra/labs-formplayer.yml`)                  |
| ------------------------------------------------------- | ------------------------------------------------------------------ |
| `LABS_ENABLED=true`                                     | literal                                                            |
| `LABS_AUTH_KEY`                                         | secret `labs-jj-app-formplayer-labs-auth-key` (shared with web)    |
| `AUTH_KEY`                                              | secret `labs-jj-app-formplayer-auth-key` (random; unused by labs)  |
| `COMMCARE_HOST`                                         | `https://www.commcarehq.org`                                       |
| `POSTGRESQL_HOST` / `_PORT` / `_DATABASE` / `_USERNAME` | the labs RDS instance, database and role `formplayer`              |
| `POSTGRESQL_PASSWROD` (sic, upstream's spelling)        | secret `labs-jj-app-formplayer-db-password`                        |
| `REDIS_HOSTNAME`, `REDIS_DATABASE`                      | the labs Redis, database 9 (never 0)                               |
| `SERVER_PORT=8080`, `MANAGEMENT_SERVER_PORT=8081`       | literal                                                            |
| `JAVA_TOOL_OPTIONS`                                     | heap 70% of the task's 4 GB, G1, upstream's other flags            |
| `LABS_ALLOW_V1`                                         | parameter `LabsAllowV1` (default `true`; see the v2 rollout)       |
| `HQ_READ_TIMEOUT_MS=60000`                              | literal (upstream: 15 minutes)                                     |
| `DB_POOL_SIZE=20`, `DB_CONNECTION_TIMEOUT_MS=10000`     | literal                                                            |
| `SENTRY_SEND_DEFAULT_PII=false`                         | literal (Sentry itself is off: no `SENTRY_DSN`)                    |
| `sqlite.dataDir`                                        | default `dbs/`, i.e. `/application/dbs`: the task's ephemeral disk |

The web tier reaches it at `CONNECT_WEB_FORMPLAYER_URL=http://formplayer.labs-jj.internal:8080`
(a Cloud Map name the stack registers) and signs with `CONNECT_WEB_FORMPLAYER_KEY`.

### Storage: a local-disk cache

The SQLite sandboxes (a user's restore, each app install) are a **rebuildable cache on the task's
own Fargate ephemeral storage** (50 GiB, encrypted, gone with the task). They used to be on EFS,
where every SQLite page read was an NFS round trip: profiling put ~85% of a request in EFS I/O
(an `answer` had a ~1.6 s floor for ~0.25 s of CPU). On local disk that I/O is ordinary file
reads.

What that costs: a new task (every deploy or restart) starts empty, so each user's first request
afterwards restores from HQ and reinstalls the app (`"restored":true,"installed":true` on its
`labs_request` line), and later ones are local. Nothing is lost: HQ holds the cases, and form and
menu sessions are in Postgres, so an in-progress form continues on the new task.

The EFS file system is still in the stack (Retain, mount targets and access point intact) but is
**not mounted**: it is reserved for the shared build store below. Its old sandbox files can be
deleted.

### Deploys

Start-before-stop (`MinimumHealthyPercent` 100, `MaximumPercent` 200, circuit breaker with
rollback): the new task must pass its health check before the old one stops, so a deploy has no
outage, and a bad image never replaces a good one. This is safe only because no storage is shared:
for the minute both tasks are in DNS, a user may be served by either, each from its own cache,
which costs at most an extra restore. The Redis sync-time key is shared, so in that window a user
could see the old task's slightly stale cases once; the next sync corrects it.

The container health check is `/health/liveness` (the JVM's own state) on the private
management port. `/health` also checks Postgres and Redis, which are shared labs services: a blip
on either must not make ECS kill the only task. Readiness for DNS is the same check.

### Scaling

**One task.** Formplayer expects all of a user's requests on the same node (HQ routes by username
for that); two steady-state tasks behind a DNS name would split a user's cache between them and
restore far more often. Scaling out needs username-affinity routing in front first; a bigger task
is the easy step before that (the heap follows the task's memory).

## Roadmap: a shared build cache (next phase)

Most of a cold request is not the user's data but the **app**: every user's first request on a
task installs the CCZ into its own app DB and builds the same in-memory platform. The next phase
shares that work across users:

1. **Template app DB per (HQ host, domain, build).** Install each released build once into a
   read-mostly template (the unmounted EFS volume, or S3, is the store); a user's app DB starts as
   a local copy instead of a fresh install.
2. **Pre-warm.** When Connect releases a build, install it before the first worker opens it.
3. **Per-build in-memory platform cache.** Keep the parsed suite and form definitions per build
   in the JVM, shared by every user of that build.
4. **Sharded services.** With app state shared and user state a cache, run several tasks with
   username-affinity routing (consistent hashing in the web tier) instead of one large task.

Each step is a patch here plus a template change; the per-request timing line (`ms`,
`installed`, `restored`) is how to measure it.

## Rollout

Ordered steps, commands and verification: `DEPLOY.md` § 9 (moving to the local-disk cache) and
the signing v2 rollout above.
