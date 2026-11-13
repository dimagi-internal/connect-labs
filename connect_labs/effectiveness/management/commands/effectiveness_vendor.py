"""Vendor connect-cea-models' spec.json files, and re-derive the per-country summary from them.

    python manage.py effectiveness_vendor --from ../connect-cea-models      # copy specs, then derive
    python manage.py effectiveness_vendor                                    # derive only

Labs runs the models' own formulas from ``connect_labs/effectiveness/data/<model>.json``.
``effectiveness/data/cea_models.json`` keeps each country's name and alpha-3 code, the
models' labels and Sheet links, and GiveWell's bar; its numbers (multiple, price
and deaths averted per 100,000 at the country's own figures) are worked out here
from the specs, never typed, so the two cannot disagree.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from connect_labs.effectiveness import engine
from connect_labs.effectiveness import result as impact_result

SUMMARY = Path(__file__).resolve().parents[2] / "data" / "cea_models.json"


class Command(BaseCommand):
    help = "Vendor connect-cea-models' spec.json files and re-derive effectiveness/data/cea_models.json from them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--from", dest="src", help="A connect-cea-models checkout to copy models/<m>/spec.json from."
        )

    def handle(self, *args, src=None, **options):
        if src:
            root = Path(src)
            for m in engine.MODELS:
                f = root / "models" / m / "spec.json"
                if not f.exists():
                    raise CommandError(f"{f} not found")
                shutil.copyfile(f, engine.DATA / f"{m}.json")
            commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
            branch = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True
            )
            (engine.DATA / "SOURCE.json").write_text(
                json.dumps(
                    {
                        "repo": "dimagi-internal/connect-cea-models",
                        "commit": commit.stdout.strip(),
                        "branch": branch.stdout.strip(),
                        "files": [f"models/{m}/spec.json" for m in engine.MODELS],
                    },
                    indent=1,
                )
                + "\n",
                encoding="utf-8",
            )
            engine.spec.cache_clear()
            engine.source.cache_clear()
            self.stdout.write(f"copied specs from {root}")

        summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
        for iso2, c in summary["countries"].items():
            for m in engine.MODELS:
                r = impact_result.model_country_result(m, c["iso3"])
                if r is None:
                    raise CommandError(f"{m} has no figures for {c['iso3']}")
                c[m] = {"x": r["x"], "unit_cost": r["price"], "deaths_per_100k": r["deaths_per_100k"]}
        summary["models"]["kc"][
            "variant"
        ] = "home visits after discharge; risk = the model's 13% for enrolled small babies"
        summary["source"] = f"connect_labs/effectiveness/data (connect-cea-models @ {engine.source()['commit'][:7]})"
        summary["generated"] = date.today().isoformat()
        SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"re-derived {len(summary['countries'])} countries into {SUMMARY.name}"))
