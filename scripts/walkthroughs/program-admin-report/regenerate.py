"""Regenerate the Program Admin Report demo from demo_config.json.

The synthetic data generator itself lives in
``commcare_connect/mcp/tools/program_admin_demo_v2.py`` and is invoked
either via the labs MCP (``mcp__connect_labs__program_admin_demo_seed_v2``)
or — for scripted reproducibility — directly via this wrapper.

This wrapper exists so the demo configuration is **versioned**. Before it,
each regeneration required copy-pasting a large JSON payload into an MCP
tool call from chat history, which made the demo non-reproducible.

Usage:
    # From a connect-labs checkout, with the labs venv active:
    python scripts/walkthroughs/program-admin-report/regenerate.py

    # Or via the MCP from Claude:
    #   read demo_config.json
    #   pass its contents to mcp__connect_labs__program_admin_demo_seed_v2

The script invokes ``program_admin_demo_seed_v2`` directly (no MCP round-
trip required) and prints the run ids needed by the recorder scripts:

    PAR_RUN_ID=<id>   # the cross-opp Program Admin Report run
    WK4_RUN_ID=<id>   # the Northern Wk4 in_progress run (Manager-flow target)

These env vars are what the recorder scripts (record_manager_flow.py,
record_drill_through.py, capture_walkthrough.py) consume.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main():
    here = Path(__file__).resolve().parent
    config_path = here / "demo_config.json"
    config = json.loads(config_path.read_text())
    config.pop("_comment", None)

    # The seed tool function lives inside Django, so we need Django set up.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    import django
    django.setup()

    from commcare_connect.mcp.tools.program_admin_demo_v2 import program_admin_demo_seed_v2

    # The MCP wrapper passes `user=request.user`; running standalone we need a
    # user proxy that the OAuth-token resolver can find. The token resolver
    # walks request.session for a stashed access_token; fall back to env var.
    class _User:
        # Match the minimal surface the tool function reads — just .username
        # + a way for require_connect_token to find a token in the env.
        username = os.environ.get("LABS_USER", "manager")

    # require_connect_token looks for an env-stashed token first.
    if not os.environ.get("LABS_CONNECT_TOKEN"):
        sys.exit(
            "ERROR: LABS_CONNECT_TOKEN env var is required. Export your labs "
            "OAuth access token before running this script. The recorder "
            "scripts use the same token from ~/.ace/labs-session.json — "
            "extract its access_token cookie value."
        )

    result = program_admin_demo_seed_v2(
        user=_User(),
        weeks=config["weeks"],
        opps=config["opps"],
        cleanup_first=bool(config.get("cleanup_first", True)),
    )

    par_run_id = result["program_admin_report"]["run_id"]
    northern = next(
        opp for opp in result["opportunities"]
        if opp["opportunity_id"] == 10000
    )
    wk4 = next(w for w in northern["weeks"] if w.get("in_progress"))

    print(json.dumps(result, indent=2))
    print()
    print(f"PAR_RUN_ID={par_run_id}")
    print(f"WK4_RUN_ID={wk4['run_id']}")
    print(f"OPP_ID={northern['opportunity_id']}")
    print(f"WORKFLOW_DEF_ID={northern['workflow_definition_id']}")
    print()
    print("Pass these to record_manager_flow.py / record_drill_through.py.")


if __name__ == "__main__":
    main()
