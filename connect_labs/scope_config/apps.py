from django.apps import AppConfig


class ScopeConfigConfig(AppConfig):
    name = "connect_labs.scope_config"
    label = "scope_config"
    verbose_name = "Settings"

    def ready(self):
        # The namespace labs itself owns (the `home` slot). Apps register their own
        # namespaces from their own ready().
        from connect_labs.scope_config import builtin  # noqa: F401
