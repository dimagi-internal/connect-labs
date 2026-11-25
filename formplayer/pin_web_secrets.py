#!/usr/bin/env python3
"""Pin Connect Web's three secrets into deploy/task-definitions/web.json (formplayer/DEPLOY.md step 6).

    python3 formplayer/pin_web_secrets.py --profile labs-read     # edits web.json; then PR it

Run it only AFTER the secrets exist (DEPLOY.md step 1). That order is the reason this is a step and
not part of the PR that added the formplayer stack: deploy-labs.yml registers web.json on every
deploy, and a `secrets[]` entry naming a secret that does not exist stops every new web task from
starting (ResourceInitializationError), so pinning early would break the next person's deploy.

It resolves each secret's full ARN (DescribeSecret: metadata only, never the value) and inserts a
`secrets[]` entry, leaving the rest of the file byte-for-byte as it was. Rerunning is a no-op; a
secret already pinned to a different ARN is an error, not a silent overwrite.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

WEB_JSON = Path(__file__).resolve().parents[1] / "deploy" / "task-definitions" / "web.json"

# env var in the web container -> Secrets Manager name (labs-jj-app-*: the execution role reads these)
PINS = {
    "CONNECT_WEB_FORMPLAYER_KEY": "labs-jj-app-formplayer-labs-auth-key",
    "CONNECT_WEB_PERSONALID_CLIENT_ID": "labs-jj-app-connect-web-personalid-client-id",
    "CONNECT_WEB_HQ_CLIENT_ID": "labs-jj-app-connect-web-hq-client-id",
}
ANCHOR = '      "secrets": [\n'


def resolve_arn(name: str, profile: str, region: str) -> str:
    out = subprocess.run(
        ["aws", "secretsmanager", "describe-secret", "--secret-id", name, "--query", "ARN", "--output", "text"]
        + ["--profile", profile, "--region", region],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        sys.exit(f"secret {name} not found or not readable ({out.stderr.strip()}). Create it first: DEPLOY.md step 1.")
    return out.stdout.strip()


def pin(text: str, arns: dict[str, str]) -> str:
    """Return web.json's text with an entry per env var in ``arns`` that is not already pinned."""
    existing = {s["name"]: s["valueFrom"] for s in json.loads(text)["containerDefinitions"][0]["secrets"]}
    entries = []
    for env_name, arn in arns.items():
        if env_name in existing:
            if existing[env_name] != arn:
                sys.exit(f"{env_name} is already pinned to {existing[env_name]}, not {arn}; fix it by hand.")
            continue
        entries.append(f'        {{\n          "name": "{env_name}",\n          "valueFrom": "{arn}"\n        }},\n')
    if not entries:
        return text
    if text.count(ANCHOR) != 1:
        sys.exit(f"expected exactly one {ANCHOR.strip()!r} line in {WEB_JSON}")
    result = text.replace(ANCHOR, ANCHOR + "".join(entries))
    pinned = {s["name"]: s["valueFrom"] for s in json.loads(result)["containerDefinitions"][0]["secrets"]}
    assert all(pinned[k] == v for k, v in arns.items())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default="labs-read", help="AWS profile that may DescribeSecret")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    arns = {env_name: resolve_arn(name, args.profile, args.region) for env_name, name in PINS.items()}
    text = WEB_JSON.read_text()
    result = pin(text, arns)
    if result == text:
        print("web.json already pins all three; nothing to do.")
        return
    WEB_JSON.write_text(result)
    for env_name, arn in arns.items():
        print(f"pinned {env_name} -> {arn}")
    print(f"Now open a PR with {WEB_JSON.relative_to(WEB_JSON.parents[2])}, merge it, and deploy (DEPLOY.md step 6).")


if __name__ == "__main__":
    main()
