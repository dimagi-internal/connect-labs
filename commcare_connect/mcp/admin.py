from django.contrib import admin

from .models import MCPAccessToken, MCPAuditLog


@admin.register(MCPAccessToken)
class MCPAccessTokenAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "created_at", "last_used_at", "expires_at", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "user__username", "user__email")
    readonly_fields = ("token_hash", "created_at", "last_used_at")
    actions = ["revoke_tokens"]

    def has_add_permission(self, request):
        # Tokens are created via the management command or the UI (future).
        # Admin can view and revoke, but not create — the raw token would
        # be unrecoverable after admin save.
        return False

    @admin.action(description="Revoke selected tokens")
    def revoke_tokens(self, request, queryset):
        queryset.update(is_active=False)


@admin.register(MCPAuditLog)
class MCPAuditLogAdmin(admin.ModelAdmin):
    list_display = ("tool_name", "user", "success", "error_code", "created_at")
    list_filter = ("tool_name", "success", "is_write")
    search_fields = ("user__username", "tool_name", "error_code")
    readonly_fields = tuple(f.name for f in MCPAuditLog._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
