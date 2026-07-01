"""
Workflow job handlers.

Each module registers handlers via @register_job_handler decorator.
Import all handler modules here so they register on app startup.
"""

from connect_labs.workflow.job_handlers import audit_par  # noqa: F401
from connect_labs.workflow.job_handlers import mbw_monitoring  # noqa: F401
from connect_labs.workflow.job_handlers import program_admin_report  # noqa: F401
from connect_labs.workflow.job_handlers import program_audit_creator  # noqa: F401
from connect_labs.workflow.job_handlers import weekly_dual_track_audit  # noqa: F401
