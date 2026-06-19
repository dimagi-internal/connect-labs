"""Per-opp profile bundle: the self-contained, prod-free handoff between
Phase 1 (profile, prod-touching) and Phase 2 (generate, offline)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Opportunity-detail keys that are program config / metadata (no beneficiary PII).
_OPP_KEEP_KEYS = {
    "id",
    "name",
    "description",
    "currency",
    "start_date",
    "end_date",
    "max_visits_per_user",
    "daily_max_visits_per_user",
    "budget_per_visit",
    "total_budget",
    "payment_units",
    "deliver_units",
    "organization",
    "program",
}


def scrub_opportunity(detail: dict) -> dict:
    return {k: v for k, v in (detail or {}).items() if k in _OPP_KEEP_KEYS}


@dataclass
class ProfileBundle:
    source_opp_id: int
    manifest_yaml: str
    app_structure: dict
    opportunity: dict


def write_bundle(out_dir, source_opp_id: int, *, manifest_yaml: str, app_structure: dict, opportunity: dict) -> Path:
    d = Path(out_dir) / str(source_opp_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_text(manifest_yaml)
    (d / "app_structure.json").write_text(json.dumps(app_structure or {}, indent=2))
    (d / "opportunity.json").write_text(json.dumps(scrub_opportunity(opportunity), indent=2))
    return d


def read_bundle(bundle_dir) -> ProfileBundle:
    d = Path(bundle_dir)
    return ProfileBundle(
        source_opp_id=int(d.name),
        manifest_yaml=(d / "manifest.yaml").read_text(),
        app_structure=json.loads((d / "app_structure.json").read_text()),
        opportunity=json.loads((d / "opportunity.json").read_text()),
    )
