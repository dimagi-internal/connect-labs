from django.apps import AppConfig


class CanopyBridgeConfig(AppConfig):
    name = "connect_labs.messaging.canopy_bridge"
    label = "canopy_bridge"
    verbose_name = "Messaging: canopy agents"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from connect_labs.messaging.canopy_bridge import signals

        signals.connect()
