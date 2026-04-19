"""Tool handlers for the labs MCP server.

Each submodule registers its tools with the @register decorator at import time.
Importing this package triggers all registration.
"""

from . import workflows  # noqa: F401
