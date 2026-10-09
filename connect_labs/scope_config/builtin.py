"""The `labs` namespace: set-up that belongs to labs itself rather than to one app.

`home` is the page a scope opens on: `/labs/p/org/<slug>/` with no page slug, and
an organisation coming in (spec, "An organisation coming in"). It names a page
definition AND the scope that owns that definition, because the two differ: a
labs-only organisation has no Connect id to own records with, so its home is a
page owned by one of its programmes, rendered with the organisation as its scope.
"""

from connect_labs.scope_config.namespaces import Namespace, register_namespace

FILL = {
    "type": "object",
    "properties": {
        "workflow": {"type": "integer", "minimum": 1},
        "organization_id": {"type": ["integer", "string"]},
        "program_id": {"type": "integer", "minimum": 1},
        "opportunity_id": {"type": "integer", "minimum": 1},
    },
    "required": ["workflow"],
    "additionalProperties": False,
}


def _one_owner(data: dict) -> None:
    fill = (data.get("home") or {}).get("fill")
    if fill:
        owners = [k for k in ("organization_id", "program_id", "opportunity_id") if fill.get(k) not in (None, "")]
        if len(owners) != 1:
            raise ValueError(
                "home.fill names its page's owner with exactly one of organization_id, program_id or opportunity_id"
            )


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
    )
)
