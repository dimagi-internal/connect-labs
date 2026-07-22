"""Setup entrypoint for the Nutrition Demo (OES/ECF) walkthrough.

The single ``setup:`` command the canopy walkthrough invokes before
rendering. Unlike the program-admin-report ensure_env.py (which re-realizes
a live 2-opp env each render), this env is a **pinned, idempotent, already-
deployed** 3-NM program-owned environment:

  - ``connect_labs/labs/synthetic/envs/nutrition-demo.yaml`` — 3 opps
    (10010 Northern, 10011 Central, 10012 Eastern) filed under PROGRAM 10110,
    so the cross-opp Program Admin Report rollup is PROGRAM-owned (viewed via
    ``&program_id=10110``). The manifests pin a fixed May-2026 story
    (Mondays May 4 / 11 / 18 / 25 + the current in-progress week), so a
    re-``synthetic_env_ensure`` yields the SAME completed-week run / audit /
    task ids the drills below target (only the current-week runs regenerate,
    and no drill targets one).

Because the env is pinned + deployed, this script's job is simply to (re)emit
the realized ``${...}`` var map the walkthrough substitution reads. The map is
authoritative and checked in as ``realized.json`` next to this file; the
walkthrough's ``setup.rerun: once`` means the recorder skips this command
entirely whenever ``realized.json`` already exists. It only runs to RESTORE
the file if it was deleted.

To fully RE-REALIZE the env against labs (fresh data, same pinned ids), call
the ``synthetic_env_ensure`` MCP tool with ``env="nutrition-demo"`` (labs PAT
required) — that writes the labs-only opps through the local-records backend
on labs prod, the only transport that reaches labs for synthetic opps.

Usage::

    python scripts/walkthroughs/nutrition-demo/ensure_env.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REALIZED = HERE / "realized.json"

# The pinned realized map — PAR run 5005 on program-owned def 5003
# (program_id 10110), plus the Central-cluster drill targets (kadi_c resolved,
# lola_c open) and the Eastern-cluster AI-flagged-image case (vida_e suspended).
REALIZED_MAP = {
    "par_url": "/labs/workflow/5003/run/?run_id=5005&program_id=10110",
    "met_opp_label": "Northern Cluster",
    "good_opp_label": "Central Cluster",
    "good_week_label": "May 11",
    "good_flw_name": "Kadi Fofana",
    "audit_good_url": "https://labs.connect.dimagi.com/audit/4996/?opportunity_id=10011",
    "task_good_url": "https://labs.connect.dimagi.com/tasks/5000/edit/?opportunity_id=10011",
    "incomplete_opp_label": "Central Cluster",
    "incomplete_week_label": "May 18",
    "incomplete_flw_name": "Lola Kargbo",
    "audit_incomplete_url": "https://labs.connect.dimagi.com/audit/4997/?opportunity_id=10011",
    "task_incomplete_url": "https://labs.connect.dimagi.com/tasks/5001/edit/?opportunity_id=10011",
    "images_opp_label": "Eastern Cluster",
    "images_week_label": "May 18",
    "images_flw_name": "Vida Kargbo",
    "images_audit_url": "https://labs.connect.dimagi.com/audit/4999/?opportunity_id=10012",
    "images_task_url": "https://labs.connect.dimagi.com/tasks/5002/edit/?opportunity_id=10012",
}


def main() -> None:
    REALIZED.write_text(json.dumps(REALIZED_MAP, indent=2) + "\n")
    print(f"[nutrition-demo] wrote {REALIZED.relative_to(HERE.parents[2])}")
    for key, value in REALIZED_MAP.items():
        print(f"  {key} = {value}")


if __name__ == "__main__":
    main()
