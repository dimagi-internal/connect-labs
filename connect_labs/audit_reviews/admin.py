from django.contrib import admin

from connect_labs.audit_reviews.models import AuditSource, Decision, Review, SettingsVersion, SourceRun, TeamEvent


class ReadOnlyAdmin(admin.ModelAdmin):
    """Decisions and events go through the service, which checks them against the rules; never here."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditSource)
class AuditSourceAdmin(admin.ModelAdmin):
    list_display = ("workflow_id", "source_workflow_id", "opportunity_id", "go_live_run_id", "mode", "last_synced_at")


@admin.register(SourceRun)
class SourceRunAdmin(ReadOnlyAdmin):
    list_display = ("source", "run_id", "period_start", "period_end", "completed_at", "ingested_at")
    exclude = ("data",)


@admin.register(Decision)
class DecisionAdmin(ReadOnlyAdmin):
    list_display = ("source", "kind", "run_id", "entity", "review_number", "status", "decided_by", "decided_at")
    list_filter = ("kind",)


@admin.register(TeamEvent)
class TeamEventAdmin(ReadOnlyAdmin):
    list_display = ("source", "review_key", "type", "created_by", "created_at")


@admin.register(Review)
class ReviewAdmin(ReadOnlyAdmin):
    list_display = ("source", "number", "entity", "status", "opened_run_id", "closed_at")
    list_filter = ("status", "entity_type")


@admin.register(SettingsVersion)
class SettingsVersionAdmin(ReadOnlyAdmin):
    list_display = ("source", "number", "note", "saved_by", "saved_at")
