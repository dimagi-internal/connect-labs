"""Feature access registry for gating Labs features by user type.

To grant external users access to more features, add to EXTERNAL_USER_FEATURES.
To grant external users access to more workflow templates, add to EXTERNAL_USER_WORKFLOW_TEMPLATES.
"""

from commcare_connect.utils.dimagi_user import is_dimagi_user

# Features accessible to external (non-Dimagi) users.
# Dimagi users always have access to all features.
EXTERNAL_USER_FEATURES = {"tasks", "workflow"}

# Workflow templates accessible to external users.
# Dimagi users always see all templates.
EXTERNAL_USER_WORKFLOW_TEMPLATES = {"bulk_image_audit"}


def user_has_feature_access(user, feature_key: str) -> bool:
    """Check if a user has access to a given feature.

    Dimagi users have access to everything. External users are
    restricted to features listed in EXTERNAL_USER_FEATURES.
    """
    if is_dimagi_user(user):
        return True
    return feature_key in EXTERNAL_USER_FEATURES


def get_allowed_templates(user) -> list[dict]:
    """Return the workflow templates a user is allowed to create from.

    Dimagi users see all templates. External users only see templates
    listed in EXTERNAL_USER_WORKFLOW_TEMPLATES.
    """
    from commcare_connect.workflow.templates import list_templates

    all_templates = list_templates()
    if is_dimagi_user(user):
        return all_templates
    return [t for t in all_templates if t["key"] in EXTERNAL_USER_WORKFLOW_TEMPLATES]


def can_create_from_template(user, template_key: str) -> bool:
    """Check if a user is allowed to create a workflow from a specific template."""
    if is_dimagi_user(user):
        return True
    return template_key in EXTERNAL_USER_WORKFLOW_TEMPLATES
