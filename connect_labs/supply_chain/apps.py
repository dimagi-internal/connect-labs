from django.apps import AppConfig


class SupplyChainConfig(AppConfig):
    name = "connect_labs.supply_chain"
    label = "supply_chain"

    def ready(self):
        # Import for side effect: each module registers its operations into
        # the single registry, and the HTTP API and MCP server are both
        # generated from that. A tier not imported here is a tier with no API.
        from connect_labs.supply_chain.fulfilment import operations as fulfilment_operations  # noqa: F401
        from connect_labs.supply_chain.procurement import operations  # noqa: F401
        from connect_labs.supply_chain.stock import operations as stock_operations  # noqa: F401
