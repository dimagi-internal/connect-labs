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

from django.db import models


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
    min_peers = models.PositiveIntegerField(default=5)
    min_denominator = models.PositiveIntegerField(default=25)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "benchmarks"
        db_table = "labs_benchmark_cohort"

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
