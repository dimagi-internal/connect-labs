from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class OrganizationConfig(AppConfig):
    name = "connect_labs.organization"
    verbose_name = _("Organization")
