# Deploying the labs formplayer

The ordered steps to stand formplayer up on labs AWS, each a copy-paste command. Steps marked
**write** change live AWS and need the owner's approval; **read** steps are safe to run any time.
Run everything from the repo root of a checkout of `main`, with the `labs` SSO profile for writes
(`aws sso login --profile labs`). What formplayer is and why it is shaped this way:
[README.md](README.md). The stack: `infra/labs-formplayer.yml`.

```bash
export AWS_REGION=us-east-1
arn() { aws secretsmanager describe-secret --profile labs --secret-id "$1" --query ARN --output text; }
NET='awsvpcConfiguration={subnets=[subnet-06646effb09be2f42,subnet-08a18eb47b48aff54],securityGroups=[sg-0666a5ed512c97d9d],assignPublicIp=ENABLED}'
```

## 0. Before you start (read)

- You have the **two OAuth client ids** Connect Web signs in with: the PersonalID public client the
  Android app uses, and the HQ OAuth client for mobile tokens (`connect_labs/connect_web/README.md`).
- **Redis database 9 is empty** (formplayer's; labs itself is database 0, and canopy-web / ace-web
  name theirs only inside their secrets, so check rather than assume):

  ```bash
  gh workflow run run-labs-command.yml --repo dimagi-internal/connect-labs --ref main \
    --field command="shell -c \"import os, redis; print('redis-db9-keys', redis.Redis.from_url(os.environ['REDIS_URL'].rsplit('/', 1)[0] + '/9').dbsize())\""
  # when the run finishes:
  aws logs tail /ecs/labs-jj-web --profile labs --since 15m --filter-pattern '"redis-db9-keys"'
  ```

  Expect `redis-db9-keys 0`. Anything else: pick an empty index and pass `RedisDatabase=<n>` in
  steps 2 and 5.

## 1. Create the secrets (write)

All five are `labs-jj-app-*`, which the task execution role can already read
(`infra/labs-agent-config.yml`). Values are generated or typed, never put on a command line.

```bash
aws secretsmanager create-secret --profile labs --name labs-jj-app-formplayer-labs-auth-key \
  --description "labs formplayer LABS_AUTH_KEY = web CONNECT_WEB_FORMPLAYER_KEY" \
  --secret-string file://<(openssl rand -base64 48 | tr -d '\n')
aws secretsmanager create-secret --profile labs --name labs-jj-app-formplayer-auth-key \
  --description "labs formplayer AUTH_KEY (HQ HMAC key; random, unused by labs)" \
  --secret-string file://<(openssl rand -base64 48 | tr -d '\n')
aws secretsmanager create-secret --profile labs --name labs-jj-app-formplayer-db-password \
  --description "labs formplayer Postgres role password" \
  --secret-string file://<(openssl rand -hex 32 | tr -d '\n')

read -rsp 'PersonalID client id: ' V && aws secretsmanager create-secret --profile labs \
  --name labs-jj-app-connect-web-personalid-client-id --secret-string file://<(printf %s "$V"); unset V
read -rsp 'HQ client id: ' V && aws secretsmanager create-secret --profile labs \
  --name labs-jj-app-connect-web-hq-client-id --secret-string file://<(printf %s "$V"); unset V
```

(`file://<(…)` keeps each value out of the process list and shell history. Run these in bash.)

The sixth is the **worker pool**: ACE's Connect test workers (canopy capability `ace/test-worker`),
as a JSON list `[{"phone", "name", "backup_code", "pin"}, …]`. Their backup codes are sealed on
canopy-web to agent devices, so build the JSON on a machine that can open an `ace/test-worker`
lease and pipe it straight in (`--secret-string file:///dev/stdin`), never via a file or argv:
`labs-jj-app-connect-web-pool`. After the step 7 deploy, load it into labs with
`gh workflow run run-labs-command.yml … --field command="connect_web_import_pool"` (rerun whenever
the secret changes).

## 2. Create the stack, with no task yet (write)

Creates the ECR repo and the deploy role's push grant on it, the security groups, EFS, the
`labs-jj.internal` name, the log group, the roles and both task definitions. `DesiredCount=0`
because no image exists yet.

