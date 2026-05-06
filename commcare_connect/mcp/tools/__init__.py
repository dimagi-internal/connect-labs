"""Tool handlers for the labs MCP server.

Each submodule registers its tools with the @register decorator at import time.
Importing this package triggers all registration.
"""

from . import apps  # noqa: F401
from . import funds  # noqa: F401
from . import labs_context  # noqa: F401
from . import mbw_parity  # noqa: F401
from . import pipelines  # noqa: F401
from . import reviews  # noqa: F401
from . import sample_ids  # noqa: F401
from . import solicitations  # noqa: F401
from . import synthetic_tasks  # noqa: F401  -- registers task_create_synthetic
from . import templates  # noqa: F401
from . import workflow_snapshots  # noqa: F401  -- registers workflow_save_snapshot
from . import workflows  # noqa: F401
from . import (  # noqa: F401  -- registers synthetic_register, synthetic_disable, synthetic_generate_from_manifest
    synthetic,
)
