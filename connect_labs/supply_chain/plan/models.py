"""The stock plan: the three numbers a partner may change, saved as the programme's plan.

One row per (programme, item). Everything else on the planning pages is computed
from the visits and the ledger; only these choices are stored, so a change to one
is a `Revision` that says who made it and what it was before (history/capture.py).
"""

from django.db import models

from connect_labs.supply_chain.models import Item, TimestampedModel


class StockPlan(TimestampedModel):
    program_id = models.IntegerField(db_index=True)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="stock_plans")
    # New children each field worker is expected to enrol a week. Null is the default: each
    # worker's own recent rate, measured from their last weeks of enrolment.
    enrolment_per_flw_per_week = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    # Extra days of stock to hand out beyond the week, for every worker and facility...
    buffer_days = models.PositiveSmallIntegerField(default=0)
    # ...unless one is named here: {"<supply point id>": days}, a field worker or a facility.
    buffer_days_by_point = models.JSONField(default=dict, blank=True)
    # How many children the programme means to treat in all (500 now, 2,500 in phase 3).
    target_cases = models.PositiveIntegerField(default=500)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["program_id", "item"], name="uniq_stock_plan_program_item")]

    def __str__(self):
        return f"stock plan: {self.item} in programme {self.program_id}"
