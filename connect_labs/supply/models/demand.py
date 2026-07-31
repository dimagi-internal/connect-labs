"""Demand domain: who needs the food, and whether it reached them.

The third lifecycle stage. :mod:`.procurement` runs up to the award decision,
:mod:`.execution` moves the goods, and this module is the denominator both of
those lack — how many severely malnourished children a district is expected to
have, what a site planned to distribute, and what a treated child's arm
circumference actually did.

Without a denominator, delivery can only be counted. A large delivery into a
large caseload and a small delivery into a small one are identical in tonnes,
which is why "cartons delivered" cannot answer a government's question and
"children treated" is usually just cartons divided by a constant. Everything
here exists to turn those into coverage and outcome.
"""
from django.db import models

from .execution import Shipment, ShipmentLine, SupplyNode
from .procurement import SupplierOrg

# SAM prevalence among under-fives by IPC/CH acute food insecurity phase.
# The direction and rough magnitude follow the IPC Acute Malnutrition
# convention (prevalence rises steeply once a population reaches Emergency);
# the exact values are demo figures, not a published table, and every
# CaseloadEstimate row says so in its own source_note.
SAM_PREVALENCE_BY_IPC_PHASE = {
    1: 0.005,
    2: 0.010,
    3: 0.020,
    4: 0.035,
    5: 0.050,
}

# A SAM child is admitted once and consumes one carton across a course of
# treatment lasting roughly this long. Burn rate is therefore driven by the
# admission rate, not by the number of children currently enrolled.
TREATMENT_WEEKS = 7

# Weeks in an average month — used to convert a monthly caseload into the
# weekly admission rate the cover projection runs on.
WEEKS_PER_MONTH = 4.33

# WHO mid-upper-arm-circumference thresholds for children 6-59 months. Below
# 115 mm is severe acute malnutrition; 115-124 mm is moderate; 125 mm and above
# is the discharge-as-recovered boundary. These are the red / yellow / green
# bands a MUAC series is read against.
MUAC_SAM_MAX_MM = 114
MUAC_MAM_MAX_MM = 124
MUAC_RECOVERED_MIN_MM = 125


class CaseloadEstimate(models.Model):
    """Expected severely-malnourished children in one district, in one month.

    The denominator under the map. Joined to
    ``static/supply/geo/admin1_ipc.geojson`` by ``adm1_code``, so the famine
    phase already shading a district and the caseload behind it are the same
    geography rather than two datasets that have to be reconciled.

    Every row carries its own ``source_note``. A caseload figure with no stated
    method is the single easiest number to argue with in a funding meeting, and
    the whole point of this model is to produce figures that survive being
    questioned.
    """

    country = models.CharField(max_length=2)
    adm1_code = models.CharField(max_length=16)
    adm1_name = models.CharField(max_length=160)
    month = models.DateField(help_text="First day of the month this estimate covers.")
    ipc_phase = models.PositiveSmallIntegerField()
    under5_population = models.PositiveIntegerField()
    children_sam = models.PositiveIntegerField()
    source_note = models.CharField(max_length=255)

    class Meta:
        db_table = "supply_caseload_estimate"
        ordering = ["country", "adm1_name", "month"]
        constraints = [
            models.UniqueConstraint(fields=["adm1_code", "month"], name="uniq_caseload_per_district_month"),
        ]

    def __str__(self):
        return f"{self.adm1_name} {self.month:%Y-%m}: {self.children_sam} SAM"

    @property
    def weekly_admissions(self):
        """Children admitted per week — the rate a store is drawn down at."""
        return self.children_sam / WEEKS_PER_MONTH


class DistributionPlan(models.Model):
    """One planned distribution day at one site.

    An implementing partner does not plan in shipments; it plans in
    distribution days. A shipment table sorted by arrival date is the
    supplier's view of the world, and rendering inbound supply against *this*
    is the difference between information and usable information.
    """

    site = models.ForeignKey(SupplyNode, on_delete=models.CASCADE, related_name="distribution_plans")
    org = models.ForeignKey(SupplierOrg, on_delete=models.CASCADE, related_name="distribution_plans")
    scheduled_for = models.DateField()
    expected_children = models.PositiveIntegerField()
    cartons_required = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "supply_distribution_plan"
        ordering = ["scheduled_for", "site__name"]
        constraints = [
            models.UniqueConstraint(fields=["site", "scheduled_for"], name="uniq_distribution_per_site_day"),
        ]

    def __str__(self):
        return f"{self.site.name} {self.scheduled_for}"


