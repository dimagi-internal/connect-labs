#!/bin/bash
# User data for the labs-emod runner: base64 of this file is labs-emod.cfn.yaml's UserData default (kept
# in step by tools/pmc_emod/worker/test_deploy_userdata.py; CloudFormation caps a parameter at 4096 bytes,
# so keep it short). Finds the stack's artifacts bucket from the instance's `artifacts-bucket` tag (IMDS),
# waits for s3://<bucket>/worker/bootstrap.sh, and runs it with WORKER_SRC=s3://<bucket>/worker. Installed
# as a per-boot script so a box stopped mid-install retries on its next start; a no-op once it succeeded.
# Why and how: deploy/aws/README-emod.md.
set -euo pipefail

install -d /var/lib/cloud/scripts/per-boot
cat > /var/lib/cloud/scripts/per-boot/50-emod-bootstrap.sh <<'PERBOOT'
#!/bin/bash
set -euo pipefail
DONE=/var/lib/emod/bootstrap.done
[ -e "$DONE" ] && exit 0
exec >> /var/log/emod-bootstrap.log 2>&1
echo "[$(date -u +%FT%TZ)] emod bootstrap: starting"

imds() {
  local token
  token=$(curl -sSf -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')
  curl -sSf -H "X-aws-ec2-metadata-token: $token" "http://169.254.169.254/latest/meta-data/$1"
}
BUCKET=$(imds tags/instance/artifacts-bucket)
AWS_DEFAULT_REGION=$(imds placement/region)
export AWS_DEFAULT_REGION
echo "bucket=$BUCKET region=$AWS_DEFAULT_REGION"

# boto3 for the wait/download below (bootstrap.sh builds its own venv later).
export DEBIAN_FRONTEND=noninteractive
python3 -c 'import boto3' 2>/dev/null || { apt-get update && apt-get install -y python3-boto3; }

WORK=$(mktemp -d /var/tmp/emod-bootstrap-XXXXXX)
python3 - "$BUCKET" "$WORK/bootstrap.sh" <<'PY'
import sys
import time

import boto3
from botocore.exceptions import ClientError

bucket, dest = sys.argv[1:]
s3 = boto3.client("s3")
deadline = time.time() + 3600
while True:
    try:
        s3.download_file(bucket, "worker/bootstrap.sh", dest)
        break
    except ClientError as exc:
        if time.time() > deadline:
            raise SystemExit(f"s3://{bucket}/worker/bootstrap.sh never appeared: {exc}")
        print(f"waiting for s3://{bucket}/worker/bootstrap.sh ({exc.response['Error']['Code']})", flush=True)
        time.sleep(15)
PY

WORKER_SRC="s3://$BUCKET/worker" bash "$WORK/bootstrap.sh"
mkdir -p "$(dirname "$DONE")"
date -u +%FT%TZ > "$DONE"
rm -rf "$WORK"
echo "[$(date -u +%FT%TZ)] emod bootstrap: done"
PERBOOT
chmod 0755 /var/lib/cloud/scripts/per-boot/50-emod-bootstrap.sh
exec /var/lib/cloud/scripts/per-boot/50-emod-bootstrap.sh
