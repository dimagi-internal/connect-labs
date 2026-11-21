from django.contrib import admin

from .models import ConnectWorker, StaffNote, WorkerEvent, WorkerFact, WorkerMembership, WorkerState


@admin.register(ConnectWorker)
class ConnectWorkerAdmin(admin.ModelAdmin):
    list_display = ("username", "name", "profile_synced_at", "erased_at")
    search_fields = ("username", "name")
    exclude = ("phone",)
    actions = ["erase_workers"]

    @admin.action(description="Erase from labs and canopy (Dimagi staff; cannot be undone)")
    def erase_workers(self, request, queryset):
        from connect_labs.messaging.canopy_bridge.erasure import erase_worker
        from connect_labs.utils.dimagi_user import is_dimagi_staff

        if not is_dimagi_staff(request.user):
            self.message_user(request, "Only Dimagi staff may erase a Connect Worker.", level="error")
            return
        who = getattr(request.user, "username", "") or getattr(request.user, "email", "")
        erased = [erase_worker(w, by=f"admin:{who}") for w in queryset.filter(erased_at__isnull=True)]
        self.message_user(request, f"Erased {len(erased)} Connect Worker(s); canopy is being told to forget them.")


@admin.register(WorkerMembership)
class WorkerMembershipAdmin(admin.ModelAdmin):
    list_display = ("worker", "opportunity_id", "program_id", "user_invite_status", "suspended", "last_active")
    list_filter = ("suspended", "visible")
    search_fields = ("worker__username",)
    exclude = ("suspension_reason",)


@admin.register(WorkerState)
class WorkerStateAdmin(admin.ModelAdmin):
    list_display = ("membership", "stage", "computed_at")
    list_filter = ("stage",)


@admin.register(WorkerEvent)
class WorkerEventAdmin(admin.ModelAdmin):
    list_display = ("worker", "kind", "opportunity_id", "occurred_at", "observed_at", "source", "confidence")
    list_filter = ("kind", "source", "confidence", "visibility")
    search_fields = ("worker__username",)


@admin.register(WorkerFact)
class WorkerFactAdmin(admin.ModelAdmin):
    list_display = ("worker", "kind", "statement", "status", "asserted_by_kind", "created_at")
    list_filter = ("kind", "status", "asserted_by_kind")


@admin.register(StaffNote)
class StaffNoteAdmin(admin.ModelAdmin):
    list_display = ("worker", "program_id", "private", "author", "created_at")
    exclude = ("body",)
