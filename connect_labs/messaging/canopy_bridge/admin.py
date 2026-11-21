from django.contrib import admin

from .models import CanopySession, Erasure, PendingTurn, ProfilePush, WorkerCommitment


@admin.register(ProfilePush)
class ProfilePushAdmin(admin.ModelAdmin):
    list_display = ("worker", "status", "pushed_version", "pushed_at", "dirty", "attempts")
    list_filter = ("status", "dirty")
    search_fields = ("worker__username",)


@admin.register(CanopySession)
class CanopySessionAdmin(admin.ModelAdmin):
    list_display = ("thread", "session_id", "agent", "skill", "opened_at", "ended_at", "end_reason")
    search_fields = ("session_id", "thread__worker__username")


@admin.register(PendingTurn)
class PendingTurnAdmin(admin.ModelAdmin):
    list_display = ("turn_id", "thread", "status", "sent_at", "polls", "finished_at", "detail")
    list_filter = ("status",)
    search_fields = ("turn_id",)


@admin.register(WorkerCommitment)
class WorkerCommitmentAdmin(admin.ModelAdmin):
    list_display = ("worker", "program_id", "text", "status", "created_at")
    list_filter = ("status",)


@admin.register(Erasure)
class ErasureAdmin(admin.ModelAdmin):
    list_display = ("username", "requested_by", "requested_at", "canopy_status", "canopy_done_at", "attempts")
    list_filter = ("canopy_status",)
    readonly_fields = [f.name for f in Erasure._meta.fields]
