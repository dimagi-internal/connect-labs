#!/usr/bin/env python3
"""Which hand-deployed labs stacks are running a template older than main?

Every `infra/labs-*.yml` is deployed by hand (`aws cloudformation deploy`, see
infra/README.md); nothing deploys it when its PR merges. So a merged template
fix can sit undeployed indefinitely while everyone reads the PR as "done".
It happened: `labs-jj-monitoring` was last deployed 2026-11-17, and three
merged changes to it (#3144 on 11-19, #3243 on 11-23, #3255 on 11-24) never
reached AWS. The RDS-connections alarm kept paging on its old 300s rule for
days after its fix merged, and nothing said so.

How it decides (exact, not a timestamp guess): it fetches each stack's
deployed template body (`GetTemplate --template-stage Original`, which is the
text `deploy` uploaded) and walks the template's git history on the ref until
it finds the commit whose file is byte-identical (line endings and trailing
whitespace normalised). Then:

  in sync         the deployed body is the ref's file
  BEHIND n        it matches an older commit; the n commits after it are
                  merged but not deployed (listed)
  UNKNOWN         it matches no commit on the ref: deployed from a branch or
                  a hand-edited copy. Read it before deploying over it.
  NOT DEPLOYED    no such stack

Exit 0 when every stack is in sync, 1 when any is BEHIND or UNKNOWN, 2 when
it could not read AWS or git. Read-only: it runs as `labs-observe`, which is
allowed `cloudformation:GetTemplate`/`DescribeStacks` and nothing that
writes; `--profile` or env LABS_PERF_PROFILE overrides.

  python3 tools/infra_drift.py               # every infra/labs-*.yml vs origin/main
  python3 tools/infra_drift.py --ref HEAD    # vs your checkout
  python3 tools/infra_drift.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_PROFILE = os.environ.get("LABS_PERF_PROFILE", "labs-observe")
REGION = "us-east-1"
REPO = Path(__file__).resolve().parents[1]

#: Templates whose stack is not `labs-jj-<name minus the labs- prefix>`.
STACK_OVERRIDES = {
    "labs-observe-readonly.yml": "labs-jj-observe",
}


def stack_for(template: str) -> str:
    """`infra/labs-monitoring.yml` -> `labs-jj-monitoring` (infra/README.md's naming)."""
    name = Path(template).name
    if name in STACK_OVERRIDES:
        return STACK_OVERRIDES[name]
    return "labs-jj-" + name[len("labs-") : -len(".yml")]


_NON_ASCII = re.compile(r"[^\x00-\x7f]")


def normalise(text: str) -> str:
    """Compare what matters: CRLF vs LF and trailing whitespace are not a change.

    Every non-ASCII character also folds to `?`, on BOTH sides. A deploy run from a
    non-UTF-8 shell uploads each one as a literal `?` (CloudFormation stores
    `guardrails ? first slice` for the repo's `guardrails — first slice`), so
    without this a stack deployed from that machine matches no commit at all and
    reads UNKNOWN. That was 10 of 14 labs stacks on 2026-11-28.
    """
    text = _NON_ASCII.sub("?", text.replace("\r\n", "\n"))
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def match_history(deployed: str, history: list[tuple[str, str, str]]) -> dict:
    """Place `deployed` in `history`: (sha, date, subject, file_text) tuples, newest first.

    Returns {"state": in_sync|behind|unknown, "deployed_sha", "undeployed": [...]}.
    """
    want = normalise(deployed)
    for i, (sha, date, subject, text) in enumerate(history):
        if normalise(text) == want:
            if i == 0:
                return {"state": "in_sync", "deployed_sha": sha, "undeployed": []}
            return {
                "state": "behind",
                "deployed_sha": sha,
                "undeployed": [{"sha": s, "date": d, "subject": subj} for s, d, subj, _ in history[:i]],
            }
    return {"state": "unknown", "deployed_sha": None, "undeployed": []}


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def git_history(ref: str, path: str, limit: int) -> list[tuple[str, str, str, str]]:
    log = _run(["git", "-C", str(REPO), "log", ref, f"-{limit}", "--format=%h%x09%cI%x09%s", "--", path])
    out = []
    for line in filter(None, log.splitlines()):
        sha, date, subject = line.split("\t", 2)
        try:
            text = _run(["git", "-C", str(REPO), "show", f"{sha}:{path}"])
        except subprocess.CalledProcessError:
            continue  # the commit deleted or renamed it
        out.append((sha, date[:10], subject, text))
    return out


def deployed_template(stack: str, profile: str) -> str | None:
    """The body `deploy` uploaded, or None when the stack does not exist."""
    proc = subprocess.run(
        [
            "aws",
            "cloudformation",
            "get-template",
            "--profile",
            profile,
            "--region",
            REGION,
            "--stack-name",
            stack,
            "--template-stage",
            "Original",
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        if "does not exist" in proc.stderr:
            return None
        raise RuntimeError(f"get-template {stack}: {proc.stderr.strip()}")
    body = json.loads(proc.stdout)["TemplateBody"]
    # JSON output keeps UTF-8 intact (text output turns an em dash into '?', which
    # would never match). A JSON-format template comes back already parsed.
    return body if isinstance(body, str) else json.dumps(body, indent=2)


def templates(ref: str) -> list[str]:
    names = _run(["git", "-C", str(REPO), "ls-tree", "--name-only", ref, "infra/"]).split()
    return sorted(n for n in names if Path(n).name.startswith("labs-") and n.endswith(".yml"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ref", default="origin/main", help="git ref to compare against")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--history", type=int, default=60, help="how many commits of each template to search (default 60)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        results = []
        for path in templates(args.ref):
            stack = stack_for(path)
            body = deployed_template(stack, args.profile)
            if body is None:
                results.append(
                    {"template": path, "stack": stack, "state": "not_deployed", "deployed_sha": None, "undeployed": []}
                )
                continue
            res = match_history(body, git_history(args.ref, path, args.history))
            results.append({"template": path, "stack": stack, **res})
    except (subprocess.CalledProcessError, RuntimeError, FileNotFoundError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        print(f"infra_drift: could not read: {detail.strip()}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        label = {"in_sync": "in sync", "behind": "BEHIND", "unknown": "UNKNOWN", "not_deployed": "NOT DEPLOYED"}
        for r in results:
            line = f"{label[r['state']]:<12} {r['stack']:<28} {r['template']}"
            if r["state"] == "behind":
                line += f"  (deployed {r['deployed_sha']}, {len(r['undeployed'])} merged since)"
            print(line)
            for u in r["undeployed"]:
                print(f"               {u['date']} {u['sha']} {u['subject']}")
            if r["state"] == "unknown":
                print(
                    "               deployed body matches no commit on "
                    f"{args.ref}: diff it before deploying over it"
                )
    return 1 if any(r["state"] in ("behind", "unknown") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
