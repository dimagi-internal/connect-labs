from django.apps import AppConfig


class MCPConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "commcare_connect.mcp"
    verbose_name = "Labs MCP Server"
