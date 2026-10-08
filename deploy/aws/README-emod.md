# labs-emod: the on-demand EMOD runner

The PMC live model (`/labs/targeting/pmc/`) runs IDM's EMOD malaria model on a dedicated EC2 instance that
is **stopped while idle** and started by the labs Celery worker when a request needs it. The instance,
its IAM, its artifacts bucket and its idle alarm are one CloudFormation stack, `labs-emod`, built from the
canopy SDK's on-demand capability template (`canopy_sdk.ondemand`, dimagi-canopy 0.7.0).

| File                                    | What                                                                    |
| --------------------------------------- | ----------------------------------------------------------------------- |
| `labs-emod.cfn.yaml`                    | the stack: a copy of the SDK template with seven marked local edits     |
| `labs-emod-userdata.sh`                 | first-boot user data; its base64 is the template's `UserData` default   |
| `../../tools/pmc_emod/worker/`          | the worker: `bootstrap.sh` (installer), `run_scenarios.py` (the runner) |
| `../task-definitions/{web,worker}.json` | `LABS_EMOD_INSTANCE_ID`, `LABS_EMOD_BUCKET`, `LABS_EMOD_REGION`         |

The consumer side is `canopy_sdk.ondemand.OnDemandInstance(LABS_EMOD_INSTANCE_ID, LABS_EMOD_REGION, "emod")`:
`ensure_running()` starts the box and waits for `/run/emod/ready`, `run([...])` runs a command over SSM,
`stop()` stops it. Nothing ever terminates it. Idle shutdown is three layers: the consumer's `stop()`, the
in-VM watchdog (halts after an hour without a touch of `/var/run/emod/last-activity`), and a CloudWatch alarm
that stops it after 5 minutes of max CPU below 5%.

## The template is a copy, not an import

CloudFormation cannot read a template out of a Python wheel, so `labs-emod.cfn.yaml` is the SDK's
`ondemand-capability.cfn.yaml` copied verbatim, with the emod values as parameter defaults and these edits,
each marked `labs-emod edit N` in the file. **Re-copy on an SDK bump and re-apply them.**

1. **Defaults.** `Capability=emod`, `OwnerTag=labs-emod`, `InstanceType=c7i.4xlarge` (16 vCPU, native x86;
   EMOD's runtime image is amd64-only), `ConsumerTaskRoleName=labs-jj-ecs-task-role` (the web and worker
   task role), `RootVolumeGb=60`, `UserData` = base64 of `labs-emod-userdata.sh`. `AmiId`, `VpcId` and
   `SubnetId` have no default and are passed at deploy time.
2. **Outbound TCP 80** on the security group. The SDK template assumes a baked AMI; this box installs at
   first boot, and Ubuntu's EC2 apt mirrors are plain http.
3. **`s3:GetObject` for the instance role** on its bucket. The SDK grants put only; the worker also reads
   its own scripts (`worker/`), each request (`requests/`) and cached burn-ins (`burnin/`).
4. **`InstanceMetadataTags: enabled`** on the launch template and 5. an **`artifacts-bucket` tag** on the
   instance: how the user data finds the bucket (see below).
5. **`s3:PutObject` on `requests/*` for the consumer.** The SDK grants the consumer read on the bucket
   only; the Celery task uploads each request there for the worker to read.
6. **Launch-template version.** The SDK pins the instance to `"$Latest"`, which CloudFormation rejects
   (`does not support using $Latest or $Default for LaunchTemplate version`), so the stack never creates.
   This copy uses `LatestVersionNumber`. Consequence: any launch-template change replaces the instance
   (see Rolling).

## First boot: how the box finds its own bucket

`bootstrap.sh` needs `WORKER_SRC`, an `s3://` prefix holding `run_scenarios.py`, `pmc_sweep.py` and the
watchdog files. They live in the stack's own artifacts bucket under `worker/`. But that bucket is created
by the same stack that launches the instance, under a generated name, so the name cannot be baked into the
user data. So:

- the stack tags the instance `artifacts-bucket=<bucket>` and exposes instance tags to IMDS;
- `labs-emod-userdata.sh` reads the tag from IMDS, waits (up to an hour per boot) for
  `s3://<bucket>/worker/bootstrap.sh`, and runs it with `WORKER_SRC=s3://<bucket>/worker`;
- it runs from `/var/lib/cloud/scripts/per-boot/`, so if the box is stopped mid-install (the idle alarm
  fires after 5 quiet minutes, e.g. while it waits for the upload) the next start retries. After one
  success it writes `/var/lib/emod/bootstrap.done` and becomes a no-op;
- `bootstrap.sh` installs `emod-ready.service`, which recreates `/run/emod/ready` and the activity marker
  on every boot (`/run` is tmpfs), so readiness survives stop/start and reboot.

Install log on the box: `/var/log/emod-bootstrap.log`. Measured 2026-10-08 on c7i.4xlarge: first boot
to ready about 2.5 min (apt, Docker image pull, emodpy-malaria venv, EMOD binary, a self-test run); a later
cold start (stopped to ready) about 2 min, nearly all of it EC2 start to status-ok (119-124 s). That is close
to the SDK 0.7.0 default `boot_timeout_s=180`, so consumers pass `boot_timeout_s=600`.

