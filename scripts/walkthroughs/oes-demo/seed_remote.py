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

from datetime import timedelta

from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.fulfilment.services.landed import landed_total
from connect_labs.supply_chain.identity import WITNESSED_SOURCES
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.values import Money, decimal_string

PROGRAMME_ID = 10610
SUPPLY_ONLY_PROGRAMME_ID = 10671


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


# ======================================================================
# The chain -- and the three kinds of truth it carries
# ======================================================================
#
# The chain is dated relative to the day it is seeded, so the demo reads as
# something that happened recently rather than in whichever month this file
# was written. The order of the offsets is the story: the award is decided,
# the order placed, billed and paid; the goods land; the distributor reads
# its own sheet to us over WhatsApp -- and everything the distributor enters
# ITSELF, once it has a link, comes after all of them. That ordering is what
# makes section 5a legible on one screen: the second-hand rows sit above the
# day the partner was onboarded and its own rows below.

AWARDED_DAYS_AGO = 32
ORDERED_DAYS_AGO = 28
INVOICED_DAYS_AGO = 24
PAID_DAYS_AGO = 21
RECEIVED_DAYS_AGO = 18
COUNTED_DAYS_AGO = 14
# The day the distributor was given its own login-free link. Task 4's rows
# are recorded through it and are dated after this.
ONBOARDED_DAYS_AGO = 10


def day(days_ago):
    """A day in the chain's timeline, as an ISO date string."""
    return (timezone.localdate() - timedelta(days=days_ago)).isoformat()


def without_commentary(value):
    """The document without the notes its authors left for each other.

    Keys beginning with an underscore (`_why`, `_indicative`, `_note`) are
    written for a human reading the Drive document and are fields of no
    operation. They are stripped once, here, rather than at each call site:
    one forgotten strip is a row of commentary sitting in a JSONField that
    reads back as data, several hundred writes into a seed.
    """
    if isinstance(value, dict):
        return {key: without_commentary(item) for key, item in value.items() if not key.startswith("_")}
    if isinstance(value, list):
        return [without_commentary(item) for item in value]
    return value


def supplier_for_org(access, org, kind="distributor"):
    """The distributor as a supplier, and as the organisation it already is.

    The document models the distributor as an ORG, because that is what it is
    at the receiving end of the chain: it runs a warehouse and releases stock
    to the partners who collect. `quote_record` needs a SUPPLIER, because
    that is what the same body is at the selling end. One organisation, two
    roles -- which is the whole reason the partner seat is worth showing.

    The supplier row therefore carries `org_id`. Without it the two roles are
    two bodies: `update_links/service.py` decides whose word a submission is
    by asking whether the link's organisation supplies the order or manages
    the supply point, and a supplier with no `org` answers neither.

    Matched by name before creating, so re-running the seed does not leave
    two suppliers with the same name quoting against each other.
    """
    for existing in op(access, "supplier_list", search=org["name"]):
        if existing["name"] == org["name"]:
            return existing
    return op(
        access,
        "supplier_create",
        data={"name": org["name"], "type": kind, "status": "awarded", "org_id": org["id"]},
    )


def _goods_value(access, contract):
    """What the goods on this order are worth -- the domain's own figure.

    Not a second cost rule. The order page's goods line is
    `landed.landed_total(contract)["goods"]`, and an invoice computed any
    other way could disagree with the number printed beside it on a screen
    whose entire argument is that it will not show a cost it cannot defend.
    An earlier version of this seeder multiplied the quote out itself and
    refused two bases that `landed` happily multiplies, which is exactly that
    divergence.

    The Drive document holds no invoice value and says why: we do not hold
    the real supplier prices. The contract's unit price is the price the
    award was made at, so this is arithmetic over a stated figure rather than
    a number anyone invented.

    Anything but a `Money` is refused rather than coerced: `Unconfirmed` means
    the order cannot say what its goods cost, and `NotCosted` means they were
    not bought at all. Neither can be billed for.
    """
    goods = landed_total(access.get_contract(contract["id"]))["goods"]
    if isinstance(goods, Money):
        return decimal_string(goods.amount)
    reason = " ".join(getattr(goods, "reasons", ())) or getattr(goods, "reason", str(goods))
    raise ValueError(
        f"this order cannot be billed: {reason}. State the invoice amount in the seed "
        "document, or give the contract a unit price this figure can be worked out from"
    )


