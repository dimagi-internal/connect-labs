"""Write the per-state EMOD inputs (read-only against the indicator registry)."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from connect_labs.labs.indicators.emod import states as emod_states

DEFAULT_OUT = Path(__file__).resolve().parents[5] / "tools" / "pmc_emod" / "states" / "inputs.json"


class Command(BaseCommand):
    help = "Export per-state rainfall, prevalence, incidence and under-5 population for EMOD."

    def add_arguments(self, parser):
        parser.add_argument(
            "--out", default=str(DEFAULT_OUT), help="Output path (default: tools/pmc_emod/states/inputs.json)"
        )
        parser.add_argument("--state", action="append", help="Limit to this state (repeatable)")

    def handle(self, *args, **opts):
        states, skipped = emod_states.state_inputs(opts["state"])
        out = Path(opts["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(emod_states.build_document(states, skipped), indent=2) + "\n")
        detail = "; ".join(f"{s['name']} ({s['reason']})" for s in skipped) or "none"
        self.stdout.write(f"states written: {len(states)}; skipped: {len(skipped)} [{detail}] -> {out}")
