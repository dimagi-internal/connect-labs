"""A workflow pinned into a programme's supply navigation.

A pinned workflow appears as a supply tab and opens inside the supply header,
so a view built on supply sources (workflow/supply_sources.py) sits in the flow
of the supply pages instead of on a page of its own. It either ADDS a tab or
REPLACES a built-in one (`replaces`, a supply tab's view name); a replaced tab's
own page stays reachable from the pinned one ("classic view").

Per programme: everyone in the programme sees the same tabs. Access is not
widened -- the workflow still loads as the viewer, with its own access rules.

Imported by `supply_chain.models` so Django registers it with the app.
"""

from django.db import models

from connect_labs.supply_chain.models import TimestampedModel


class SupplyWorkflowView(TimestampedModel):
    program_id = models.IntegerField(db_index=True)
    slug = models.SlugField(max_length=80)
    label = models.CharField(max_length=80)
    workflow_definition_id = models.IntegerField()
    # The opportunity the workflow's runs are scoped to (its own primary opportunity).
    opportunity_id = models.IntegerField(null=True, blank=True)
    # A built-in supply tab this one stands in for ("supply_chain:workers"), or "" to add a tab.
    replaces = models.CharField(max_length=80, blank=True, default="")
    position = models.IntegerField(default=0)
    created_by = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        ordering = ["program_id", "position", "id"]
        constraints = [models.UniqueConstraint(fields=["program_id", "slug"], name="uniq_supply_workflow_view_slug")]

    def __str__(self):
        return f"{self.label} (programme {self.program_id}, workflow {self.workflow_definition_id})"