def _unit(basis, context):
    """A `pack` / `base` basis as the unit the ledger actually holds.

    The document says "pack" rather than "carton" for the same reason the
    partner's own form does (`update_links/service.py`): a unit typed by hand
    splits one balance into two that never add up. The ladder is the trade
    item's own, then the order's, then the commodity's.
    """
    item, commodity, contract = context["item"], context["commodity"], context["contract"]
    if basis == "base":
        unit = item.get("base_unit") or commodity.get("base_unit")
    else:
        unit = item.get("pack_unit") or contract.get("quantity_unit") or commodity.get("pack_unit")
    if not unit:
        raise ValueError(f"this product has no {'single' if basis == 'base' else 'pack'} unit on file")
    return unit


def _with_unit(data, context):
    """`unit_basis` resolved into the `quantity_unit` the operation wants."""
    if "unit_basis" in data:
        data["quantity_unit"] = _unit(data.pop("unit_basis"), context)
    return data


def _wire_receipt(data, context):
    """A goods received note. The quantities are the fact; the ids are ours.

    The document states one accepted quantity, its basis and a batch, because
    that is what somebody reads off a delivery note. `receipt_record` wants
    them as a line against a contract, at a supply point, on a date -- none
    of which the document can know, because it is written once and seeded
    into whatever programme is standing.
    """
    line = {key: data.pop(key) for key in _RECEIPT_LINE_FIELDS if key in data}
    line["item_id"] = context["item"]["id"]
    # No fallback unit. A line that says neither `unit_basis` nor
    # `quantity_unit` is a document that did not say what it counted, and
    # `receipt_record`'s own schema refuses it by name -- which is a better
    # error than a quantity silently filed against a guessed unit.
    _with_unit(line, context)
    return {
        "contract_id": context["contract"]["id"],
        "supply_point_id": context["warehouse"]["id"],
        "received_on": day(RECEIVED_DAYS_AGO),
        **data,
        "lines": [line],
    }


_RECEIPT_LINE_FIELDS = (
    "quantity_accepted",
    "quantity_rejected",
    "rejection_reason",
    "batch",
    "expiry",
    "unit_basis",
)


def _wire_stock_count(data, context):
    """What somebody says is on hand, at the store it is on hand in."""
    return {
        "supply_point_id": context["warehouse"]["id"],
        "commodity_slug": context["commodity"]["slug"],
        "item_id": context["item"]["id"],
        "counted_on": day(COUNTED_DAYS_AGO),
        **_with_unit(data, context),
    }


def _wire_payment(data, context):
    """A settlement, against the bill it settles.

    The document names the payment and not the invoice, because from the
    programme's side the fact is "we paid this". The invoice exists so the
    payment has something to be a settlement OF -- an amount paid against
    nothing cannot be shown as outstanding or cleared.
    """
    return {
        "invoice_id": context["invoice"]["id"],
        "paid_on": day(PAID_DAYS_AGO),
        "amount": context["invoice"]["amount"],
        "currency": context["invoice"]["currency"],
        **data,
    }


_WIRING = {
    "receipt_record": _wire_receipt,
    "stock_count_record": _wire_stock_count,
    "payment_record": _wire_payment,
}


