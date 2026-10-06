"""Merge a template workflow's published render back into its code template.

Fixes published to a template workflow (data, no deploy) reach that template's
followers and nothing else: every NEW programme is seeded from the code template in
this repo, so it starts without them, and the same defects get found again. This is
the way back. It asks labs for the template's render at a version beside its seed
(the `workflow_template_export` MCP tool -- version 1 is the verbatim seed) and
three-way merges seed -> template into the repo file:

    git merge-file <repo render> <seed> <template version>

so whatever the code template gained since the seed is kept, and only the
template's own changes are applied. Conflicts are left as markers in the file.
Then read the diff: port what is generic, drop what is one programme's (a word, a
heuristic), and open the PR with the version notes this prints.

Usage, from the repo root (needs the labs MCP token: LABS_MCP_TOKEN, or the
connect_labs entry in ~/.claude.json):

    python tools/promote_template_workflow.py --template 7379 --program 10097
    python tools/promote_template_workflow.py --template 7385 --opportunity 10097 --version 6
    python tools/promote_template_workflow.py --from-json export.json   # a saved tool result

Two templates that share one code render (the programme and opportunity reports)
are merged one after the other: run it twice; the second merge keeps the first.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def fetch(args) -> dict:
    import httpx

    sys.path.insert(0, str(REPO))
    from scripts.walkthroughs._mcp_client import call, session, token

    params = {"template_workflow_id": args.template, "include_code": True, "base": args.base, "max_diff_lines": 0}
    params["program_id" if args.program else "opportunity_id"] = args.program or args.opportunity
    if args.version:
        params["version"] = args.version
    if args.draft:
        params["draft"] = True
    with httpx.Client(timeout=120) as c:
        result, is_error = call(c, session(c, token()), "workflow_template_export", params)
    if is_error or not isinstance(result, dict) or "code" not in result:
        sys.exit(f"workflow_template_export failed: {json.dumps(result)[:600]}")
    return result


def merge(export: dict, *, dry_run: bool = False) -> int:
    path = (export.get("code_template") or {}).get("path")
    if not path:
        sys.exit(
            f"template type {export.get('template_type')!r} has no render file in this checkout "
            "(an inline render, or a template this branch does not have)"
        )
    target = REPO / path
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base.js"
        tip = Path(tmp) / "template.js"
        base.write_text(export["base_code"])
        tip.write_text(export["code"])
        if dry_run:
            work = Path(tmp) / "repo.js"
            work.write_text(target.read_text())
        else:
            work = target
        label = f"template {export['template_workflow_id']} {export['target']}"
        proc = subprocess.run(
            [
                "git",
                "merge-file",
                "-L",
                "repo",
                "-L",
                f"seed v{export['base']['version']}",
                "-L",
                label,
                str(work),
                str(base),
                str(tip),
            ],
            capture_output=True,
            text=True,
        )
    conflicts = proc.returncode
    if conflicts < 0:
        sys.exit(proc.stderr)
    print(
        f"{export['name']} ({export['template_type']}): {export['target']} against seed v{export['base']['version']}"
    )
    print(f"  into {path}{' (dry run, file untouched)' if dry_run else ''}")
    if (export.get("code_template") or {}).get("deployed_equals_base") is False:
        print("  the code template has changed since the seed: its changes are kept by the merge")
    print(f"  {conflicts} conflict(s)" + (" -- resolve the <<<<<<< markers" if conflicts else ""))
    print("\nVersion notes (for the PR body):")
    for c in export.get("changes") or []:
        restored = f" (restores v{c['restores_version']})" if c.get("restores_version") else ""
        print(f"  v{c['version']}{restored}: {c.get('note')}")
    return conflicts


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--template", type=int, help="the template workflow id")
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--program", type=int, help="its home program")
    scope.add_argument("--opportunity", type=int, help="its home opportunity")
    p.add_argument("--version", type=int, help="default: the published version")
    p.add_argument("--base", type=int, default=1, help="default 1: the seed")
    p.add_argument("--draft", action="store_true", help="merge the draft instead (write access)")
    p.add_argument("--from-json", type=Path, help="a saved workflow_template_export result (include_code=true)")
    p.add_argument("--dry-run", action="store_true", help="report the merge without writing the file")
    args = p.parse_args(argv)
    if args.from_json:
        export = json.loads(args.from_json.read_text())
    else:
        if not args.template or not (args.program or args.opportunity):
            p.error("--template and one of --program / --opportunity, or --from-json")
        export = fetch(args)
    return 1 if merge(export, dry_run=args.dry_run) else 0


if __name__ == "__main__":
    sys.exit(main())
