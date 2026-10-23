from django.contrib import admin, messages
from django.utils import timezone

from connect_labs.embed.models import EmbedClient, EmbedSession


@admin.register(EmbedClient)
class EmbedClientAdmin(admin.ModelAdmin):
    list_display = ("client_id", "name", "active", "identity_authority", "updated_at")
    list_filter = ("active", "identity_authority")
    search_fields = ("client_id", "name")
    readonly_fields = ("created_at", "updated_at")

    # Who may frame labs, and who may say who people are, is a superuser's call alone.
    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(EmbedSession)
class EmbedSessionAdmin(admin.ModelAdmin):
    list_display = ("client", "user", "kind", "resource_id", "created_at", "started_at", "expires_at", "revoked_at")
    list_filter = ("client", "kind")
    search_fields = ("user__username", "resource_id")
    readonly_fields = [f.name for f in EmbedSession._meta.fields]
    actions = ("revoke",)

    def has_add_permission(self, request):
        return False

    @admin.action(description="End these embed sessions now")
    def revoke(self, request, queryset):
        count = queryset.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
        self.message_user(request, f"Ended {count} session(s).", messages.SUCCESS)
