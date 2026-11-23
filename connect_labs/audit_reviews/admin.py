from django.contrib import admin

from connect_labs.audit_reviews.models import AuditSource, Decision, Review, SourceRun, TeamEvent


@admin.register(AuditSource)
class AuditSourceAdmin(admin.ModelAdmin):
    list_display = ("workflow_id", "source_workflow_id", "opportunity_id", "go_live_run_id", "mode", "last_synced_at")


@admin.register(SourceRun)
class SourceRunAdmin(admin.ModelAdmin):
    list_display = ("source", "run_id", "period_start", "period_end", "completed_at", "ingested_at")
    exclude = ("data",)


@admin.register(Decision)
class DecisionAdmin(admin.ModelAdmin):
    list_display = ("source", "kind", "run_id", "entity", "review_number", "status", "decided_by", "decided_at")
    list_filter = ("kind",)


@admin.register(TeamEvent)
class TeamEventAdmin(admin.ModelAdmin):
    list_display = ("source", "review_key", "type", "created_by", "created_at")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("source", "number", "entity", "status", "opened_run_id", "closed_at")
    list_filter = ("status", "entity_type")
