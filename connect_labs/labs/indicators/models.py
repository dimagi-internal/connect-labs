"""Storage for targeting indicators.

One fact table. Population, births, mortality rates and fertility all live as
rows in ``IndicatorValue``, distinguished by ``indicator`` and given their
aggregation semantics by ``measures.MEASURES`` rather than by their table.

The deliberate choice here is that ``(indicator, boundary, year, source)`` is
unique but ``(indicator, boundary, year)`` is *not*. Competing estimates for the
same cell coexist as rows and are chosen at query time. That follows the
precedent already set by ``AdminBoundary.extra.populations``, which holds eight
parallel population figures per Nigerian ward from WorldPop, Meta, GRID3 and
GeoPoDe — the sources disagree, and hiding that behind a single canonical number
would be the dishonest option.
"""

from __future__ import annotations

from django.db import models

from connect_labs.labs.admin_boundaries.models import AdminBoundary


class License(models.TextChoices):
    """Licence of the underlying data, carried on the row.

    Kept on the value rather than in a README so that "may we put this in a
    commercially-framed deliverable?" is a query rather than a memory. See
    ``NON_COMMERCIAL`` below.
    """

    CC_BY_4 = "cc-by-4.0", "CC BY 4.0"
    CC_BY_3_IGO = "cc-by-3.0-igo", "CC BY 3.0 IGO"
    CC_BY_3 = "cc-by-3.0", "CC BY 3.0 Unported"
    CC_BY_NC_SA_3_IGO = "cc-by-nc-sa-3.0-igo", "CC BY-NC-SA 3.0 IGO"
    OPEN_API = "open-api", "Open API, no explicit licence"
    # CHIRPS is released without restriction by UCSB's Climate Hazards Center.
    # Distinct from CC BY: there is no attribution *requirement*, though the
    # method string names the source anyway because provenance is the point.
    PUBLIC_DOMAIN = "public-domain", "Public domain"
    DERIVED = "derived", "Derived (inherits from inputs)"


#: Licences that forbid commercial use. Values carrying one of these must not
#: reach a commercially-framed surface. IHME's non-commercial agreement would
#: land here too, if we ever licensed it — see the source research.
NON_COMMERCIAL = frozenset({License.CC_BY_NC_SA_3_IGO})


class Source(models.TextChoices):
    DHS = "dhs", "DHS Program"
    IGME = "igme", "UN IGME (via UNICEF SDMX)"
    WORLDPOP = "worldpop", "WorldPop"
    WORLDPOP_RASTER = "worldpop_raster", "WorldPop (1km grid, UN-adjusted)"
    HAPI = "hapi", "HDX HAPI"
    WORLDBANK = "worldbank", "World Bank (WDI)"
    IGME_SUBNATIONAL = "igme_subnational", "UN IGME (subnational model)"
    DHS_CALIBRATED = "dhs_calibrated", "DHS (re-levelled)"
    MAP = "map", "Malaria Atlas Project"
    MAP_WORLDPOP = "map_worldpop", "MAP accessibility x WorldPop"
    GHSL = "ghsl", "GHS-SMOD (DEGURBA) x WorldPop"
    # UNICEF's subnational SDMX warehouse. Distinct from IGME above, which is
    # also served over UNICEF SDMX: this is the JMP's harmonised pooling of
    # DHS, MICS, MIS and national surveys, and it is the only route to the
    # countries DHS has never surveyed. See sources/unicef_sdmx.py.
    UNICEF_SDMX = "unicef_sdmx", "UNICEF (subnational SDMX)"
    CHIRPS = "chirps", "CHIRPS x WorldPop"
    DERIVED = "derived", "Derived"


#: DHS suppresses an estimate resting on fewer than 25 unweighted cases and
#: parenthesises one resting on 25 to 49 — its own published convention for
#: "do not rely on this". We carry the same line rather than inventing one,
#: because a reader who knows DHS already knows what a bracketed figure means.
SMALL_SAMPLE_UNWEIGHTED = 50