```bash
aws cloudformation deploy --profile labs --stack-name labs-jj-formplayer \
  --template-file infra/labs-formplayer.yml --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    ImageTag="$(formplayer/build.sh --print-tag)" DesiredCount=0 \
    LabsAuthKeySecretArn="$(arn labs-jj-app-formplayer-labs-auth-key)" \
    HqAuthKeySecretArn="$(arn labs-jj-app-formplayer-auth-key)" \
    DbPasswordSecretArn="$(arn labs-jj-app-formplayer-db-password)"
```

## 3. Create the database (write)

A one-off task (`labs-jj-formplayer-db-setup`, in the stack) that connects with labs' own
`DATABASE_URL` and creates the `formplayer` role and database on the labs RDS instance.
Idempotent; rerunning it only resets the role's password to the secret's value.

```bash
TASK=$(aws ecs run-task --profile labs --cluster labs-jj-cluster --launch-type FARGATE \
  --task-definition labs-jj-formplayer-db-setup --network-configuration "$NET" \
  --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --profile labs --cluster labs-jj-cluster --tasks "$TASK"
aws ecs describe-tasks --profile labs --cluster labs-jj-cluster --tasks "$TASK" \
  --query 'tasks[0].containers[0].exitCode'                                   # expect 0
aws logs tail /ecs/labs-jj-formplayer --profile labs --since 15m --log-stream-name-prefix db-setup
```

The log ends `formplayer role and database are in place`. Formplayer creates its tables itself
(Flyway) on first boot.

## 4. Build and push the image (write: pushes to ECR)

```bash
gh workflow run build-formplayer.yml --repo dimagi-internal/connect-labs --ref main
gh run watch --repo dimagi-internal/connect-labs \
  "$(gh run list --repo dimagi-internal/connect-labs --workflow build-formplayer.yml -L 1 --json databaseId --jq '.[0].databaseId')"
aws ecr describe-images --profile labs --repository-name labs-jj-formplayer \
  --query 'imageDetails[].imageTags' --output text                            # includes the tag from step 2
```

## 5. Start formplayer (write)

```bash
aws cloudformation deploy --profile labs --stack-name labs-jj-formplayer \
  --template-file infra/labs-formplayer.yml --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides ImageTag="$(formplayer/build.sh --print-tag)" DesiredCount=1
aws ecs wait services-stable --profile labs --cluster labs-jj-cluster --services labs-formplayer
```

Parameters not named keep their current values. **This is also the whole procedure for every later
upgrade**: merge a change under `formplayer/`, let `build-formplayer.yml` push the new tag, run this
step. The old task stops before the new one starts (a minute or two without forms); if the new one
never turns healthy, the deployment circuit breaker rolls back to the previous task definition.

## 6. Verify formplayer itself (read)

```bash
TASK=$(aws ecs list-tasks --profile labs --cluster labs-jj-cluster --service-name labs-formplayer \
  --query 'taskArns[0]' --output text)
aws ecs describe-tasks --profile labs --cluster labs-jj-cluster --tasks "$TASK" \
  --query 'tasks[0].{status:lastStatus,health:healthStatus,image:containers[0].image}'   # RUNNING, HEALTHY
aws servicediscovery list-instances --profile labs --service-id "$(aws servicediscovery list-services \
  --profile labs --query "Services[?Name=='formplayer'].Id" --output text)" --query 'Instances[].Attributes'
aws logs tail /ecs/labs-jj-formplayer --profile labs --since 15m --log-stream-name-prefix formplayer
```

`HEALTHY` is formplayer's own `/health` (database and Redis) read from inside the task, on the
management port nothing else can reach.

## 7. Point the web tier at it (write: a PR, then a labs deploy)

```bash
python3 formplayer/pin_web_secrets.py --profile labs-read     # adds 3 secrets[] entries to web.json
git switch -c connect-web-pin-secrets && git commit -am "Connect Web: pin the client ids and formplayer key" \
  && gh pr create --fill                                         # merge when checks pass, then:
gh workflow run deploy-labs.yml --repo dimagi-internal/connect-labs --ref main --field run_migrations=false
```

