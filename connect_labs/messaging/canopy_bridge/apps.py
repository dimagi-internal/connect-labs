from django.apps import AppConfig


class CanopyBridgeConfig(AppConfig):
    name = "connect_labs.messaging.canopy_bridge"
    label = "canopy_bridge"
    verbose_name = "Messaging: canopy agents"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from connect_labs.messaging import handlers
        from connect_labs.messaging.canopy_bridge import signals
        from connect_labs.messaging.canopy_bridge.handler import CanopyAgentHandler

        signals.connect()
        # The real agent handler, over the stub labs ships. Off (a person takes the thread, saying
        # why) while the workers surface of labs' canopy site is not configured.
        handlers.register("agent", CanopyAgentHandler())
