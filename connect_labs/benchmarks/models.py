"""Cross-opportunity benchmark aggregates, owned by labs.

The labs DB is the system of record here, for the same three reasons
`supply_chain` gives: the data originates in labs (the semantic layer computes
it), it carries no PII by construction (aggregates only), and it needs
relational work (per-indicator, per-period, per-cohort series).

The consequence that matters is permission. Labs already knows every user's
opportunity access from the OAuth session, so "which opportunities may read
this" is enforceable here -- production Connect's LabsRecord has exactly one
ACL field (`public`) and no allow-list, so any cross-opportunity grant kept
there would mean a model change in another repo.

Opportunity and organisation references are plain columns, never FKs: those
tables are empty in labs (CLAUDE.md), and `ComputedEntityCache` sets the
precedent.
"""

from __future__ import annotations

from django.core.validators import MinValueValidator
from django.db import models

from connect_labs.audit_trail.service import record as audit_record

# The lowest min_peers a cohort may carry. 1 means "no peer floor": see R1 in
# disclosure.py for why that is a cohort's choice (Jonathan, 2026-09-18). Kept as
# a constant and a database constraint so 0 -- which would publish a figure no
# opportunity contributed -- stays impossible.
MIN_PEERS_FLOOR = 1


class BenchmarkCohort(models.Model):
    """A named set of opportunities that may be benchmarked against each other.

    Membership IS the grant: an opportunity sees benchmarks for the cohorts it
    belongs to and nothing else. A second cohort (only the Uganda opportunities,
    say) is a row, not a code change.
    """

    name = models.CharField(max_length=200)
    # Connect organisation slug (e.g. "dimagi-kmc"). Who owns and may publish.
    organization_id = models.CharField(max_length=200, db_index=True)
    description = models.TextField(blank=True, default="")

    # Publishing to delivery partners is a different decision from completing a
    # run for internal review, so it is off until deliberately turned on.
    auto_publish_on_completion = models.BooleanField(default=False)

    # Disclosure thresholds live on the cohort so they are tunable without a
    # deploy. See disclosure.py for what each one defends against.
    min_peers = models.PositiveIntegerField(default=5, validators=[MinValueValidator(MIN_PEERS_FLOOR)])
    min_denominator = models.PositiveIntegerField(default=25)
    # R6. On by default: a line with a hole, or one that stops early, says
    # something about that peer's own history. Switched off, members with
    # fewer reports than the longest-running peer still contribute -- which for
    # a real cohort is the difference between a series and no series at all.
    # Safe to switch off only because a period is an opportunity's own Nth
    # report, so an incomplete line carries no date. R1 and R5 still apply.
    require_complete_series = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "benchmarks"
        db_table = "labs_benchmark_cohort"
        constraints = [
            # The validator alone would not hold: it runs on `full_clean()`, and
            # nothing in this codebase calls that before `objects.create()`. The
            # database is where "no cohort below the floor" is actually true.
            models.CheckConstraint(
                condition=models.Q(min_peers__gte=MIN_PEERS_FLOOR), name="benchmark_cohort_min_peers_floor"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.organization_id})"

    @property
    def opportunity_ids(self) -> set[int]:
        return set(self.members.values_list("opportunity_id", flat=True))

    @classmethod
    def for_opportunity(cls, opportunity_id: int):
        """Every cohort this opportunity belongs to."""
        return cls.objects.filter(members__opportunity_id=int(opportunity_id)).distinct()


class BenchmarkCohortMember(models.Model):
    cohort = models.ForeignKey(BenchmarkCohort, on_delete=models.CASCADE, related_name="members")
    opportunity_id = models.IntegerField(db_index=True)

    class Meta:
        app_label = "benchmarks"
        db_table = "labs_benchmark_cohort_member"
        constraints = [
            models.UniqueConstraint(fields=["cohort", "opportunity_id"], name="uniq_benchmark_cohort_member"),
        ]

    def __str__(self) -> str:
        return f"opp {self.opportunity_id} in {self.cohort_id}"


class BenchmarkPublication(models.Model):
    """One publish event -- the audit row, and what every value hangs off.

    A publication is immutable. Re-publishing creates a new one, so a figure a
    partner saw can always be reconstructed.
    """

    cohort = models.ForeignKey(BenchmarkCohort, on_delete=models.CASCADE, related_name="publications")
    source_workflow_id = models.IntegerField()
    source_run_id = models.IntegerField()
    registry_id = models.IntegerField(null=True, blank=True)
    # The as-of date of the snapshot these figures were computed from.
    as_of = models.DateField()
    published_by = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "benchmarks"
        db_table = "labs_benchmark_publication"
        indexes = [models.Index(fields=["cohort", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.cohort_id} as of {self.as_of}"


class _BenchmarkValueQuerySet(models.QuerySet):
    def with_source(self):
        """The named, audited way to read values for their source opportunity.

        This is a NAMING and AUDIT boundary, not a technical one -- the column is
        on the model and `row.opportunity_id` works on any instance. Django's
        `defer()` was tried here and rejected: a deferred field lazily loads on
        access rather than raising, so it reads as a guard while guarding
        nothing, which is worse than no guard at all.

        What actually holds the boundary is three things, in this order:
          1. `to_public()` is the only projection any view may call.
          2. A contract test asserts no benchmark response carries an
             opportunity id, over the payload rather than per-endpoint.
          3. A source-level test pins which modules may mention `with_source`,
             so widening that set is a reviewed act.

        Three callers are legitimate: the publisher, the debugging path and
        republication.
        """
        audit_record(
            "read",
            resource_type="benchmark_value_identified",
            metadata={"model": "BenchmarkValue"},
        )
        return self


class _BenchmarkValueManager(models.Manager):
    def get_queryset(self):
        return _BenchmarkValueQuerySet(self.model, using=self._db)

    def with_source(self):
        return self.get_queryset().with_source()


class BenchmarkValue(models.Model):
    """One published figure: an indicator, for one anonymous peer, at one period.

    `peer_index` is assigned at publish time by sorting peers on value WITHIN
    this indicator (rule R4), so the index carries no identity and cannot be
    joined across indicators into a per-opportunity profile.
    """

    publication = models.ForeignKey(BenchmarkPublication, on_delete=models.CASCADE, related_name="values")
    series = models.CharField(max_length=16)
    indicator_id = models.CharField(max_length=64, db_index=True)
    # None for a point-in-time value; "YYYY-MM" for a series point.
    period = models.CharField(max_length=7, null=True, blank=True)
    peer_index = models.PositiveIntegerField()
    value = models.FloatField()

    # Provenance. Stored deliberately (Jonathan, 2026-09-14) and never
    # displayed: every read for display goes through `to_public()`, and reading
    # it for its source is spelled `with_source()` so it is greppable and
    # audited. See _BenchmarkValueQuerySet.with_source for why this is a naming
    # boundary rather than a technical one.
    opportunity_id = models.IntegerField(db_index=True)

    objects = _BenchmarkValueManager()

    class Meta:
        app_label = "benchmarks"
        db_table = "labs_benchmark_value"
        indexes = [models.Index(fields=["publication", "series", "indicator_id", "period"])]

    def to_public(self) -> dict:
        """The only projection that may leave this model; callers may narrow
        further, never widen."""
        return {
            "series": self.series,
            "indicator_id": self.indicator_id,
            "period": self.period,
            "peer_index": self.peer_index,
            "value": self.value,
        }
