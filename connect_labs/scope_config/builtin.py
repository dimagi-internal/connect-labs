"""The `labs` namespace: set-up that belongs to labs itself rather than to one app.

`home` is the page a scope opens on: `/labs/p/org/<slug>/` with no page slug, and
an organisation coming in (spec, "An organisation coming in"). It names a page
definition AND the scope that owns that definition, because the two differ: a
labs-only organisation has no Connect id to own records with, so its home is a
page owned by one of its programmes, rendered with the organisation as its scope.

Whoever sets a home must be able to use its owner scope (`owners` below): the page
is read there for every viewer, so naming a scope you cannot use would hand its
records to everyone who opens yours.
"""

from connect_labs.scope_config.namespaces import Namespace, register_namespace
from connect_labs.scope_config.scopes import Scope

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
    )
)
