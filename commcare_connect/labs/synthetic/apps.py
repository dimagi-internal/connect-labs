from django.apps import AppConfig


class SyntheticConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "commcare_connect.labs.synthetic"
    verbose_name = "Synthetic Opportunities"

    def ready(self):
        from commcare_connect.labs.synthetic import signals  # noqa: F401
