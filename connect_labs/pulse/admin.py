"""Admin for the cost data labs adds on top of Connect's.

`PulseInvoice` is read-only here: it is Connect's record, mirrored as-is, and an
edit would be overwritten by the next sync. A decision about an invoice goes on
`PulseInvoiceReview`; a cost Connect never saw goes on `PulseCostEntry`.
"""

from django.contrib import admin

from connect_labs.pulse.models import PulseCostEntry, PulseInvoice, PulseInvoiceReview


@admin.register(PulseInvoice)
class PulseInvoiceAdmin(admin.ModelAdmin):
    list_display = ("opportunity_id", "invoice_number", "service_delivery", "amount", "amount_usd", "date")
    list_filter = ("service_delivery",)
    search_fields = ("opportunity_id", "invoice_number")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PulseInvoiceReview)
class PulseInvoiceReviewAdmin(admin.ModelAdmin):
    list_display = ("opportunity_id", "invoice_number", "usd_override", "exclude", "decided_by", "updated_at")
    search_fields = ("opportunity_id", "invoice_number", "reason")


@admin.register(PulseCostEntry)
class PulseCostEntryAdmin(admin.ModelAdmin):
    list_display = ("opportunity_id", "kind", "usd", "date", "source", "entered_by", "created_at")
    list_filter = ("kind",)
    search_fields = ("opportunity_id", "reason", "source")