def wired(row, context, stamp, *, may_witness=True):
    """A document row's partial `data`, with the ids only this run knows.

    `stamp` is the provenance the tier asserts -- who wrote the row down and
    how they knew. It is applied here rather than left to
    `identity.stamp_provenance` because this seeder runs as SYSTEM with no
    user and no request: there is nobody for the stamping layer to resolve,
    so it deliberately leaves the payload alone. A row that reached an
    operation with no provenance would be refused by the schema, which is the
    right way round.

    Two rules about where provenance may come from, both refusals:

      - it is declared BESIDE the operation, never inside `data`. `data` is
        the fact; `source` is how we knew it. One place to write it means one
        place to check it -- and a `source` buried in a payload would have
        slipped past the check below, because it was spread over the tier's
        own stamp.
      - a row under `reported_to_us` may not claim a WITNESSED source. That
        tier is somebody else's word with our hand on it, and a row there
        saying `we_recorded` would read on screen as something we saw, which
        is the exact inversion section 5a exists to prevent. The document
        declares a source on every row, so without this the tier headings
        would be labels carrying no enforcement at all.
    """
    operation = row["operation"]
    wire = _WIRING.get(operation)
    if wire is None:
        raise ValueError(
            f"the seed does not know how to attach {operation!r} to this chain; "
            f"add it to _WIRING (known: {', '.join(sorted(_WIRING))})"
        )

    in_payload = sorted(key for key in ("source", "recorded_by_org_id") if key in row["data"])
    if in_payload:
        raise ValueError(
            f"the {operation!r} row states {', '.join(in_payload)} inside its `data`. Provenance is "
            "declared beside the operation, not in the payload, so there is one place to read it "
            "and one place to check it"
        )

    data = {**row["data"], **stamp}
    if row.get("source"):
        data["source"] = row["source"]
    if not may_witness and data["source"] in WITNESSED_SOURCES:
        raise ValueError(
            f"the {operation!r} row is under `reported_to_us` and claims {data['source']!r}, which "
            "asserts first-hand knowledge. What a partner told us is not something we saw: use "
            "'partner_reported' or 'supplier_reported', or move the row to `we_did`"
        )
    return wire(data, context)


def _supply_point(access, row, reference, stamp):
    """One store, managed by the organisation that runs it.

    The document names that organisation by slug, because it is written
    before any of this exists; the domain holds `managed_by_org_id`.
    """
    row = dict(row)
    org_slug = row.pop("managed_by_org_slug", None)
    org_slug = row.pop("org_slug", None) or org_slug
    data = {**stamp, **row}
    if org_slug:
        data["managed_by_org_id"] = reference["orgs"][org_slug]["id"]
    return op(access, "supply_point_upsert", data=data)


