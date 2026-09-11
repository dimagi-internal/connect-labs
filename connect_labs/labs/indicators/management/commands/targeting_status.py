"""Is the targeting dataset actually complete? Answers without opening the page.

Written for the moment after a bootstrap on a new machine, when everything
*looks* fine and the only way to tell was to click around. Reports what is
loaded, what is missing, and — where something is missing — the command that
fixes it.
"""

from __future__ import annotations

import collections

from django.core.management.base import BaseCommand

from connect_labs.labs.indicators import availability, boundaries, measures, methods
from connect_labs.labs.indicators.africa import ISO_CODES, name_for
from connect_labs.labs.indicators.models import IndicatorValue, IngestRun
from connect_labs.labs.indicators.resolve import BulkResolver
from connect_labs.labs.indicators.sources import derive


def births_plausibility() -> tuple[int, list[tuple[str, int, str, float]]]:
    """Every ADM1/ADM2 unit whose resolved births cannot belong to its under-fives.

    Resolved exactly as a selection resolves them, so this checks the number a
    reader would actually be shown rather than whichever row happens to be
    stored. Returns (units checked, [(iso, level, name, ratio)]).
    """
    units = list(boundaries.owned().filter(admin_level__in=(1, 2), iso_code__in=ISO_CODES))
    bulk = BulkResolver(units)
    checked = 0
    bad: list[tuple[str, int, str, float]] = []
    for b in units:
        births, u5 = bulk.get("births", b), bulk.get("pop_u5", b)
        if not (births and u5) or u5.value <= 0:
            continue
        checked += 1
        if derive.births_implausible(births.value, u5.value):
            bad.append((b.iso_code, b.admin_level, b.name, births.value / u5.value))
    return checked, sorted(bad)


#: Indicator -> the stage that produces it, for the "what to run" hints.
STAGE_OF = {
    "u5mr": "mortality",
    "imr": "mortality",
    "nmr": "mortality",
    "tfr": "fertility",
    "pop_total": "population",
    "pop_u5": "population",
    "pop_u1": "population (WorldPop only)",
    "pop_f_15_49": "population",
    "births": "births",
    "expected_deaths": "births",
    "lbw_rate": "lbw",
    "births_lbw": "lbw",
    "facility_delivery": "lbw",
    "households": "child_health",
    "diarrhoea_prevalence": "child_health",
    "malaria_prevalence": "child_health",
}


class Command(BaseCommand):
    help = "Report what the targeting dataset holds and what is missing"

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose-countries",
            action="store_true",
            help="List every country missing subnational mortality",
        )

    def handle(self, *args, **opts):
        ok = True

        self.stdout.write(self.style.MIGRATE_HEADING("Boundaries"))
        b = boundaries.owned().filter(iso_code__in=ISO_CODES)
        adm0, adm1, adm2 = (b.filter(admin_level=n).count() for n in (0, 1, 2))
        self.stdout.write(f"  ADM0 {adm0:>5}   ADM1 {adm1:>5}   ADM2 {adm2:>5}")
        if adm1 < 700:
            ok = False
            self.stdout.write(self.style.ERROR('  -> thin. Run: make manage CMD="load_africa_boundaries --adm2"'))

        self.stdout.write(self.style.MIGRATE_HEADING("\nIndicators"))
        counts = collections.Counter(IndicatorValue.objects.values_list("indicator", flat=True))
        total = IndicatorValue.objects.count()
        self.stdout.write(f"  {total:,} values across {len(counts)} indicators")

        missing = [code for code in STAGE_OF if not counts.get(code)]
        if missing:
            ok = False
            self.stdout.write(self.style.ERROR("  missing entirely:"))
            for code in missing:
                self.stdout.write(self.style.ERROR(f"    {code:<24} run the '{STAGE_OF[code]}' stage"))

        for code, n in sorted(counts.items()):
            if 0 < n < 50 and code in STAGE_OF:
                self.stdout.write(self.style.WARNING(f"  thin: {code} has only {n} values"))

        self.stdout.write(self.style.MIGRATE_HEADING("\nBirths plausibility"))
        checked, implausible = births_plausibility()
        lo, hi = derive.BIRTHS_PER_UNDER5
        self.stdout.write(f"  {checked} units carry births and under-fives; births/pop_u5 expected {lo}-{hi}")
        if implausible:
            ok = False
            by_iso = collections.Counter(iso for iso, *_ in implausible)
            self.stdout.write(
                self.style.ERROR(
                    f"  {len(implausible)} units outside it: "
                    + ", ".join(f"{iso} {n}" for iso, n in by_iso.most_common())
                )
            )
            for iso, level, name, ratio in implausible[:10]:
                self.stdout.write(self.style.ERROR(f"    {iso} ADM{level} {name:<28} {ratio:.3f}"))
            self.stdout.write(
                self.style.ERROR(
                    "  -> usually a stale derivation outranking a current one. Re-run: "
                    'make manage CMD="load_indicators --stage births" (it now sweeps what it no longer produces)'
                )
            )

        self.stdout.write(self.style.MIGRATE_HEADING("\nMethods"))
        for code, m in methods.METHODS.items():
            rows = availability.for_method(m, "u5mr")
            have = sum(1 for r in rows if r.available)
            flag = "" if have else "   <- nothing loaded"
            self.stdout.write(f"  {code:<26} {have:>2}/{len(rows)} countries{flag}")
            if not have:
                ok = False

        self.stdout.write(self.style.MIGRATE_HEADING("\nTargetable indicators"))
        empty = [m.code for m in measures.targetable() if not counts.get(m.code)]
        self.stdout.write(f"  {len(measures.targetable()) - len(empty)}/{len(measures.targetable())} have data")
        if empty:
            self.stdout.write(self.style.WARNING(f"  no data: {', '.join(empty)}"))

        if opts["verbose_countries"]:
            self.stdout.write(self.style.MIGRATE_HEADING("\nNo subnational mortality"))
            for r in availability.for_method(methods.get("subnational_survey"), "u5mr"):
                if not r.available:
                    self.stdout.write(f"  {r.iso_code} {name_for(r.iso_code):<28} {r.reason}")

        self.stdout.write(self.style.MIGRATE_HEADING("\nRecent ingest runs"))
        for r in IngestRun.objects.all()[:8]:
            style = self.style.SUCCESS if r.ok else self.style.ERROR
            self.stdout.write(
                style(
                    f"  {r.started_at:%Y-%m-%d %H:%M}  {r.source}/{r.indicator or 'all':<22} "
                    f"{'ok' if r.ok else 'FAILED':<7} {r.rows_written:>6} rows"
                )
            )

        self.stdout.write("")
        if ok:
            self.stdout.write(self.style.SUCCESS("Dataset looks complete."))
        else:
            self.stdout.write(
                self.style.ERROR(
                    "Dataset is incomplete — see above. " 'Usually: make manage CMD="bootstrap_targeting"'
                )
            )
