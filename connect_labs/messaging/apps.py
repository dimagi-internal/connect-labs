from django.apps import AppConfig


class MessagingConfig(AppConfig):
    name = "connect_labs.messaging"
    label = "messaging"
    verbose_name = "Messaging"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Settings namespaces (`messaging.settings`, `messaging.playbooks`), the handlers labs
        # ships (an app installed later may replace one, e.g. the canopy bridge's `agent`), the
        # router on the inbound path.
        from connect_labs.messaging import config, handlers, playbooks, router  # noqa: F401

        handlers.register_builtin()
        router.install()
