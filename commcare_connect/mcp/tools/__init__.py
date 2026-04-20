"""Tool handlers for the labs MCP server.

Each submodule registers its tools with the @register decorator at import time.
Importing this package triggers all registration.
"""

from . import funds  # noqa: F401
from . import labs_context  # noqa: F401
from . import pipelines  # noqa: F401
from . import reviews  # noqa: F401
from . import sample_ids  # noqa: F401
from . import solicitations  # noqa: F401
from . import templates  # noqa: F401
from . import workflows  # noqa: F401