class ShortfallSignal(models.Model):
    """A partner telling the centre it is going to run short.

    This inverts the direction every monitoring product runs in. A centre that
    invents its own alerts is watching; a centre answering what the people
    holding the cartons reported is coordinating. The ``origin`` marker is what
    lets the command centre show the difference on the row.
    """

    class Status(models.TextChoices):
        OPEN = "open"
        ACKNOWLEDGED = "acknowledged"
        RESOLVED = "resolved"

    site = models.ForeignKey(SupplyNode, on_delete=models.CASCADE, related_name="shortfall_signals")
    org = models.ForeignKey(SupplierOrg, on_delete=models.CASCADE, related_name="shortfall_signals")
    raised_on = models.DateField()
    needed_by = models.DateField()
    children_affected = models.PositiveIntegerField()
    cartons_short = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    resolved_by_action = models.ForeignKey(
        "supply.SupplyAction", null=True, blank=True, on_delete=models.SET_NULL, related_name="resolves_signals"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_shortfall_signal"
        ordering = ["-raised_on", "-id"]

    def __str__(self):
        return f"{self.site.name} short {self.cartons_short} by {self.needed_by}"


class SupplyAction(models.Model):
    """An append-only record of what the centre decided to do, and why.

    Append-only for the same reason shipment status is derived rather than
    typed: a decision log that can be edited afterwards is a decision log
    nobody can rely on. The rationale is required — an action with no stated
    reason is the thing that cannot be defended six months later, which is
    exactly when it gets asked about.
    """

    class Kind(models.TextChoices):
        EXPEDITE = "expedite"
        REALLOCATE = "reallocate"
        SPLIT_LOT = "split_lot"
        MINI_TENDER = "mini_tender"

    kind = models.CharField(max_length=24, choices=Kind.choices)
    actor = models.CharField(max_length=160)
    rationale = models.TextField()
    effect = models.CharField(max_length=255, blank=True)
    source_node = models.ForeignKey(
        SupplyNode, null=True, blank=True, on_delete=models.SET_NULL, related_name="actions_from"
    )
    target_node = models.ForeignKey(
        SupplyNode, null=True, blank=True, on_delete=models.SET_NULL, related_name="actions_to"
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    shipment = models.ForeignKey(
        Shipment, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_by_actions"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_action"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} by {self.actor}"

    def save(self, *args, **kwargs):
        """Append-only: an action can be created, never rewritten.

        Enforced here rather than by convention because the credibility of the
        command centre rests on it — the same argument that makes shipment
        status derived instead of editable.
        """
        if self.pk is not None:
            raise ValueError("SupplyAction is append-only; records cannot be modified once created.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("SupplyAction is append-only; records cannot be deleted.")


class DistributionRecord(models.Model):
    """What a site actually handed out, traced back to the batch it came from.

    The join the sector describes and does not achieve: this row carries a
    ``batch_lot`` that resolves to a real :class:`~.execution.ShipmentLine`, so
    a delivered pallet can be followed forward to the children it fed rather
    than stopping at the warehouse door.
    """

    plan = models.ForeignKey(
        DistributionPlan, null=True, blank=True, on_delete=models.SET_NULL, related_name="records"
    )
    site = models.ForeignKey(SupplyNode, on_delete=models.CASCADE, related_name="distribution_records")
    org = models.ForeignKey(SupplierOrg, on_delete=models.CASCADE, related_name="distribution_records")
    distributed_on = models.DateField()
    cartons_dispensed = models.DecimalField(max_digits=12, decimal_places=2)
    children_served = models.PositiveIntegerField()
    batch_lot = models.CharField(max_length=32)
    shipment_line = models.ForeignKey(
        ShipmentLine, null=True, blank=True, on_delete=models.SET_NULL, related_name="distribution_records"
    )

    class Meta:
        db_table = "supply_distribution_record"
        ordering = ["-distributed_on", "site__name"]

    def __str__(self):
        return f"{self.site.name} {self.distributed_on} ({self.batch_lot})"


class ChildOutcome(models.Model):
    """One anonymised child's treatment episode, linked to the batch that fed it.

    Synthetic, and labelled synthetic on every surface that renders it. The
    point is not the data — it is that both ends of this chain already exist in
    reality (a batch identity in the despatch advice, a MUAC series in the
    treating organisation's own records) and nothing currently holds them as
    one record.

    Discharge outcomes are seeded against the Sphere / SMART performance
    thresholds for SAM treatment programmes — recovery above 75%, death below
    10%, defaulting below 15% — so the gap between courses delivered and
    recoveries recorded reads as a normally-performing programme rather than as
    a broken one or a rounding error.
    """

    class Discharge(models.TextChoices):
        RECOVERED = "recovered"
        DEFAULTED = "defaulted"
        # The outcome whose absence can only ever flatter the recovery share.
        #
        # Sphere grades a SAM programme on three rates and the death rate is one
        # of them, so a discharge table without the category cannot be checked
        # against the standard it cites. It renders at zero rather than being
        # omitted: "no deaths recorded" is a finding, whereas a missing row is
        # indistinguishable from a programme that does not count them.
        DIED = "died"
        TRANSFERRED = "transferred", "Transferred to inpatient care"
        NON_RESPONSE = "non_response"
        IN_TREATMENT = "in_treatment"

    anon_id = models.CharField(max_length=32, unique=True)
    site = models.ForeignKey(SupplyNode, on_delete=models.CASCADE, related_name="child_outcomes")
    org = models.ForeignKey(SupplierOrg, on_delete=models.CASCADE, related_name="child_outcomes")
    batch_lot = models.CharField(max_length=32)
    distribution_record = models.ForeignKey(
        DistributionRecord, null=True, blank=True, on_delete=models.SET_NULL, related_name="child_outcomes"
    )
    admitted_on = models.DateField()
    admission_muac_mm = models.PositiveSmallIntegerField()
    # [{"date": "2026-05-04", "muac_mm": 112}, ...] — one entry per visit
    measurements = models.JSONField(default=list)
    discharge_status = models.CharField(max_length=16, choices=Discharge.choices, default=Discharge.IN_TREATMENT)
    discharged_on = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "supply_child_outcome"
        ordering = ["site__name", "admitted_on"]

    def __str__(self):
        return f"{self.anon_id} ({self.get_discharge_status_display()})"

    @property
    def latest_muac_mm(self):
        if self.measurements:
            return self.measurements[-1].get("muac_mm")
        return self.admission_muac_mm
