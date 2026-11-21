from django.apps import AppConfig


class MessagingConfig(AppConfig):
    name = "connect_labs.messaging"
    label = "messaging"
    verbose_name = "Messaging"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # The `messaging.settings` namespace (labs Settings), and the handlers labs ships.
        from connect_labs.messaging import config  # noqa: F401
        from connect_labs.messaging import handlers

        handlers.register_builtin()
