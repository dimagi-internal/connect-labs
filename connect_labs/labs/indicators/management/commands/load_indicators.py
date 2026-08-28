"""Ingest indicators from their sources.

Order matters: mortality before population before births, because births are
derived from the two. Running a single stage is fine — the derive stage simply
finds fewer inputs and says so.

    python manage.py load_indicators                    # everything, all of Africa
    python manage.py load_indicators --stage mortality
    python manage.py load_indicators --iso NGA,KEN
    python manage.py load_indicators --stage population --iso NGA
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators.africa import ISO_CODES
from connect_labs.labs.indicators.models import IndicatorValue, IngestRun, Source
from connect_labs.labs.indicators.sources import (
    base,
    calibrate,
    derive,
    dhs,
    hapi,
    igme,
    igme_subnational,
    worldbank,
    worldpop,
)

STAGES = ("mortality", "calibrate", "fertility", "population", "births", "child_health")


class Command(BaseCommand):
    help = "Load targeting indicators from DHS, UN IGME, WorldPop, and derivations"

    def add_arguments(self, parser):
        parser.add_argument("--iso", help="Comma-separated ISO-3 codes; default is all of Africa")
        parser.add_argument(
            "--stage",
            choices=STAGES,
            help="Run one stage only; default runs all four in dependency order",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=worldpop.MAX_WORKERS,
            help="Concurrent WorldPop requests; more is not faster (see worldpop.MAX_WORKERS)",
        )
        parser.add_argument("--limit", type=int, help="Cap boundaries in the population stage (for a quick trial run)")
        parser.add_argument(
            "--missing-only",
            action="store_true",
            help="Population stage: fetch only boundaries that have no population yet",
        )
        parser.add_argument(
            "--levels",
            default="1",
            help=(
                "Population stage: comma-separated admin levels for WorldPop (default 1). "
                "Level 2 is 1,518 more boundaries against a daily quota, so it is opt-in"
            ),
        )
        parser.add_argument(
            "--source",
            choices=("hapi", "worldpop"),
            help="Population stage: run just one source; default runs HAPI then WorldPop",
        )

    def handle(self, *args, **opts):
        codes = (
            [c.strip().upper() for c in opts["iso"].split(",") if c.strip()] if opts.get("iso") else list(ISO_CODES)
        )
        stages = [opts["stage"]] if opts.get("stage") else list(STAGES)

        if not boundary_set.owned().filter(iso_code__in=codes, admin_level=1).exists():
            self.stdout.write(
                self.style.ERROR(
                    "No ADM1 boundaries for the requested countries. " "Run `manage.py load_africa_boundaries` first."
                )
            )
            return

        for stage in stages:
            getattr(self, f"_stage_{stage}")(codes, opts)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Done. Recent runs:"))
        for run in IngestRun.objects.all()[:6]:
            self.stdout.write(f"  {run}")

    # -- stages ------------------------------------------------------------

    def _stage_mortality(self, codes, opts):
        for measure in ("u5mr", "imr"):
            with self._run(Source.DHS, measure) as ctx:
                rows = dhs.load(measure, iso_codes=codes)
                ctx["rows"] = base.upsert(rows)
                ctx["countries"] = len({r.boundary.iso_code for r in rows})

            with self._run(Source.IGME, measure) as ctx:
                rows = igme.load(measure, iso_codes=codes)
                ctx["rows"] = base.upsert(rows)
                ctx["countries"] = len({r.boundary.iso_code for r in rows})

        # IGME's own small-area model — preferred over anything we derive.
        for measure in ("u5mr", "nmr"):
            with self._run(Source.IGME_SUBNATIONAL, measure) as ctx:
                rows = igme_subnational.load(measure, iso_codes=codes)
                ctx["rows"] = base.upsert(rows)
                ctx["countries"] = len({r.boundary.iso_code for r in rows})

    def _stage_calibrate(self, codes, opts):
        # Runs after mortality: needs both the raw surveys and the IGME series.
        for measure in calibrate.CALIBRATED:
            with self._run(Source.DHS_CALIBRATED, measure) as ctx:
                rows = calibrate.load(measure, iso_codes=codes)
                ctx["rows"] = base.upsert(rows)
                ctx["countries"] = len({r.boundary.iso_code for r in rows})

    def _stage_fertility(self, codes, opts):
        with self._run(Source.DHS, "tfr") as ctx:
            rows = dhs.load("tfr", iso_codes=codes)
            ctx["rows"] = base.upsert(rows)
            ctx["countries"] = len({r.boundary.iso_code for r in rows})

        # National fallback. Without it, a country with no recent DHS has no
        # route to a births estimate and drops out of continental totals
        # silently — a worse error than a coarser number.
        with self._run(Source.WORLDBANK, "tfr") as ctx:
            rows = worldbank.load("tfr", iso_codes=codes)
            ctx["rows"] = base.upsert(rows)
            ctx["countries"] = len({r.boundary.iso_code for r in rows})

    def _stage_population(self, codes, opts):
        # HAPI first: it returns a whole country per request, where WorldPop
        # needs a task per polygon and queues them server-side. HAPI cannot
        # supply pop_u1, so WorldPop still has work to do — but everything HAPI
        # covers is population we do not have to wait for.
        if opts.get("source") in (None, "hapi"):
            for level in (1, 2):
                with self._run(Source.HAPI, f"adm{level}") as ctx:
                    rows = hapi.load(codes, admin_level=level)
                    ctx["rows"] = base.upsert(rows)
                    ctx["countries"] = len({r.boundary.iso_code for r in rows})

        if opts.get("source") == "hapi":
            return

        self._stage_population_worldpop(codes, opts)

    def _stage_population_worldpop(self, codes, opts):
        # Never ADM0. A country's population is the sum of its regions — asking
        # the service for the country outline as well would cost a second
        # measurement that can only disagree with the first, and every ADM0 in
        # Africa is far over the service's 100,000 km2 area cap anyway.
        #
        # ADM1 is the default because it is the layer every method can use.
        # ADM2 is opt-in rather than automatic: it is 1,518 more boundaries
        # against an undocumented daily quota, and spending the quota there
        # before ADM1 is complete would leave the base layer short. It matters
        # nonetheless — where a method resolves at ADM2, a district with no
        # population of its own contributes no births, and population is a
        # count, so it can never be inherited from the province above.
        levels = [int(x) for x in str(opts.get("levels") or "1").split(",") if x.strip()]
        if any(lvl not in (1, 2) for lvl in levels):
            raise CommandError(f"--levels must be 1 and/or 2, got {opts['levels']!r}")

        boundaries = list(
            boundary_set.owned()
            .filter(iso_code__in=codes, admin_level__in=levels)
            .order_by("admin_level", "iso_code", "name")
        )
        if opts.get("missing_only"):
            # Keyed on pop_u1, NOT pop_total. HAPI supplies a total but has no
            # 0-1 band, so keying on pop_total would skip precisely the
            # boundaries that still need WorldPop — and births coverage would
            # silently plateau.
            have = set(IndicatorValue.objects.filter(indicator="pop_u1").values_list("boundary_id", flat=True))
            before = len(boundaries)
            boundaries = [b for b in boundaries if b.pk not in have]
            self.stdout.write(f"  skipping {before - len(boundaries)} boundaries already loaded")

        if opts.get("limit"):
            boundaries = boundaries[: opts["limit"]]

        if not boundaries:
            self.stdout.write("  nothing to fetch")
            return

        self.stdout.write(
            f"WorldPop: {len(boundaries)} boundaries, {opts['workers']} workers. "
            "Each is a hosted zonal-stats task; expect several seconds apiece."
        )
        started = time.time()

        def progress(done, total, boundary, n_rows):
            if done % 25 == 0 or done == total:
                rate = done / max(time.time() - started, 1e-6)
                left = (total - done) / rate if rate else 0
                self.stdout.write(
                    f"  {done}/{total} ({done * 100 // total}%) — "
                    f"{boundary.iso_code} {boundary.name} — ~{left / 60:.0f} min left"
                )

        # Written as each boundary lands, not buffered to the end: a run this
        # long must not be all-or-nothing.
        seen_countries: set[str] = set()

        def sink(rows):
            base.upsert(rows)
            seen_countries.update(r.boundary.iso_code for r in rows)

        with self._run(Source.WORLDPOP, "") as ctx:
            produced, failures = worldpop.load(
                boundaries, max_workers=opts["workers"], on_progress=progress, sink=sink
            )
            ctx["rows"] = produced
            ctx["countries"] = len(seen_countries)
            if failures:
                ctx["detail"] = f"{len(failures)} boundaries returned no data: " + ", ".join(failures[:50])
                self.stdout.write(self.style.WARNING(f"  {len(failures)} boundaries returned no population"))

    def _stage_births(self, codes, opts):
        with self._run(Source.DERIVED, "births") as ctx:
            rows = derive.load(iso_codes=codes)
            ctx["rows"] = base.upsert(rows)
            ctx["countries"] = len({r.boundary.iso_code for r in rows})

        with self._run(Source.DERIVED, "births_fertility_check") as ctx:
            rows = derive.load_fertility_crosscheck(iso_codes=codes)
            ctx["rows"] = base.upsert(rows)
            ctx["countries"] = len({r.boundary.iso_code for r in rows})

        # Depends on births, so it runs last in this stage.
        with self._run(Source.DERIVED, "expected_deaths") as ctx:
            rows = derive.load_expected_deaths(iso_codes=codes)
            ctx["rows"] = base.upsert(rows)
            ctx["countries"] = len({r.boundary.iso_code for r in rows})

        div = derive.births_divergence(iso_codes=codes)
        if div:
            wide = sum(1 for d in div.values() if d > 0.25)
            self.stdout.write(f"  births cross-check: {wide} of {len(div)} regions disagree by >25%")

    def _stage_child_health(self, codes, opts):
        """Diarrhoea, ORS and breastfeeding — and the ORS gap they imply.

        Runs after population and births, since the derived gap needs pop_u5.
        """
        for measure in (
            "diarrhoea_prevalence",
            "ors_coverage",
            "diarrhoea_untreated",
            "exclusive_breastfeeding",
            "stunting",
            "wasting",
            "vitamin_a_coverage",
            "measles_vaccination",
            "dpt3_vaccination",
            "full_immunisation",
            "malaria_prevalence",
            "malaria_treatment",
            "itn_use_children",
            "ari_prevalence",
            "ari_antibiotics",
            "zinc_coverage",
            "skilled_birth_attendance",
            "anc4",
            "mean_household_size",
            "improved_water",
            "improved_sanitation",
        ):
            with self._run(Source.DHS, measure) as ctx:
                rows = dhs.load(measure, iso_codes=codes)
                ctx["rows"] = base.upsert(rows)
                ctx["countries"] = len({r.boundary.iso_code for r in rows})

        with self._run(Source.DERIVED, "ors_gap_children") as ctx:
            rows = derive.load_ors_gap(iso_codes=codes)
            ctx["rows"] = base.upsert(rows)
            ctx["countries"] = len({r.boundary.iso_code for r in rows})

        with self._run(Source.DERIVED, "households") as ctx:
            rows = derive.load_households(iso_codes=codes)
            ctx["rows"] = base.upsert(rows)
            ctx["countries"] = len({r.boundary.iso_code for r in rows})

        # One row per coverage measure, driven by the registry.
        with self._run(Source.DERIVED, "coverage_gaps") as ctx:
            rows = derive.load_coverage_gaps(iso_codes=codes)
            ctx["rows"] = base.upsert(rows)
            ctx["countries"] = len({r.boundary.iso_code for r in rows})

    # -- run bookkeeping ---------------------------------------------------

    class _RunContext(dict):
        pass

    def _run(self, source: str, indicator: str):
        """Context manager recording one loader execution as an IngestRun."""
        stdout, style = self.stdout, self.style

        class _CM:
            def __enter__(self_inner):
                self_inner.run = IngestRun.objects.create(source=source, indicator=indicator)
                self_inner.ctx = Command._RunContext(rows=0, countries=0)
                stdout.write(f"\n> {source}/{indicator or 'all'}")
                return self_inner.ctx

            def __exit__(self_inner, exc_type, exc, tb):
                run = self_inner.run
                run.finished_at = timezone.now()
                run.rows_written = self_inner.ctx.get("rows", 0)
                run.detail = self_inner.ctx.get("detail", "") or run.detail
                run.countries = self_inner.ctx.get("countries", 0)
                run.ok = exc is None
                if exc is not None:
                    run.detail = f"{exc_type.__name__}: {exc}"
                    stdout.write(style.ERROR(f"  failed: {exc}"))
                else:
                    stdout.write(style.SUCCESS(f"  {run.rows_written} rows across {run.countries} country(ies)"))
                run.save()
                # Swallow so one failing source does not abort the whole ingest.
                return exc is not None

        return _CM()
