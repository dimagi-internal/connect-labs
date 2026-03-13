"""
Helper functions for tasks using ExperimentRecord-based TaskRecord model.

Simplified helpers that use TaskDataAccess instead of Django ORM.
OAuth API is the source of truth for permissions - no local checks needed.
"""

from commcare_connect.tasks.data_access import TaskDataAccess


def get_user_tasks_queryset(user):
    """
    Get filtered queryset of tasks user can access via OAuth.

    Args:
        user: Django User instance

    Returns:
        QuerySet of TaskRecord instances (OAuth enforces access)
    """
    data_access = TaskDataAccess(user=user)
    return data_access.get_tasks()


def create_task_from_audit(
    audit_session_id: int,
    username: str,
    opportunity_id: int,
    description: str,
    creator_name: str = "System",
    **kwargs,
):
    """
    Create task from audit trigger.

    This is a clean API for future automation when audit failures
    automatically create tasks.

    Args:
        audit_session_id: ID of the audit session that triggered this task
        username: The FLW username this task is about
        opportunity_id: The opportunity ID this task relates to
        description: Description of what happened
        creator_name: Name of creator (or "System" for automated tasks)
        **kwargs: Additional fields (priority, title, status, assigned_to_type, assigned_to_name)

    Returns:
        The created TaskRecord instance
    """
    data_access = TaskDataAccess()

    return data_access.create_task(
        username=username,
        opportunity_id=opportunity_id,
        description=description,
        audit_session_id=audit_session_id,
        title=kwargs.get("title", f"Task for {username}"),
        priority=kwargs.get("priority", "medium"),
        status=kwargs.get("status", "investigating"),
        creator_name=creator_name,
        assigned_to_type=kwargs.get("assigned_to_type", "self"),
        assigned_to_name=kwargs.get("assigned_to_name", creator_name),
    )
