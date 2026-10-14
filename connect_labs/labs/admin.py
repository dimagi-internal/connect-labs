"""Admin registrations for labs-DB models."""

from django.contrib import admin

from connect_labs.labs.models import ConnectOrganization, DeletedWorkflowBackup


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


@admin.register(ConnectOrganization)
class ConnectOrganizationAdmin(admin.ModelAdmin):
    """Connect's organisations as `labs.connect_orgs` last read them.

    A cache, so nothing is editable here: filter on "gone" to see what Connect
    deleted or merged away since labs last linked to it.
    """

    list_display = ("connect_id", "slug", "name", "is_test", "program_manager", "gone_at", "merged_into_id")
    list_filter = ("is_test", "program_manager", "funder", "verified", ("gone_at", admin.EmptyFieldListFilter))
    search_fields = ("connect_id", "slug", "name", "short_name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
