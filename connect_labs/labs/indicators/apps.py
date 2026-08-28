from django.apps import AppConfig


class IndicatorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "connect_labs.labs.indicators"
    label = "indicators"
    verbose_name = "Targeting indicators"
