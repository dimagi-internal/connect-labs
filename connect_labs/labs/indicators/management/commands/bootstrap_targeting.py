"""Build the whole targeting dataset from an empty database, in one command.

The stages have real dependencies — births need mortality *and* population,
calibration needs both the surveys and the IGME series, households need the
population and the household-size ratio — and getting the order wrong produces a
dataset that looks loaded but is quietly short. This encodes the order so it
cannot be got wrong by hand.

Every stage is idempotent: values are upserted on
``(indicator, boundary, year, source)``, so re-running repairs rather than
duplicates. Safe to interrupt and re-run.

    make manage CMD="bootstrap_targeting"                  # everything
    make manage CMD="bootstrap_targeting --skip-worldpop"  # skip the slow, quota-limited stage
    make manage CMD="bootstrap_targeting --iso NGA,KEN"    # a couple of countries, for a trial

Expect roughly 25-40 minutes without WorldPop, most of it geoBoundaries
downloads. WorldPop adds hours and is subject to an undocumented daily quota —
see ``--skip-worldpop``.
"""

from __future__ import annotations

import time

from django.core.management import call_command
from django.core.management.base import BaseCommand

from connect_labs.labs.indicators import boundaries
from connect_labs.labs.indicators.africa import ISO_CODES
from connect_labs.labs.indicators.models import IndicatorValue

#: Stages in dependency order. The comment on each is why it sits where it does.
STAGE_ORDER = [
    ("mortality", "DHS surveys, the IGME national series, and IGME's subnational model"),
    ("calibrate", "needs both the raw surveys and the IGME series from the stage above"),
    ("fertility", "DHS TFR plus the World Bank national fallback"),
    ("population", "HAPI first (minutes), then WorldPop (hours, quota-limited)"),
    ("births", "derived from population and mortality, so it must follow both"),
    ("child_health", "diarrhoea, malaria, nutrition, immunisation, WASH, and the gaps they imply"),
]


class Command(BaseCommand):
    help = "Load every boundary and indicator the targeting surface needs, in dependency order"

    def add_arguments(self, parser):
        parser.add_argument("--iso", help="Comma-separated ISO-3 codes; default is all of Africa")
        parser.add_argument(
            "--skip-boundaries",
            action="store_true",
            help="Assume boundaries are already loaded",
        )
        parser.add_argument(
            "--skip-worldpop",
            action="store_true",
            help=(
                "Skip the WorldPop half of the population stage. It takes hours and has an "
                "undocumented daily quota; HAPI covers most of the continent in a minute. "
                "Only pop_u1 is WorldPop-exclusive."
            ),
        )
        parser.add_argument(
            "--from-stage",
            choices=[s for s, _ in STAGE_ORDER],
            help="Resume from a stage instead of starting at the beginning",
        )

    def handle(self, *args, **opts):
        started = time.time()
        iso_args = ["--iso", opts["iso"]] if opts.get("iso") else []

        self.stdout.write(self.style.MIGRATE_HEADING("Targeting bootstrap"))
        self._report_state("before")

        if not opts["skip_boundaries"]:
            self.stdout.write(self.style.MIGRATE_HEADING("\n[1] Boundaries — geoBoundaries"))
            self.stdout.write(
                "    ADM0 + ADM1 everywhere, plus ADM2 for the countries where IGME\n"
                "    publishes district-level mortality. Slowest step; mostly downloads."
            )
            call_command("load_africa_boundaries", "--adm2", "--missing-only", *iso_args)

            # geoBoundaries publishes each level as a standalone layer with no
            # pointer upward, so the hierarchy has to be derived before anything
            # inherits correctly: without it a district reaches past its own
            # province to the national figure, and household counts — held only
            # at ADM1 — cannot reach ADM2 at all.
            self.stdout.write("    Deriving parent links, which the source omits.")
            call_command("link_admin_parents", *iso_args)

        stages = [s for s, _ in STAGE_ORDER]
        if opts.get("from_stage"):
            stages = stages[stages.index(opts["from_stage"]) :]

        for n, stage in enumerate(stages, start=2):
            why = dict(STAGE_ORDER)[stage]
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n[{n}] {stage}"))
            self.stdout.write(f"    {why}")

            args = ["load_indicators", "--stage", stage, *iso_args]
            if stage == "population" and opts["skip_worldpop"]:
                args += ["--source", "hapi"]
                self.stdout.write(self.style.WARNING("    skipping WorldPop (--skip-worldpop)"))

            try:
                call_command(*args)
            except Exception as exc:  # noqa: BLE001 — one stage must not lose the rest
                self.stdout.write(self.style.ERROR(f"    stage {stage} failed: {exc}"))
                self.stdout.write(self.style.WARNING(f"    continuing; resume later with --from-stage {stage}"))

        self.stdout.write(self.style.MIGRATE_HEADING("\nDone"))
        self._report_state("after")
        self.stdout.write(f"\nElapsed: {(time.time() - started) / 60:.0f} min")

        if opts["skip_worldpop"]:
            self.stdout.write(
                self.style.WARNING(
                    "\nWorldPop was skipped, so pop_u1 is unpopulated and births come from the\n"
                    "fertility method only. Fill it in later with:\n"
                    '  make manage CMD="load_indicators --stage population '
                    '--source worldpop --missing-only"\n'
                    '  make manage CMD="load_indicators --stage births"'
                )
            )

    def _report_state(self, when: str) -> None:
        b = boundaries.owned().filter(iso_code__in=ISO_CODES)
        self.stdout.write(
            f"  {when}: {b.filter(admin_level=0).count()} ADM0, "
            f"{b.filter(admin_level=1).count()} ADM1, "
            f"{b.filter(admin_level=2).count()} ADM2 boundaries; "
            f"{IndicatorValue.objects.count():,} values across "
            f"{IndicatorValue.objects.values('indicator').distinct().count()} indicators"
        )
