from django.apps import AppConfig


class EmbedConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "connect_labs.embed"
    verbose_name = "Embedding labs in other systems"
