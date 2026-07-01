"""Admin registrations for labs-DB models."""

from django.contrib import admin

from commcare_connect.labs.models import DeletedWorkflowBackup


@admin.register(DeletedWorkflowBackup)
class DeletedWorkflowBackupAdmin(admin.ModelAdmin):
    """Read-oriented view of deleted-workflow safety copies.

    Lets a human find a deleted workflow and read back its definition JSON +
    render-code JSX to reconstruct it via the API. Fields are read-only — a
    backup is a snapshot, not something to edit — but rows may be deleted to
    prune old backups.
    """

    list_display = ("definition_id", "opportunity_id", "name", "template_type", "deleted_by", "deleted_at")
    list_filter = ("template_type", "deleted_at")
    search_fields = ("definition_id", "opportunity_id", "name", "deleted_by")
    ordering = ("-id",)
    readonly_fields = (
        "definition_id",
        "opportunity_id",
        "name",
        "template_type",
        "definition_data",
        "render_code",
        "deleted_by",
        "deleted_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
