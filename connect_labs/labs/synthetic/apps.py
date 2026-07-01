from django.apps import AppConfig


class SyntheticConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "connect_labs.labs.synthetic"
    verbose_name = "Synthetic Opportunities"

    def ready(self):
        from connect_labs.labs.synthetic import signals  # noqa: F401