class IndicatorValue(models.Model):
    """A single measured or derived number, for one boundary, in one year."""

    indicator = models.CharField(max_length=40, db_index=True, help_text="Measure code — see measures.MEASURES")
    boundary = models.ForeignKey(
        AdminBoundary,
        on_delete=models.CASCADE,
        related_name="indicator_values",
        help_text="The administrative unit this value describes",
    )
    # Denormalised from boundary so continent-wide queries stay a single table
    # scan. Kept in sync by save(); boundaries do not change identity.
    iso_code = models.CharField(max_length=3, db_index=True)
    admin_level = models.PositiveSmallIntegerField(db_index=True)

    year = models.IntegerField(db_index=True, help_text="Reference year of the estimate")

    value = models.FloatField()
    ci_low = models.FloatField(null=True, blank=True, help_text="Lower bound of the published uncertainty interval")
    ci_high = models.FloatField(null=True, blank=True, help_text="Upper bound of the published uncertainty interval")

    source = models.CharField(max_length=20, choices=Source.choices, db_index=True)
    source_ref = models.CharField(
        max_length=200,
        blank=True,
        help_text="Specific origin — survey code, dataset release, or API dataset name",
    )
    source_url = models.URLField(
        blank=True,
        max_length=500,
        help_text="Where a reader can go to see this figure at its source",
    )
    license_code = models.CharField(max_length=30, choices=License.choices, db_index=True)
    method = models.TextField(
        blank=True,
        help_text="For derived values, the formula and inputs used. Shown verbatim in the methodology panel.",
    )
    retrieved_at = models.DateTimeField(help_text="When this value was fetched from its source")
    extra = models.JSONField(default=dict, blank=True, help_text="Source-specific metadata kept verbatim")

    class Meta:
        db_table = "labs_indicator_value"
        indexes = [
            models.Index(fields=["indicator", "iso_code", "year"]),
            models.Index(fields=["indicator", "admin_level", "year"]),
            models.Index(fields=["boundary", "indicator"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["indicator", "boundary", "year", "source"],
                name="labs_indicator_value_uniq",
            ),
        ]
        verbose_name = "Indicator value"
        verbose_name_plural = "Indicator values"
        ordering = ["iso_code", "admin_level", "indicator", "-year"]

    def __str__(self):
        return f"{self.indicator}={self.value:g} @ {self.boundary_id} ({self.year}, {self.source})"

    def save(self, *args, **kwargs):
        # Keep the denormalised columns true without making callers remember.
        if self.boundary_id and (not self.iso_code or self.admin_level is None):
            self.iso_code = self.boundary.iso_code
            self.admin_level = self.boundary.admin_level
        return super().save(*args, **kwargs)

    @property
    def is_non_commercial(self) -> bool:
        return self.license_code in NON_COMMERCIAL


class IngestRun(models.Model):
    """One execution of a source loader — what ran, when, and what it wrote.

    Exists so the methodology panel can answer "how fresh is this?" without
    guessing from ``retrieved_at`` scattered across rows.
    """

    source = models.CharField(max_length=20, choices=Source.choices, db_index=True)
    indicator = models.CharField(max_length=40, blank=True, help_text="Blank when a loader writes several")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    rows_written = models.IntegerField(default=0)
    countries = models.IntegerField(default=0)
    ok = models.BooleanField(default=False)
    detail = models.TextField(blank=True, help_text="Errors, skips, and notes worth keeping")

    class Meta:
        db_table = "labs_indicator_ingest_run"
        ordering = ["-started_at"]

    def __str__(self):
        state = "ok" if self.ok else "failed"
        return f"{self.source}/{self.indicator or 'all'} {state} ({self.rows_written} rows)"


class ResearchNote(models.Model):
    """What we already worked out about an indicator, and how to tell if it still holds.

    Investigating an indicator properly is expensive — which sources exist,
    which of them can answer it, what their licences permit, why the ones we
    rejected were rejected. Doing that again from scratch every time somebody
    asks about malaria is waste. Trusting a note written months ago is worse
    than waste, because the data underneath it moves and a stale conclusion
    reads exactly like a fresh one.

    So a note is not allowed to be only prose. It carries ``checks``: claims
    small enough to re-run against the live database every time the note is
    read. A reader gets the thinking *and* a verdict on whether the thinking
    still describes reality — and when a check has drifted, the note says so
    instead of being quietly wrong.

    ``scanned_at`` records something the checks cannot: when we last went
    looking for sources we do not already know about. Checks tell you whether
    what you found still holds. Only a new scan tells you whether something
    better has appeared since.
    """

    #: How long a full alternative-source scan stays fresh. Six months is
    #: roughly the cadence at which the big providers cut releases — MAP and
    #: WorldPop annually, DHS continuously but in country-sized lumps. Past
    #: this the note still reads, but it says a rescan is due rather than
    #: letting a reader assume the field was swept yesterday.
    SCAN_INTERVAL_DAYS = 180

    indicator = models.CharField(
        max_length=40,
        blank=True,
        db_index=True,
        help_text="Measure code this is about; blank for research that spans indicators",
    )
    topic = models.SlugField(max_length=80, help_text="Short slug — what question this note answers")
    summary = models.CharField(max_length=300, help_text="The conclusion, in one line")
    body = models.TextField(help_text="The reasoning, in markdown. What was tried, what was found, what it means.")

    checks = models.JSONField(
        default=list,
        blank=True,
        help_text="Falsifiable claims, re-run every time the note is read. See research.revalidate.",
    )
    alternatives = models.JSONField(
        default=list,
        blank=True,
        help_text="Sources considered: name, url, licence, verdict, and why it was or was not adopted",
    )
    scanned_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When a FULL alternative-source scan was last run for this indicator",
    )
    author = models.CharField(max_length=120, blank=True, help_text="Who or what wrote this")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "labs_indicator_research"
        constraints = [
            models.UniqueConstraint(fields=["indicator", "topic"], name="labs_indicator_research_uniq"),
        ]
        ordering = ["indicator", "topic"]
        verbose_name = "Research note"

    def __str__(self):
        return f"{self.indicator or 'general'}/{self.topic}"
