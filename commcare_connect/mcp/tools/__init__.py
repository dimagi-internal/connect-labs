"""Tool handlers for the labs MCP server.

Each submodule registers its tools with the @register decorator at import time.
Importing this package triggers all registration.
"""

from . import apps  # noqa: F401
from . import custom_analysis_run  # noqa: F401  -- registers custom_analysis_run
from . import funds  # noqa: F401
from . import labs_context  # noqa: F401
from . import mbw_parity  # noqa: F401
from . import microplans  # noqa: F401  -- registers microplans_list_plans, microplans_plan_work_areas
from . import microplans_study  # noqa: F401  -- registers microplans_study_ensure, microplans_study_reset_round
from . import pipelines  # noqa: F401
from . import program_admin_demo  # noqa: F401  -- registers program_admin_demo_seed
from . import reviews  # noqa: F401
from . import sample_ids  # noqa: F401
from . import solicitations  # noqa: F401
from . import synthetic_tasks  # noqa: F401  -- registers task_create_synthetic
from . import templates  # noqa: F401
from . import workflow_create_run  # noqa: F401  -- registers workflow_create_run
from . import workflow_snapshots  # noqa: F401  -- registers workflow_save_snapshot
from . import workflow_template_sync  # noqa: F401
from . import workflows  # noqa: F401
from . import (  # noqa: F401  -- registers synthetic_register, synthetic_disable, synthetic_generate_from_manifest
    synthetic,
)
