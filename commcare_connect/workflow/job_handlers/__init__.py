"""
Workflow job handlers.

Each module registers handlers via @register_job_handler decorator.
Import all handler modules here so they register on app startup.
"""

from commcare_connect.workflow.job_handlers import mbw_auditing_v4  # noqa: F401
from commcare_connect.workflow.job_handlers import mbw_monitoring  # noqa: F401
