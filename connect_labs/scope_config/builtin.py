"""The `labs` namespace: set-up that belongs to labs itself rather than to one app.

`home` is the page a scope opens on: `/labs/p/org/<slug>/` with no page slug, and
an organisation coming in (spec, "An organisation coming in"). It names a page
definition AND the scope that owns that definition, because the two differ: a
labs-only organisation has no Connect id to own records with, so its home is a
page owned by one of its programmes, rendered with the organisation as its scope.

Whoever sets a home must be able to use its owner scope (`owners` below): the page
is read there for every viewer, so naming a scope you cannot use would hand its
records to everyone who opens yours. And it must name a PAGE (`_home_is_a_page`): a
home is drawn as a page, so a workflow named there is refused here, and refused again
when the home is opened (workflow/page_views.py), since a definition's kind can change
after it was chosen.
"""

import logging

from connect_labs.scope_config.namespaces import Namespace, register_namespace
from connect_labs.scope_config.scopes import Scope

logger = logging.getLogger(__name__)

OWNER_KEYS = ("organization_id", "program_id", "opportunity_id")

FILL = {
    "type": "object",
    "properties": {
        "workflow": {"type": "integer", "minimum": 1},
        # An organisation is named by its slug (rows and addresses are keyed by slug).
        "organization_id": {"type": "string", "pattern": "^(?![0-9]+$)[a-z0-9-]{1,200}$"},
        "program_id": {"type": "integer", "minimum": 1},
        "opportunity_id": {"type": "integer", "minimum": 1},
    },
    "required": ["workflow"],
    "additionalProperties": False,
}


def fill_owner(fill: dict) -> Scope | None:
    """The scope a page fill's definition is owned by (and read in)."""
    for key, type_ in (
        ("opportunity_id", "opportunity"),
        ("program_id", "program"),
        ("organization_id", "organization"),
    ):
        if (fill or {}).get(key) not in (None, ""):
            return Scope.of(type_, fill[key])
    return None


def _one_owner(data: dict) -> None:
    fill = (data.get("home") or {}).get("fill")
    if fill:
        owners = [k for k in OWNER_KEYS if fill.get(k) not in (None, "")]
        if len(owners) != 1:
            raise ValueError(
                "home.fill names its page's owner with exactly one of organization_id, program_id or opportunity_id"
            )


def _owners(data: dict) -> list:
    owner = fill_owner((data.get("home") or {}).get("fill") or {})
    return [owner] if owner else []


def _read_definition(caller, owner: Scope, definition_id: int):
    """The definition, read where its owner keeps it, as the person making the change."""
    from connect_labs.labs.access.scopes import _user_of
    from connect_labs.workflow.data_access import WorkflowDataAccess

    access = WorkflowDataAccess(
        **{f"{owner.type}_id": int(owner.key)},
        user=_user_of(caller),
        request=getattr(caller, "request", None),
        access_token=getattr(caller, "access_token", None),
    )
    try:
        return access.get_definition(definition_id)
    finally:
        access.close()


def _home_is_a_page(before: dict, after: dict, caller) -> None:
    """A home names a page, not a workflow with runs.

    Checked when the home's fill changes, by reading the definition as the person
    setting it. A definition that cannot be read here (an organisation-owned one, or
    Connect unreachable) is let through: the home is checked again, as every viewer,
    each time it is opened, and a home that is not a page is not drawn.
    """
    fill = (after.get("home") or {}).get("fill") or {}
    if not fill or fill == ((before.get("home") or {}).get("fill") or {}):
        return
    owner = fill_owner(fill)
    if owner is None or owner.type == "organization":
        return
    try:
        definition = _read_definition(caller, owner, int(fill["workflow"]))
    except Exception:
        logger.warning("Could not read %s to check it is a page", fill.get("workflow"), exc_info=True)
        return
    from connect_labs.workflow.page_mode import is_page

    if definition is not None and not is_page(definition):
        raise ValueError(f"home.fill.workflow {fill['workflow']} is a workflow, not a page: a home must be a page")


LABS = register_namespace(
    Namespace(
        key="labs",
        label="Labs",
        description="The page this scope opens on.",
        schema={
            "type": "object",
            "properties": {
                "home": {
                    "type": "object",
                    "properties": {"fill": FILL, "label": {"type": "string", "maxLength": 80}},
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        layers=frozenset({"organization", "program", "opportunity"}),
        check=_one_owner,
        owners=_owners,
        check_targets=_home_is_a_page,
    )
)