def seed_chain(access, chain, reference):
    """One procurement, from the round to the stock sitting in the warehouse.

    Shared by the programme's own CHC chain and by the supply-only
    organisation's, because the document gives them the same shape: they
    differ in what they carry, not in how they are built. The difference that
    matters -- the supply-only chain has no opportunity binding and no
    user-held points -- is data, so `summary._deliver()` stops at the last
    store on its own rather than being made to.
    """
    chain = without_commentary(chain)
    orgs = reference["orgs"]
    programme_org = orgs[chain["programme_org_slug"]]
    distributor = orgs[chain["distributor_slug"]]

    # Tier 2 in the design's table: our own hand, first-hand. Everything the
    # programme itself does carries this, and `witnessed` is true of it.
    ours = {"source": "we_recorded", "recorded_by_org_id": programme_org["id"]}
    # Tier 1: our hand, their word. The spreadsheet world, told honestly --
    # `told_by_for` renders it "Dimagi, for EHA Clinics (they told us)".
    their_word = {"source": "partner_reported", "recorded_by_org_id": programme_org["id"]}

    supplier = supplier_for_org(access, distributor)

    round_ = op(access, "round_create", data=chain["round"])
    # A round that received quotes was open when it received them.
    round_ = op(access, "round_open", round_id=round_["id"])

    items, quotes = {}, []
    for quoted in chain["quotes"]:
        quoted = dict(quoted)
        item = op(
            access,
            "item_upsert",
            data={**quoted.pop("item"), "commodity_slug": quoted["commodity_slug"]},
        )
        items[item["sku"]] = item
        quotes.append(
            op(
                access,
                "quote_record",
                data={
                    **quoted,
                    "round_id": round_["id"],
                    "supplier_id": supplier["id"],
                    "item_id": item["id"],
                },
            )
        )

    awarded_index = chain["awarded_quote_index"]
    awarded_quote = chain["quotes"][awarded_index]
    awarded_item = items[awarded_quote["item"]["sku"]]
    award = op(
        access,
        "award_create",
        round_id=round_["id"],
        quote_id=quotes[awarded_index]["id"],
        rationale=chain["award_rationale"],
        decided_on=day(AWARDED_DAYS_AGO),
        **({"decided_by": chain["award_decided_by"]} if chain.get("award_decided_by") else {}),
    )

    # The stores first: the order says where its goods are to be delivered,
    # and a contract that cannot name the place is a contract nobody can
    # receive against.
    warehouse = _supply_point(access, chain["warehouse"], reference, ours)
    partner_points = {row["org_slug"]: _supply_point(access, row, reference, ours) for row in chain["partner_points"]}

    contract_row = dict(chain["contract"])
    buyer_slug = contract_row.pop("buyer_org_slug")
    contract = op(
        access,
        "contract_create",
        data={
            **ours,
            **contract_row,
            "round_id": round_["id"],
            "award_id": award["id"],
            "supplier_id": supplier["id"],
            "item_id": awarded_item["id"],
            "commodity_slug": awarded_quote["commodity_slug"],
            "buyer_org_id": orgs[buyer_slug]["id"],
            "delivery_supply_point_id": warehouse["id"],
            # The price the award was made at. Not a new figure: the contract
            # IS that award, and without it the comparison's per-course
            # column has nothing to carry through to the order.
            "unit_price": awarded_quote["as_quoted_amount"],
            "unit_price_unit": awarded_quote["as_quoted_unit"],
            "signed_on": day(ORDERED_DAYS_AGO),
        },
    )

    invoice = op(
        access,
        "invoice_record",
        data={
            **ours,
            "contract_id": contract["id"],
            "issued_on": day(INVOICED_DAYS_AGO),
            "amount": _goods_value(access, contract),
            "currency": contract_row["currency"],
            "quantity_billed": contract_row["quantity"],
            "quantity_unit": contract_row["quantity_unit"],
        },
    )

    context = {
        "contract": {**contract, "quantity_unit": contract_row["quantity_unit"]},
        "commodity": reference["commodities"][awarded_quote["commodity_slug"]],
        "item": awarded_item,
        "warehouse": warehouse,
        "partner_points": partner_points,
        "invoice": invoice,
    }

    # Tier 1 -- the spreadsheet world. The distributor told us over WhatsApp
    # that the goods had landed and read us a stock figure off its own sheet;
    # we typed both in. Our hand, their word, and the screen says so.
    reported_to_us = [
        op(access, row["operation"], data=wired(row, context, their_word, may_witness=False))
        for row in chain["reported_to_us"]
    ]

    # Tier 2 -- what we did ourselves, and therefore witnessed.
    we_did = [op(access, row["operation"], data=wired(row, context, ours)) for row in chain["we_did"]]

    # Tier 3 -- what the partner enters through its own link -- is seeded by
    # the partner-link step, because it has to go THROUGH the link: that is
    # what makes `recorded_by_org` the partner's rather than ours, and a row
    # written here claiming to be theirs would be exactly the self-assertion
    # `identity.stamp_provenance` exists to refuse.
    return {
        "round": round_,
        "supplier": supplier,
        "items": items,
        "quotes": quotes,
        "award": award,
        "contract": contract,
        "invoice": invoice,
        "warehouse": warehouse,
        # Keyed by organisation slug: this is the reference-map resolution a
        # row naming `to_org_slug` needs.
        "partner_points": partner_points,
        "reported_to_us": reported_to_us,
        "we_did": we_did,
    }


def seed_chc_chain(access, data, reference):
    """The CHC chain as it ran, carrying all three kinds of truth.

    The programme buys from the distributor; the distributor pays the
    manufacturers, holds the goods and releases them to the collecting
    partners. The rows are deliberately split three ways:

      - what we wrote down from an email or a WhatsApp message, which is the
        partner's word with our hand on it (`partner_reported`, recorded by
        us) -- this is the spreadsheet world, told honestly;
      - what we did ourselves and therefore witnessed (`we_recorded`);
      - what the partner entered through its own link, from the point at
        which it was given one.

    One order carries all three, so the screen can answer "what do we
    actually know about this stock, and how" in one read.
    """
    return seed_chain(access, data["chc_chain"], reference)
