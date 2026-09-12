from django.apps import AppConfig


class SupplyChainConfig(AppConfig):
    name = "connect_labs.supply_chain"
    label = "supply_chain"

    def ready(self):
        from connect_labs.supply_chain.procurement import operations  # noqa: F401
