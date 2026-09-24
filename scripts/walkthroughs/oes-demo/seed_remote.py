"""Server-side seed for the OES demo environment. Runs INSIDE labs.

``ensure_demo.py`` ships this file to the deployed labs worker and runs it
through ``manage.py shell`` -- the same route the walkthrough seeders use, and
for the same reason: every write tool is rate-limited per user, and this seed
is several hundred writes. Everything goes through ``call_operation``, so the
registry, its schemas and its provenance stamping apply exactly as they do to
the screens.

THIS REPOSITORY IS PUBLIC. Every partner name, volume and price this seeds
comes from the Drive document read by ``load_seed_data`` -- none of them
appear here. What IS here is the shape of the chain, which is public already
(docs/superpowers/specs/2026-09-23-supply-field-use-cases.md).

See docs/superpowers/specs/2026-09-24-oes-demo-environment-design.md.
"""

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

PROGRAMME_ID = 10610
SUPPLY_ONLY_PROGRAMME_ID = 10611


def op(access, name, **payload):
    return call_operation(name, access, payload)


def access_for(program_id):
    return SupplyDataAccess(access_token="oes-demo-seed", program_id=program_id, caller=SYSTEM)


def seed_reference(access, data):
    """The organisations and products the chain is made of.

    Organisations are upserted by slug and carry `connect_organization_id`,
    so the supply domain's EHA and Connect's EHA are one organisation. A
    lookalike would make the partner seat a mock-up.

    `connect_organization_id` is the organisation's IDENTITY
    (`connect_labs/labs/models.py`'s `LabsOrg` docstring: "It does not
    change") -- `org_upsert` correctly refuses an explicit null for it, so a
    partner with no known Connect id must not send the key at all rather than
    send it as `None`. `connect_organization_slug` is the designed route for
    exactly that case ("the slug is a finding aid, and matches only a row
    with no id yet"): it is a plain field on the org and is passed through
    whenever the source document carries one, independent of whether an id
    is also known.
    """
    orgs = {}
    for row in data["orgs"]:
        org_data = {
            "slug": row["slug"],
            "name": row["name"],
            "country": row.get("country", "NG"),
            "notes": row.get("notes", ""),
        }
        connect_organization_id = row.get("connect_organization_id")
        if connect_organization_id is not None:
            org_data["connect_organization_id"] = connect_organization_id
        connect_organization_slug = row.get("connect_organization_slug")
        if connect_organization_slug:
            org_data["connect_organization_slug"] = connect_organization_slug
        orgs[row["slug"]] = op(access, "org_upsert", data=org_data)

    commodities = {}
    for row in data["commodities"]:
        commodities[row["slug"]] = op(access, "commodity_upsert", data=row)

    return {"orgs": orgs, "commodities": commodities}