Timing gate (6 schedules x 1 seed, pop 5000, 2 intervention years): cold (burn-in built) 188.5 s, of which
burn-in 81 s; warm (burn-in cached) 106.5 s.

Bucket objects expire after 7 days (SDK lifecycle rule), `worker/` included. That only matters if the box
has to install again (a replacement instance, or a first boot that never finished); re-upload first.

## Deploy

From the repo root, with the `labs` AWS profile (needs `CAPABILITY_NAMED_IAM`). Take the subnet and VPC
from the labs default VPC (the subnet needs outbound 443 and 80; a public subnet with auto-assigned public
IPs does):

```bash
export AWS_PROFILE=labs AWS_REGION=us-east-1
AMI=$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query Parameter.Value --output text)
aws cloudformation validate-template --template-body file://deploy/aws/labs-emod.cfn.yaml
aws cloudformation deploy --stack-name labs-emod --template-file deploy/aws/labs-emod.cfn.yaml \
  --capabilities CAPABILITY_NAMED_IAM --tags owner=labs-emod \
  --parameter-overrides AmiId=$AMI SubnetId=<subnet-id> VpcId=<vpc-id>
```

Then stage `WORKER_SRC` (the instance is already booting and waiting for it; doing this while the deploy
runs, as soon as the bucket exists, saves a few minutes):

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name labs-emod \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactsBucketName'].OutputValue" --output text)
ASSETS=$(python -c 'from canopy_sdk.ondemand.assets import asset_path; print(asset_path("idle-shutdown.sh").parent)')
aws s3 cp tools/pmc_emod/worker/bootstrap.sh       s3://$BUCKET/worker/bootstrap.sh
aws s3 cp tools/pmc_emod/worker/run_scenarios.py   s3://$BUCKET/worker/run_scenarios.py
aws s3 cp tools/pmc_emod/pmc_sweep.py              s3://$BUCKET/worker/pmc_sweep.py
for f in idle-shutdown.sh ondemand-idle-shutdown.service ondemand-idle-shutdown.timer; do
  aws s3 cp "$ASSETS/$f" s3://$BUCKET/worker/watchdog/$f
done
```

Smoke test (any machine with the SDK's `ondemand` extra and labs credentials). Note the worker's Python
is the venv at `/opt/emod/.venv`, not the system `python3`:

```python
from canopy_sdk.ondemand import OnDemandInstance
i = OnDemandInstance("<InstanceId>", "us-east-1", "emod")
print(i.ensure_running(boot_timeout_s=600, ready_timeout_s=1800))   # first boot runs the install
print(i.run(["/opt/emod/.venv/bin/python /opt/emod/run_scenarios.py --self-test"]).stdout)  # OK (..s)
i.stop()
```

Finally put the `InstanceId` and `ArtifactsBucketName` outputs into `LABS_EMOD_INSTANCE_ID` and
`LABS_EMOD_BUCKET` in both task definitions and deploy labs.

## Rolling

- **New worker code** (`run_scenarios.py`, `pmc_sweep.py`): no rebuild. Upload the file to
  `s3://$BUCKET/worker/` and copy it into place on a running box:
  `i.run(["/opt/emod/.venv/bin/python -c \"import boto3; boto3.client('s3').download_file('$BUCKET', 'worker/run_scenarios.py', '/opt/emod/run_scenarios.py')\""])`.
  If the change alters what a burn-in contains, bump `BURNIN_VERSION` in `run_scenarios.py` so stale
  cached burn-ins are not reused.
- **New EMOD/emodpy pin or installer change** (`bootstrap.sh`): bootstrap is idempotent. Upload the
  `worker/` files, then on a running box clear the done marker and re-run the per-boot installer, which
  downloads `bootstrap.sh` again and runs it (it also re-fetches the worker scripts):
  `i.run(["rm -f /var/lib/emod/bootstrap.done && /var/lib/cloud/scripts/per-boot/50-emod-bootstrap.sh"], timeout_s=3600)`.
  The log is `/var/log/emod-bootstrap.log`.
- **Template change.** IAM, security group, alarm and policy changes update in place: re-run the deploy.
  A **launch-template change** (instance type, AMI, user data, volume size) **replaces the instance**
  (edit 7): CloudFormation launches a new instance with a new `InstanceId`, which installs itself from
  `worker/` (re-upload it if older than 7 days), and the old instance is retained, not terminated. Then:
  update `LABS_EMOD_INSTANCE_ID` in both task definitions and deploy labs; stop the old instance; once the
  new one has served a request, terminate the old one by hand. The bucket, and so `LABS_EMOD_BUCKET`, does
  not change.

## Teardown

`aws cloudformation delete-stack --stack-name labs-emod` removes only the launch template, the alarm and
the consumer policy. The instance, the bucket, the security group (`ondemand-emod`), the instance role and
instance profile (`ondemand-emod-instance`) are `Retain`, so the runner keeps working (and stays stoppable)
after a stack delete. Removing it for good is by hand, in this order: terminate the instance; delete the
instance profile (after removing the role from it), the role (after deleting its inline `artifacts-put`
policy and detaching `AmazonSSMManagedInstanceCore`) and the security group; empty and delete the bucket.
The fixed names (`ondemand-emod`, `ondemand-emod-instance`) collide with a new stack until they are gone.
The same cleanup applies after a failed create (`ROLLBACK_COMPLETE`): delete the stack, then those retained
resources, then redeploy.
