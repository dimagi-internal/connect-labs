from django.apps import AppConfig


class CommunicationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "connect_labs.communications"
    label = "communications"
    verbose_name = "Communications"

    def ready(self):
        # Each feature registers the kinds of message it writes.
        from connect_labs.communications import kinds  # noqa: F401
        from connect_labs.solicitations import communication_kinds  # noqa: F401
        from connect_labs.supply_chain import communication_kinds as supply_kinds  # noqa: F401
