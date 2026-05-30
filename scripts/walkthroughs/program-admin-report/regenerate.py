"""Regenerate the Program Admin Report demo from demo_config.json.

Loads ``demo_config.json``, invokes the synthetic data generator
(``commcare_connect.labs.synthetic.program_admin_demo``), and persists
the resulting run ids to ``.run_ids.json`` so the recorder scripts can
pick them up without copy-pasting integers between terminals.

Usage::

    # From a connect-labs checkout, with the labs venv active:
    python scripts/walkthroughs/program-admin-report/regenerate.py

    # Or via the MCP from Claude:
    #   read demo_config.json
    #   pass its contents to mcp__connect_labs__program_admin_demo_seed

Writes ``scripts/walkthroughs/program-admin-report/.run_ids.json``
with these keys (consumed by the recorders + capture_walkthrough):

    par_run_id        — the cross-opp Program Admin Report run
    wk4_run_id        — Northern's last-week in_progress run (manager-flow target)
    opp_id            — primary opportunity id (Northern, by convention)
    workflow_def_id   — chc_nutrition_analysis workflow definition id
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from walkthroughs._lib import config as wcfg  # noqa: E402
from walkthroughs._lib.verify import report, run_checks  # noqa: E402


def main() -> int:
    config_path = HERE / "demo_config.json"
    config = json.loads(config_path.read_text())
    config.pop("_comment", None)

    # Django setup so we can import the seed function directly.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    import django

    django.setup()

    from commcare_connect.labs.synthetic.program_admin_demo import program_admin_demo_seed

    if not os.environ.get("LABS_CONNECT_TOKEN"):
        sys.exit(
            "ERROR: LABS_CONNECT_TOKEN env var is required. Export your labs "
            "OAuth access token before running this script. The recorder "
            "scripts use the same token via the session file at "
            f"{wcfg.session_path()}."
        )

    class _User:
        # Minimal surface: `username` for record provenance + access via env
        # for the OAuth token resolver.
        username = os.environ.get("LABS_USER", "manager")

    result = program_admin_demo_seed(
        user=_User(),
        weeks=config["weeks"],
        opps=config["opps"],
        cleanup_first=bool(config.get("cleanup_first", True)),
    )

    # Resolve the four ids the recorders need.
    par_run_id = result["program_admin_report"]["run_id"]
    primary_opp_cfg = config["opps"][0]
    primary_opp_id = primary_opp_cfg["opportunity_id"]
    primary = next(opp for opp in result["opportunities"] if opp["opportunity_id"] == primary_opp_id)
    wk4 = next((w for w in primary["weeks"] if w.get("in_progress")), None)
    ids = {
        "par_run_id": par_run_id,
        "opp_id": primary_opp_id,
        "workflow_def_id": primary["workflow_definition_id"],
    }
    if wk4:
        ids["wk4_run_id"] = wk4["run_id"]

    written = wcfg.write_run_ids(HERE, ids)
    print(json.dumps(result, indent=2))
    print()
    for k, v in ids.items():
        print(f"  {k}={v}")
    print(f"\nWrote {written}")

    # Pre-record smoke check — surface any mismatch immediately rather
    # than during the recorder's scene 2.
    print("\nRunning verify checks...")
    issues = run_checks(result, config)
    return report(issues)


if __name__ == "__main__":
    raise SystemExit(main())
