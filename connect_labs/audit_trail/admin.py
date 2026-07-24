from django.contrib import admin

from connect_labs.audit_trail.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """Read-only — the table is append-only by trigger; admin mirrors that."""

    list_display = (
        "occurred_at",
        "username",
        "action",
        "resource_type",
        "resource_id",
        "record_count",
        "opportunity_id",
        "labs_only",
        "outcome",
        "source",
    )
    list_filter = ("action", "outcome", "source", "labs_only")
    search_fields = ("username", "resource_type", "resource_id", "request_id", "path")
    date_hierarchy = "occurred_at"
    readonly_fields = tuple(f.name for f in AuditEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
