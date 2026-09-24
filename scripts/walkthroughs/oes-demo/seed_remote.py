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
        # The ids this chain's rows were wired with. Returned so the tier-3
        # step resolves the same warehouse, trade item and product this
        # chain used, rather than working them out a second time from the
        # document and risking a different answer.
        "context": context,
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


# ======================================================================
# The partner seats -- and the third kind of truth
# ======================================================================
#
# Tier 3 is the only one that cannot be written from here. Tiers 1 and 2
# differ in `source` alone and both are recorded by us; tier 3 differs in
# `recorded_by_org`, and a row this seeder wrote claiming to be the
# distributor's would be exactly the self-assertion `identity.source_for`
# exists to refuse. So these rows go through the partner's own link, by the
# route a partner uses: an HTTP POST to the login-free page.
#
# The date of a tier-3 row is not the day it is typed. A distributor that has
# just been given a link types up what it has been doing, and the page says
# so ("recorded <today>" beside the day it happened). What makes section 5a
# legible is that everything ENTERED BY THE PARTNER was entered after
# `ONBOARDED_DAYS_AGO`, which is true of every row here by construction: they
# are all recorded now.
ENTERED_DAYS_AGO = 6
# The day the goods left the supplier: after the order, before they landed.
DISPATCHED_DAYS_AGO = 22


def _link_host():
    """A host name this deployment will actually accept.

    `django.test.Client` sends `testserver`, which a deployed labs refuses
    with `DisallowedHost` before the view is reached -- and the refusal names
    the header, not the seed. Preferred is the public host, since that is the
    one a partner types; it is used only when `ALLOWED_HOSTS` would accept it,
    so the same call works under the test settings, where `ALLOWED_HOSTS`
    carries `testserver` and not the public host.
    """
    from urllib.parse import urlparse

    from django.conf import settings

    allowed = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
    named = [host for host in allowed if "*" not in host]
    preferred = urlparse(getattr(settings, "LABS_PUBLIC_URL", "") or "").hostname
    if preferred and (not named or preferred in named or "*" in allowed):
        return preferred
    return named[0] if named else "testserver"


def _basis_for(data, context):
    """ "Packs or single units?", from whichever of the two a row states.

    The partner's form deliberately does not take a unit NAME
    (`update_links/forms.py`): a distributor typing "ctn" where the ledger
    holds "carton" splits one balance into two that never add up. The document
    writes quantities the way a person reads them off a sheet, so a stated
    unit is matched against the product's own ladder here, and a unit that is
    on neither rung is refused rather than filed under a guess.
    """
    if "unit_basis" in data:
        return data.pop("unit_basis")
    unit = data.pop("quantity_unit", None)
    if not unit:
        raise ValueError("this row does not say what it counted: give it a `quantity_unit` or a `unit_basis`")
    rungs = {}
    for basis in ("pack", "base"):
        try:
            rungs[_unit(basis, context)] = basis
        except ValueError:
            continue
    if unit not in rungs:
        raise ValueError(f"this product is not counted in {unit!r}; it is counted in {', '.join(sorted(rungs))}")
    return rungs[unit]


def _release_fields(row, context):
    """A release, as the partner's own form asks for it.

    The document says what left, how much of it and who collected it, which
    is what a distributor knows. Which store that organisation runs, which
    trade item the order was for and which warehouse it came out of are ours,
    and are taken from the chain this link was minted for.
    """
    data = dict(row["data"])
    kind = data.pop("kind", "transfer")
    if kind != "transfer":
        raise ValueError(f"a release through a link is a transfer, not {kind!r}")
    collected_by = data.pop("to_org_slug", None)
    if not collected_by:
        raise ValueError("a release has to say who collected it: give the row a `to_org_slug`")
    if collected_by not in context["partner_points"]:
        raise ValueError(f"no store in this chain is run by {collected_by!r}")
    return {
        "from_supply_point": context["warehouse"]["id"],
        "to_supply_point": context["partner_points"][collected_by]["id"],
        "item": context["item"]["id"],
        "quantity": data.pop("quantity", None),
        "unit_basis": _basis_for(data, context),
        "occurred_on": data.pop("occurred_on", None) or day(ENTERED_DAYS_AGO),
        **data,
    }


def _dispatch_fields(row, context):
    """A dispatch, as the supplier's own form asks for it.

    Dated when the goods LEFT, which is before the day we received them and
    so before the distributor held a link at all. That is not a contradiction
    and it is worth seeing: a distributor given a link types up what it has
    already been doing, and the page prints "recorded <today>" beside the day
    it happened precisely for this. What makes tier 3 tier 3 is who entered
    it, not when the thing it records took place.
    """
    data = dict(row["data"])
    return {
        "contract": context["contract"]["id"],
        "status": data.pop("status", "dispatched"),
        "quantity": data.pop("quantity", None),
        "unit_basis": _basis_for(data, context),
        "dispatched_on": data.pop("dispatched_on", None) or day(DISPATCHED_DAYS_AGO),
        **data,
    }


# Which of the partner's own actions produces each operation the document
# names. The document names OPERATIONS, as every other tier does; the page
# behind a link offers ACTIONS, and only its own. An operation with no action
# here is refused by name rather than written some other way -- writing it
# any other way is writing it as us, which is the one thing tier 3 must not
# be.
_ENTERED = {
    "movement_record": ("record_release", _release_fields),
    "shipment_record": ("record_shipment", _dispatch_fields),
}


