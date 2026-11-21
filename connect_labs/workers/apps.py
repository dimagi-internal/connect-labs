from django.apps import AppConfig


class WorkersConfig(AppConfig):
    name = "connect_labs.workers"
    label = "workers"
    verbose_name = "Connect Workers"
    default_auto_field = "django.db.models.BigAutoField"
