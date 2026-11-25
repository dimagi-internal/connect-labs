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
   - `LabsAuthFilter`: a request carrying `X-Labs-Signature` (standard padded Base64 of
     HMAC-SHA256 of the exact raw body, keyed with `LABS_AUTH_KEY`; for multipart, of the
     `application/json` part) and `X-Labs-HQ-Token` (the worker's HQ access token, scope `sync`)
     authenticates as the body's `username` and `domain`. A bad or missing signature or token,
     `restoreAs`, or HQ's own `X-MAC-DIGEST` alongside gets a 401 and stops there.
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

The API contract — endpoints, which `app_id` to send (the released build id, not the master app
id), submission results — is `docs/labs-mode.md` inside patch 0001; after a build it is at
`formplayer/.build/src/docs/labs-mode.md`.

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
  sandboxes (the worker's restore: their cases) on an encrypted EFS volume; locks and caches in
  Redis. All of it is a cache of HQ state — HQ is the system of record — and none of it is a
  credential. Tokens are never written down.
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
At `c083486c` + `0001`–`0002`, all 454 tests pass.

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

| Env var                                                 | Where it comes from (`infra/labs-formplayer.yml`)                 |
| ------------------------------------------------------- | ----------------------------------------------------------------- |
| `LABS_ENABLED=true`                                     | literal                                                           |
| `LABS_AUTH_KEY`                                         | secret `labs-jj-app-formplayer-labs-auth-key` (shared with web)   |
| `AUTH_KEY`                                              | secret `labs-jj-app-formplayer-auth-key` (random; unused by labs) |
| `COMMCARE_HOST`                                         | `https://www.commcarehq.org`                                      |
| `POSTGRESQL_HOST` / `_PORT` / `_DATABASE` / `_USERNAME` | the labs RDS instance, database and role `formplayer`             |
| `POSTGRESQL_PASSWROD` (sic, upstream's spelling)        | secret `labs-jj-app-formplayer-db-password`                       |
| `REDIS_HOSTNAME`, `REDIS_DATABASE`                      | the labs Redis, database 9 (never 0)                              |
| `SERVER_PORT=8080`, `MANAGEMENT_SERVER_PORT=8081`       | literal                                                           |
| `JAVA_TOOL_OPTIONS`                                     | heap 70% of the task's 4 GB, G1, upstream's other flags           |
| `sqlite.dataDir`                                        | default `dbs/`, i.e. `/application/dbs`: the EFS mount            |

The web tier reaches it at `CONNECT_WEB_FORMPLAYER_URL=http://formplayer.labs-jj.internal:8080`
(a Cloud Map name the stack registers) and signs with `CONNECT_WEB_FORMPLAYER_KEY`.

### Scaling

**One task.** Formplayer keeps a user's SQLite sandbox on local disk and expects all of that
user's requests on the same node; HQ routes by username to get that. Two tasks behind a DNS name
would split a user's state between them, and a rolling deploy would put two JVMs on the same SQLite
files on EFS — so the service is stop-then-start and `DesiredCount` is 0 or 1. Scaling out needs
username-affinity routing in front (and per-task data dirs) first; a bigger task is the easy step
before that (the heap follows the task's memory).