def entered_through_link(token, row, context):
    """One tier-3 row, POSTed to the partner's page the way the partner does.

    Not `service.submit` and not an access object of our own making. The page
    is the write path a partner has: the token is found by keyed hash, the
    form offers only the rows the link covers, `service.submit` checks the
    scope again, and `_provenance` decides whose word it is FROM THE LINK.
    Reaching past any of that to stamp the distributor's id ourselves would
    produce a row that says "EHA Clinics" without EHA having said anything --
    a demo of the very substitution section 5a exists to make impossible.

    A refused submission is answered with a 200 and the form's errors, not an
    exception, so a seeder that only called this would report success and seed
    nothing. Anything but the redirect is raised, carrying the errors.
    """
    from django.test import Client
    from django.urls import reverse

    from connect_labs.supply_chain.update_links.forms import PUBLIC_FORMS

    operation = row["operation"]
    if operation not in _ENTERED:
        raise ValueError(
            f"the partner's page has no action that records {operation!r} "
            f"(it can record: {', '.join(sorted(_ENTERED))})"
        )
    stated = sorted(key for key in ("source", "recorded_by_org_id") if key in row or key in row.get("data", {}))
    if stated:
        raise ValueError(
            f"the {operation!r} row states {', '.join(stated)}. What the partner entered is stamped by the "
            "link it came through -- a row that declared whose word it is would be us saying it on their "
            "behalf, which is the whole difference this tier draws"
        )

    action, build = _ENTERED[operation]
    fields = build(row, context)
    unknown = sorted(set(fields) - set(PUBLIC_FORMS[action].base_fields))
    if unknown:
        raise ValueError(f"{action!r} has no field {', '.join(unknown)} -- check the row against the form")

    response = Client().post(
        reverse("supply_chain:update_link_public", kwargs={"token": token}),
        {"action": action, **{f"{action}-{name}": value for name, value in fields.items() if value not in (None, "")}},
        SERVER_NAME=_link_host(),
    )
    if response.status_code != 302:
        raise ValueError(
            f"the partner's page refused this {operation!r} row ({response.status_code}): {_why(response)}"
        )
    return response


def _why(response):
    """What the page said was wrong, from the form it re-rendered."""
    forms = (getattr(response, "context", None) or {}).get("forms") or []
    errors = [form.errors.as_text() for form in forms if getattr(form, "errors", None)]
    return " / ".join(errors) or "no form errors -- the page did not accept the submission at all"


def _listed_cover(org, chain, destinations):
    """What a LISTED link names, worked out from the chain it is minted for.

    The document cannot name rows: it is written long before any of them
    exist. A listed link therefore covers the chain's order, the stores its
    own organisation runs, and the stores its rows release into -- exactly
    what it needs to record its part and nothing more. (A link that follows
    its organisation names nothing at all, which is why this is only for the
    other kind.)
    """
    points = [chain["warehouse"], *chain["partner_points"].values()]
    covered = {point["id"]: point for point in points if point.get("managed_by_org_id") == org["id"]}
    covered.update({point["id"]: point for point in destinations})
    return {"contract_ids": [chain["contract"]["id"]], "supply_point_ids": sorted(covered)}


def seed_partner_links(access, data, reference, chain):
    """A link per partner, and the rows they entered through it.

    Minted here rather than on camera so the raw tokens never reach anything
    committed. A link that follows its ORGANISATION is how a distributor the
    programme buys from every quarter actually holds one: an order placed next
    month is covered without reissuing. A link that covers only what it is
    given is how the same distributor releases stock into somebody else's
    store -- `update_links/service.py` resolves an organisation link's stores
    as the ones that organisation runs, so a release into a collecting
    partner's store needs a link that names it. The document says which each
    is; this refuses to guess between them.

    Returns raw tokens. They are shown once and never stored, so this value
    is for handing to a person -- never for writing to a file.
    """
    chain_document = without_commentary(data["chc_chain"])
    entered = chain_document.get("partner_entered") or []
    distributor_slug = chain_document["distributor_slug"]
    context = chain["context"]

    links = {}
    for row in without_commentary(data["partner_links"]):
        org = reference["orgs"][row["org_slug"]]
        issue = {"org_id": org["id"], "label": row["label"], "coverage": row.get("coverage", "organisation")}
        if issue["coverage"] != "organisation":
            # Only the distributor's own rows name a destination, and they
            # are the reason a listed link is asked for at all.
            destinations = [
                context["partner_points"][entry["data"]["to_org_slug"]]
                for entry in entered
                if row["org_slug"] == distributor_slug and entry.get("data", {}).get("to_org_slug")
            ]
            issue.update(_listed_cover(org, chain, destinations))
        issued = op(access, "update_link_issue", data=issue)
        links[row["org_slug"]] = {"id": issued["id"], "token": issued["token"], "url": issued["url"]}

    # Tier 3 -- what the distributor recorded ITSELF, through that link. These
    # carry the distributor's own `recorded_by_org`, so they lose the "they
    # told us" qualifier and the "reported, not witnessed" note beside it.
    if entered:
        token = links[distributor_slug]["token"]
        for row in entered:
            entered_through_link(token, row, context)

    return links
