"""
Proxy models for funder_dashboard.

FundRecord extends LocalLabsRecord with typed @property access
to JSON data stored via the LabsRecord API. Cannot be .save()d locally.
"""
from commcare_connect.labs.models import LocalLabsRecord


class FundRecord(LocalLabsRecord):
    """Proxy model for fund records. Scoped by org_id."""

    @property
    def name(self):
        return self.data.get("name", "")

    @property
    def description(self):
        return self.data.get("description", "")

    @property
    def total_budget(self):
        return self.data.get("total_budget")

    @property
    def currency(self):
        return self.data.get("currency", "USD")

    @property
    def org_id(self):
        return self.data.get("org_id", "")

    @property
    def program_ids(self):
        return self.data.get("program_ids", [])

    @property
    def delivery_types(self):
        return self.data.get("delivery_types", [])

    @property
    def status(self):
        return self.data.get("status", "active")

    @property
    def allocations(self):
        return self.data.get("allocations", [])

    @property
    def committed_amount(self):
        return sum(a.get("amount", 0) for a in self.allocations)

    @property
    def remaining_amount(self):
        budget = self.total_budget or 0
        return max(0, budget - self.committed_amount)
