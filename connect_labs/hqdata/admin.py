from django.contrib import admin

from connect_labs.hqdata.models import HQProject, HQSource, HQSyncRun


class HQSourceInline(admin.TabularInline):
    model = HQSource
    extra = 0
    fields = ("kind", "app_id", "xmlns", "case_type", "enabled", "watermark", "last_error")
    readonly_fields = ("watermark", "last_error")


@admin.register(HQProject)
class HQProjectAdmin(admin.ModelAdmin):
    list_display = ("domain", "hq_host", "storage_mode", "backend", "status", "last_synced_at")
    list_filter = ("storage_mode", "backend", "status")
    search_fields = ("domain", "name")
    inlines = [HQSourceInline]


@admin.register(HQSyncRun)
class HQSyncRunAdmin(admin.ModelAdmin):
    list_display = ("source", "mode", "status", "fetched", "deleted", "started_at", "finished_at")
    list_filter = ("mode", "status")
    readonly_fields = [f.name for f in HQSyncRun._meta.fields]
