#!/usr/bin/env bash
# Instance user-data for the PMC EMOD worker (Ubuntu, amd64, run as root).
#
# Installs Docker, the EMOD runtime image, a pinned emodpy-malaria venv and the EMOD binary, copies the
# worker scripts, installs the canopy idle watchdog for capability "emod", and marks the box ready.
#
# Inputs (environment, set by whatever renders the user-data):
#   WORKER_SRC               directory or s3:// prefix holding run_scenarios.py and pmc_sweep.py (required)
#                            Also holds watchdog/{idle-shutdown.sh,ondemand-idle-shutdown.service,
#                            ondemand-idle-shutdown.timer} (the canopy SDK on-demand assets; the box cannot
#                            fetch the SDK wheel itself). A missing watchdog is fatal: no ready file.
set -euxo pipefail

# The emodpy-malaria commit the sweep and burn-in were validated against on 2026-10-08.
EMODPY_COMMIT=62d9aa6699502d0391c2535c3a510c97fe800e04
EMOD_IMAGE=ghcr.io/emod-hub/emod-ubuntu-runtime:latest
OPT=/opt/emod
CACHE=/var/cache/emod
ACTIVITY_FILE=/var/run/emod/last-activity

: "${WORKER_SRC:?set WORKER_SRC to a directory or s3:// prefix with run_scenarios.py and pmc_sweep.py}"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io git python3-venv python3-pip
systemctl enable --now docker

# EMOD's runtime image is amd64-only.
docker pull --platform linux/amd64 "$EMOD_IMAGE"

mkdir -p "$OPT" "$CACHE"
if [ ! -d "$OPT/emodpy-malaria/.git" ]; then
  git clone https://github.com/EMOD-Hub/emodpy-malaria.git "$OPT/emodpy-malaria"
fi
git -C "$OPT/emodpy-malaria" fetch origin "$EMODPY_COMMIT" || git -C "$OPT/emodpy-malaria" fetch origin
git -C "$OPT/emodpy-malaria" checkout --force "$EMODPY_COMMIT"

python3 -m venv "$OPT/.venv"
"$OPT/.venv/bin/pip" install --upgrade pip
"$OPT/.venv/bin/pip" install -e "$OPT/emodpy-malaria" boto3

# Fetch the EMOD binary and schema that the tutorials' manifest.py points at.
(cd "$OPT/emodpy-malaria/tutorials" && "$OPT/.venv/bin/python" -c '
import pathlib
import manifest
import emod_malaria.bootstrap as dtk
dtk.setup(pathlib.Path(manifest.eradication_path).parent)
')

# Fetch one file from WORKER_SRC (a directory or an s3:// prefix). S3 goes through boto3 in the venv, so the
# box needs no AWS CLI.
fetch() { # fetch <relative-name> <dest>
  case "$WORKER_SRC" in
    s3://*)
      "$OPT/.venv/bin/python" - "${WORKER_SRC%/}/$1" "$2" <<'PY'
import sys
import boto3

bucket, _, key = sys.argv[1][len("s3://"):].partition("/")
boto3.client("s3").download_file(bucket, key, sys.argv[2])
PY
      ;;
    *) cp "$WORKER_SRC/$1" "$2" ;;
  esac
}

fetch run_scenarios.py "$OPT/run_scenarios.py"
fetch pmc_sweep.py "$OPT/pmc_sweep.py"

# Smoke test before declaring ready: imports, binary, one tiny run.
EMOD_CACHE_DIR="$CACHE" EMOD_TUTORIALS_DIR="$OPT/emodpy-malaria/tutorials" EMOD_ACTIVITY_FILE="$ACTIVITY_FILE" \
  "$OPT/.venv/bin/python" "$OPT/run_scenarios.py" --self-test

# Idle watchdog (canopy SDK on-demand assets). Any failure here aborts the script (set -e), so the box never
# reports ready without a way to stop itself.
fetch watchdog/idle-shutdown.sh /usr/local/bin/ondemand-idle-shutdown
fetch watchdog/ondemand-idle-shutdown.service /etc/systemd/system/ondemand-idle-shutdown.service
fetch watchdog/ondemand-idle-shutdown.timer /etc/systemd/system/ondemand-idle-shutdown.timer
chmod 0755 /usr/local/bin/ondemand-idle-shutdown
# The script's own default for CAPABILITY=emod is /var/run/emod/last-activity; it is set explicitly anyway.
cat > /etc/default/ondemand-idle-shutdown <<DEFAULTS
CAPABILITY=emod
ACTIVITY_FILE=$ACTIVITY_FILE
DEFAULTS
systemctl daemon-reload
systemctl enable --now ondemand-idle-shutdown.timer
systemctl is-active --quiet ondemand-idle-shutdown.timer

# /run is tmpfs, so readiness and the activity directory vanish on every reboot. A oneshot unit recreates them.
cat > /etc/systemd/system/emod-ready.service <<'UNIT'
[Unit]
Description=Mark the EMOD worker ready
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'mkdir -p /run/emod && chmod 1777 /run/emod && touch /run/emod/last-activity /run/emod/ready'

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now emod-ready.service
test -e /run/emod/ready
