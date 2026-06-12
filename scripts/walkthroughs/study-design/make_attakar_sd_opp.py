#!/usr/bin/env python
"""Create the Attakar service-delivery synthetic opp under the Vitamin-A study program.

A FULL service-delivery project across ALL of Attakar (the R6 intervention ward), so
that surveying the whole ward is justified: visits saturate the ward via many
settlements spread across the real Attakar polygon. Filed under program 10008 (the
Vitamin-A Kaura study) via the new SyntheticOpportunity.program_id.

Flow (proper synthetic infrastructure, mirrors how Kano opp 10009 was made):
  1. synthetic_create_labs_only(program_id=10008)  -> allocates a labs-only opp id
  2. synthetic_generate_from_manifest(opp_id, manifest with geography.polygon=Attakar)
     -> generates GPS visits inside Attakar, uploads fixtures to GDrive (keeps program_id)
  3. synthetic_set_my_visibility(true)             -> so the viewing user sees it

Run AFTER the program_id deploy lands. Prints the new opp id + point count.
"""

import json
import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
sys.path.insert(0, str(_REPO / "scripts" / "walkthroughs" / "_lib"))
from labs_mcp import LabsMCPSession  # noqa: E402

STUDY_PROGRAM_ID = 10008
PROGRAM_NAME = "Vitamin-A community-health home-visit monitoring"
WARD = "Attakar"
GEOJSON = _REPO / "scripts" / "walkthroughs" / "verified-monitoring" / "geo" / "kaura_lga_wards.geojson"


def attakar_polygon() -> dict:
    fc = json.loads(GEOJSON.read_text())
    for f in fc["features"]:
        if f["properties"].get("name") == WARD:
            return f["geometry"]
    raise SystemExit(f"{WARD} not found in {GEOJSON}")


def build_manifest(opp_id: int) -> str:
    """A real, ward-spanning CHC service-delivery project inside Attakar."""
    manifest = {
        "opportunity_id": opp_id,
        "opportunity_name": "Kaura CHC — Service Delivery (Attakar)",
        "random_seed": 20260612,
        "timeline": {
            "start_date": "2026-01-05",
            "end_date": "2026-04-26",
            "weeks": 16,
            "visit_cadence_per_week_per_flw": {"mean": 5, "stddev": 1},
        },
        # A 6-CHW delivery team working the whole ward.
        "flw_personas": [
            {
                "id": f"chw{i}",
                "display_name": name,
                "archetype": arch,
                "accuracy_distribution": {"mean": acc, "stddev": 0.03},
                "completeness_distribution": {"mean": comp, "stddev": 0.03},
                "flag_rate": flag,
            }
            for i, (name, arch, acc, comp, flag) in enumerate(
                [
                    ("Amina B.", "rockstar", 0.93, 0.96, 0.0),
                    ("Yakubu D.", "steady", 0.88, 0.92, 0.05),
                    ("Hauwa K.", "steady", 0.86, 0.90, 0.05),
                    ("Sani M.", "steady", 0.85, 0.89, 0.08),
                    ("Ladi T.", "new_hire", 0.80, 0.86, 0.10),
                    ("Musa A.", "struggling", 0.70, 0.82, 0.20),
                ],
                start=1,
            )
        ],
        # ~480 households scattered across the whole ward (large cohort = ward-wide point cloud).
        "beneficiary_cohorts": [
            {
                "id": "households",
                "size": 480,
                "field_distributions": {
                    "form.muac_mm": {"distribution": "normal", "mean": 132.0, "stddev": 9.0},
                },
                "progression": "flat",
            }
        ],
        "anomalies": [],
        "kpi_config": [
            {
                "kpi": "accuracy",
                "field_path": "form.muac_mm",
                "aggregation": "validated_rate",
                "threshold_underperform": 0.75,
                "threshold_target": 0.90,
            }
        ],
        "coaching_arcs": [],
        # FULL-WARD coverage: many settlements spread across the real Attakar polygon.
        "geography": {
            "polygon": attakar_polygon(),
            "settlements": 22,
            "settlement_spread_km": 0.9,
        },
    }
    return yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)


def main() -> int:
    with LabsMCPSession() as s:
        created, err = s.tool(
            "synthetic_create_labs_only",
            {
                "label": "Kaura CHC — Service Delivery (Attakar)",
                "gdrive_folder_id": "pending",
                "org_name": "Vitamin-A Program",
                "program_name": PROGRAM_NAME,
                "program_id": STUDY_PROGRAM_ID,
                "allowed_domains": ["@dimagi.com"],
            },
        )
        if err:
            print("create_labs_only ERROR:", json.dumps(created)[:400])
            return 1
        opp_id = created["opportunity_id"]
        print(f"created opp {opp_id} (program_id={created.get('program_id')})")

        gen, err = s.tool(
            "synthetic_generate_from_manifest",
            {"opportunity_id": opp_id, "manifest_yaml": build_manifest(opp_id)},
        )
        if err:
            print("generate_from_manifest ERROR:", json.dumps(gen)[:600])
            return 1
        print("generated:", json.dumps(gen)[:400])

        vis, verr = s.tool("synthetic_set_my_visibility", {"enabled": True})
        print("set_my_visibility:", "ERR" if verr else "OK")
        print(
            json.dumps(
                {"opp_id": opp_id, "folder_url": gen.get("folder_url"), "record_counts": gen.get("record_counts")},
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
