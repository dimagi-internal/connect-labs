"""How each partner distributes, kept as history.

Imported by `supply_chain.models` so Django registers it with the app.
"""

from django.db import models

from connect_labs.supply_chain import records
from connect_labs.supply_chain.models import SourcedModel, TimestampedModel

# How a partner gets stock from its warehouse to its field workers.
DISTRIBUTION_MODELS = ("through_facilities", "direct_to_workers", "mixed")

DISTRIBUTION_MODEL_LABELS = {
    "through_facilities": "Through health facilities (PHCs): field workers collect from a facility",
    "direct_to_workers": "Straight to field workers, who keep the stock at home",
    "mixed": "A mix: some through facilities, some straight to field workers",
}


class PartnerPlan(SourcedModel):
    """One partner's way of distributing, from a day on.

    Kept as history: a change of model is a NEW row that closes the one before it
    on that day, never an edit and never field workers quietly moved. The row with
    no `ended_on` is the current plan. `note` carries the reason (for example, "stopped storing at
    facilities after stock was taken") and what the change did to the network.
    """

    program_id = models.IntegerField(db_index=True)
    org = models.ForeignKey("labs.LabsOrg", on_delete=models.PROTECT, related_name="supply_partner_plans")
    distribution_model = models.CharField(
        max_length=24, choices=[(m, DISTRIBUTION_MODEL_LABELS[m]) for m in DISTRIBUTION_MODELS]
    )
    starts_on = models.DateField(db_index=True)
    ended_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-starts_on", "-id"]
        indexes = [models.Index(fields=["program_id", "org", "starts_on"])]

    def __str__(self):
        return f"{self.org_id}: {self.distribution_model} from {self.starts_on}"

    @property
    def is_current(self) -> bool:
        return self.ended_on is None


class PartnerProfile(TimestampedModel):
    """Who a partner is to us: its main contact, who to copy, how it usually buys and hands out stock.

    One row per partner per programme, edited in place (the current answer is what matters; how it
    distributes is the dated `PartnerPlan`). The main contact is who award emails and handovers are
    addressed to, with `copy_to` copied. `connect_username` is where an alert sent as a Connect
    message goes.
    """

    program_id = models.IntegerField(db_index=True)
    org = models.ForeignKey("labs.LabsOrg", on_delete=models.PROTECT, related_name="supply_partner_profiles")
    contact_name = models.CharField(max_length=255, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    contact_phone = models.CharField(max_length=64, blank=True, default="")
    contact_connect_username = models.CharField(max_length=150, blank=True, default="")
    # People to copy on what is sent to the partner: [{"name": ..., "email": ...}].
    copy_to = models.JSONField(default=list, blank=True)
    # records.BUYING_MODELS: the way this partner is bought for unless an order says otherwise.
    default_buying_model = models.CharField(
        max_length=16, blank=True, default="", choices=[(m, m) for m in records.BUYING_MODELS]
    )
    # How often the partner hands out stock (days between handouts), and the place it hands out from.
    handout_every_days = models.PositiveSmallIntegerField(null=True, blank=True)
    handout_from = models.ForeignKey(
        "supply_chain.SupplyPoint", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["program_id", "org"], name="uniq_partner_profile")]

    def __str__(self):
        return f"{self.org_id}: {self.contact_name or 'no contact'}"
