"""A split tender awarded to one supplier, one partner delivered, one in transit, one not yet ordered.

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented. Not a test module: the round-2
purchase-walk tests import `build` from here (Sophie Feintuch's walk of the demo, Nov 2026).
"""

from connect_labs.supply_chain.tests import chlorine_world
from connect_labs.supply_chain.tests.chlorine_world import op

da = chlorine_world.da
account = chlorine_world.account
client_in_programme = chlorine_world.client_in_programme


def build(da, monkeypatch, *, ordered=("north", "river"), holds=3, contacts=True):
    """The world as a dict. `ordered` names the partners whose order is placed."""
    from connect_labs.labs.models import LabsOrg
    from connect_labs.supply_chain import runner

    op(
        da,
        "commodity_upsert",
        data={
            "slug": "rutf",
            "name": "RUTF",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
            "course_definition": {"base_units_per_course": 168},
        },
    )
    item = op(da, "item_upsert", data={"sku": "RUTF-CTN", "name": "RUTF carton", "commodity_slug": "rutf"})
    supplier = op(da, "supplier_create", data={"name": "Dabs Nutritional Products Ltd"})
    ours = op(da, "org_upsert", data={"slug": "us", "name": "The programme"})
    monkeypatch.setattr(runner, "running_org", lambda program_id: LabsOrg.objects.get(pk=ours["id"]))
    names = {"north": "North Star", "river": "Riverbend", "savanna": "Savanna"}
    orgs, places, splits = {}, {}, []
    for key, name in names.items():
        orgs[key] = op(da, "org_upsert", data={"slug": key, "name": name})
        places[key] = op(
            da,
            "supply_point_upsert",
            data={
                "slug": f"{key}-wh",
                "name": f"{name} warehouse",
                "kind": "central_store",
                "managed_by_org_id": orgs[key]["id"],
                "source": "we_recorded",
            },
        )
        splits.append(
            {
                "org_id": orgs[key]["id"],
                "quantity": {"north": 900, "river": 600, "savanna": 500}[key],
                "delivery_point": {"name": f"{name} warehouse", "city": {"north": "Kano"}.get(key, "Abuja")},
                "receiver_name": f"{name} receiver",
                "receiver_contact": f"{key}@example.org" if contacts else "",
                "buying_model": "programme_buys",
                "supply_point_id": places[key]["id"],
            }
        )
    tender = op(
        da,
        "tender_create",
        data={
            "label": "Demo split tender",
            "delivery_point": {"name": "Abuja depot", "city": "Abuja"},
            "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
            "partner_splits": splits,
        },
    )
    quote = op(
        da,
        "quote_record",
        data={
            "tender_id": tender["id"],
            "commodity_slug": "rutf",
            "supplier_id": supplier["id"],
            "as_quoted_amount": "52.40",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "2000",
            "quantity_basis_unit": "carton",
            "base_per_pack_stated": 150,
            "pack_spec_source": "stated_on_quote",
            "freight_basis": "included",
            "duties_basis": "included",
            **({"price_holds_for_purchases": holds} if holds is not None else {}),
        },
    )
    award = op(da, "award_create", tender_id=tender["id"], quote_id=quote["id"])
    made = {}
    if ordered:
        for contract in op(da, "award_orders_create", award_id=award["id"]):
            made[contract["partner_org_id"]] = contract
        # Only the partners named have ordered: cancel the rest.
        for key in names:
            contract = made.get(orgs[key]["id"])
            if contract and key not in ordered:
                op(da, "contract_update", contract_id=contract["id"], data={"status": "cancelled"})
                made.pop(orgs[key]["id"])
    return {
        "tender": tender,
        "award": award,
        "quote": quote,
        "supplier": supplier,
        "orgs": orgs,
        "places": places,
        "item": item,
        "contracts": {k: made.get(orgs[k]["id"]) for k in names},
    }


def deliver(da, world, key, *, day="2026-10-22", batch="DEMO-B1"):
    contract = world["contracts"][key]
    op(da, "contract_update", contract_id=contract["id"], data={"status": "placed"})
    shipment = op(
        da,
        "shipment_record",
        data={
            "contract_id": contract["id"],
            "status": "delivered",
            "dispatched_on": "2026-10-10",
            "source": "supplier_reported",
            "lines": [
                {
                    "item_id": world["item"]["id"],
                    "batch": batch,
                    "expiry": "2027-12-31",
                    "quantity": contract["quantity"],
                    "quantity_unit": "carton",
                }
            ],
        },
    )
    return op(
        da,
        "receipt_record",
        data={
            "contract_id": contract["id"],
            "shipment_id": shipment["id"],
            "supply_point_id": world["places"][key]["id"],
            "received_on": day,
            "reference": "DEMO-GRN-1",
            "source": "partner_reported",
            "lines": [
                {
                    "item_id": world["item"]["id"],
                    "batch": batch,
                    "expiry": "2027-12-31",
                    "quantity_accepted": contract["quantity"],
                    "quantity_unit": "carton",
                }
            ],
        },
    )


def in_transit(da, world, key, *, expected="2026-11-20"):
    contract = world["contracts"][key]
    op(da, "contract_update", contract_id=contract["id"], data={"status": "placed"})
    return op(
        da,
        "shipment_record",
        data={
            "contract_id": contract["id"],
            "status": "in_transit",
            "dispatched_on": "2026-10-15",
            "expected_on": expected,
            "source": "supplier_reported",
            "lines": [
                {
                    "item_id": world["item"]["id"],
                    "batch": "DEMO-B2",
                    "expiry": "2027-12-31",
                    "quantity": contract["quantity"],
                    "quantity_unit": "carton",
                }
            ],
        },
    )