`CONNECT_WEB_FORMPLAYER_URL` is already in `web.json`. The secrets are a separate PR on purpose:
`deploy-labs.yml` registers `web.json` on every deploy, and a `secrets[]` entry naming a secret
that does not exist yet would stop every new web task from starting.

## 8. Smoke test, from inside the VPC (read)

```bash
gh workflow run run-labs-command.yml --repo dimagi-internal/connect-labs --ref main \
  --field command="connect_web_formplayer_check"
# the run fails if the check fails; the detail is in the web log group:
aws logs tail /ecs/labs-jj-web --profile labs --since 15m --filter-pattern '"formplayer"'
```

The check (`connect_labs/connect_web/management/commands/connect_web_formplayer_check.py`) sends
three requests from a web task: an unsigned one and a wrongly signed one must get a 401, and one
signed with the real key must succeed. That proves the name resolves, the security group admits
the web tier, labs mode is enforcing, both sides hold the same key, and formplayer's Redis and
storage work. A real form (a worker's HQ
token, an app build) is the Connect Web UI's own test once its form screens use formplayer.

## Rollback

| Situation                        | Do                                                                                                                                                                                                               |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A new image misbehaves           | Step 5 with the previous tag: `ImageTag=<old>` (ECR keeps the last 20: `aws ecr describe-images --profile labs --repository-name labs-jj-formplayer --query 'sort_by(imageDetails,&imagePushedAt)[].imageTags'`) |
| Stop formplayer, keep everything | Step 5 with `DesiredCount=0`. Connect Web's forms say the engine is unavailable; nothing else in labs uses it                                                                                                    |
| The web pin breaks the web tier  | Revert the step 7 PR and deploy labs again                                                                                                                                                                       |
| Remove it all                    | Revert step 7 and deploy; `aws cloudformation delete-stack --profile labs --stack-name labs-jj-formplayer`. ECR and EFS are kept (Retain): delete them by hand if wanted. The database stays until dropped       |

**Rotating the shared key:** `put-secret-value` on `labs-jj-app-formplayer-labs-auth-key`, then
`aws ecs update-service --profile labs --cluster labs-jj-cluster --service labs-formplayer --force-new-deployment`
and a labs deploy. Requests fail with 401 until both sides have restarted. **The database password:**
`put-secret-value` on `labs-jj-app-formplayer-db-password`, rerun step 3, then force a new formplayer
deployment.

## What this assumes about labs AWS (checked read-only, 2026-11-25)

- Cluster `labs-jj-cluster`, VPC `vpc-024df4c7bcc30cb01` (DNS support and hostnames on), two
  subnets in us-east-1a/b, **no NAT gateway** — tasks get a public IP to reach the internet, and
  formplayer does too (inbound is still only what its security groups admit).
- `labs-jj-fargate-sg` is the one task security group (web, worker, canopy-web, ace-web, Umami), and
  the RDS and Redis security groups admit exactly it. Formplayer's task carries it for that reason;
  it is also the only source admitted to formplayer's 8080, so any labs task can reach formplayer,
  but only labs' web tier holds the key that makes it act.
- RDS `labs-jj-postgres` (Postgres 18, TLS forced; the JDBC driver negotiates TLS by default) holds
  labs, canopy-web and ace-web databases already; `formplayer` is one more. Labs' `DATABASE_URL`
  user may create roles and databases (labs already manages its `labs_data_reader` role).
- Redis `labs-jj-redis` (7.0, no auth, no TLS).
- No Cloud Map namespace exists yet; `labs-jj.internal` is new.
- connect-labs workflows assume `github-actions-connect-labs-deploy` (CloudTrail), whose ECR push
  grant covers only `labs-jj-commcare-connect`; the stack adds `labs-jj-formplayer`.
- The execution role `labs-jj-ecs-task-execution-role` reads `labs-jj-app-*` and
  `labs-jj-database-url`, and writes `/ecs/labs-jj-*` logs, so it needs no new grant.
