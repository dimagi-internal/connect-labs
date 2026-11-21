from django.contrib import admin

from .models import Address, Message, Thread, ThreadSummary, TransportEvent


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("worker", "transport", "channel_source", "program_id", "consent", "opted_out_at", "created_at")
    list_filter = ("transport", "purpose", "consent")
    search_fields = ("worker__username", "channel_id")
    exclude = ("key",)  # a channel key is never shown


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = ("pk", "worker", "program_id", "playbook_key", "handler_kind", "state", "needs_attention")
    list_filter = ("state", "handler_kind", "direction", "needs_attention")
    search_fields = ("worker__username", "playbook_key")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("pk", "thread", "direction", "author_kind", "delivery_status", "created_at")
    list_filter = ("direction", "author_kind", "delivery_status")
    search_fields = ("worker__username", "transport_message_id")
    exclude = ("text",)  # read a conversation in the Inbox, where access is checked


@admin.register(ThreadSummary)
class ThreadSummaryAdmin(admin.ModelAdmin):
    list_display = ("pk", "thread", "author_kind", "outcome", "created_at")
    exclude = ("text",)


@admin.register(TransportEvent)
class TransportEventAdmin(admin.ModelAdmin):
    list_display = ("pk", "kind", "channel_id", "received_at", "processed_at", "attempts", "error")
    list_filter = ("kind",)
    exclude = ("payload",)
