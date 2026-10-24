from django.apps import AppConfig


class LabsDataConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "connect_labs.labsdata"
    label = "labsdata"
    verbose_name = "Labs data (the Data page)"
