from django.apps import AppConfig


class MCPConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "connect_labs.mcp"
    verbose_name = "Labs MCP Server"

    def ready(self):
        # Import the tools package so each submodule's @register decorator runs.
        # Without this, registration only happened transitively when views.py was
        # imported by URL resolution — leaving test_solicitation_tools and friends
        # to fail when run in isolation (no view ever imported, registry empty).
        from . import tools  # noqa: F401
