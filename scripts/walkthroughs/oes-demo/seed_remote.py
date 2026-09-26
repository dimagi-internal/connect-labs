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

import re
from datetime import timedelta
from html import unescape

from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.fulfilment.services.landed import landed_total
from connect_labs.supply_chain.identity import WITNESSED_SOURCES
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.values import Money, decimal_string

# Three chains, three programs -- plus the supply-only organisation's own.
#
# `SupplyDataAccess.scope_key` is "the program, always", and it governs the
# CATALOGUE as well as the ledger: commodities, items and suppliers are
# per-program, not shared. CHC is Connect program 217 under `dimagi-chc-rct`,
# RUTF is program 263 under `dimagi-ng-rutf`, and the chlorine chain is not an
# opportunity at all yet -- which is the whole point of its beat. One scope
# holding all three would put chlorine in the CHC catalogue and RUTF's
# supplier register in with ORS: a program on screen that corresponds to
# nothing real. See the design, section 1a.
CHC_PROGRAM_ID = 10610
RUTF_PROGRAM_ID = 10672
CHLORINE_PROGRAM_ID = 10673
SUPPLY_ONLY_PROGRAM_ID = 10671

# Which section of the Drive document each scope is seeded from. The first
# three keys are the names the document's `portfolio.program_slugs` already
# uses, so the portfolio resolves to program ids through this map rather than
# through a second one that could disagree with it. The supply-only
# organisation is deliberately NOT in that portfolio: it is a different
# organisation's program, not one of this operation's three chains.
SCOPES = {
    "chc": {"program_id": CHC_PROGRAM_ID, "section": "chc_chain"},
    "rutf": {"program_id": RUTF_PROGRAM_ID, "section": "rutf_rounds"},
    "chlorine": {"program_id": CHLORINE_PROGRAM_ID, "section": "chlorine_blocked"},
    "supply_only": {"program_id": SUPPLY_ONLY_PROGRAM_ID, "section": "supply_only"},
}


def op(access, name, **payload):
    return call_operation(name, access, payload)


def access_for(program_id):
    return SupplyDataAccess(access_token="oes-demo-seed", program_id=program_id, caller=SYSTEM)


# Where each supplier's goods leave from: `{supplier label or org slug: {country, city}}`,
# read from the document's `supplier_places` by `seed_orgs`, which runs first.
# A module global rather than a parameter threaded through every seeder,
# because suppliers are created from six call sites and a location is a fact
# about the supplier, not about the chain that happens to buy from it. Real
# towns are named in Drive, never here -- this repository is public.
_SUPPLIER_PLACES: dict = {}


def _placed_supplier(access, supplier, key):
    """`supplier`, with the country and city the document gives it filled in.

    Only fills blanks: a supplier is a global company since #2019, so one
    somebody has already located -- through the marketplace, or by hand --
    keeps what they said.
    """
    place = _SUPPLIER_PLACES.get(key) or {}
    missing = {field: place[field] for field in ("country", "city") if place.get(field) and not supplier.get(field)}
    if not missing:
        return supplier
    return op(access, "supplier_update", supplier_id=supplier["id"], data=missing)


def seed_orgs(access, data):
    """The organisations, which belong to no program in particular.

    `upsert_org` is the one reference write in this domain that is NOT
    program-scoped, and says why: "An organisation is the same organisation
    in every program it appears in, and scoping it per program is what
    produced three registries of the same thing." So these are seeded once
    and shared by all four scopes, and the `access` here only carries the
    call -- any scope's would do.

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
    _SUPPLIER_PLACES.clear()
    _SUPPLIER_PLACES.update(without_commentary(data.get("supplier_places") or {}))
    orgs = {}
    directory = None
    for row in data["orgs"]:
        if row.get("from_directory"):
            # An organisation already in the partner directory, used as it
            # is. Upserting it would overwrite a real partner's name and
            # notes with the demo's; a slug of our own would make a second
            # row of the same organisation -- which is how the demo came to
            # hold two ISODAFs. So it is read, never written, and refused by
            # name if the directory does not have it.
            if directory is None:
                directory = {org["slug"]: org for org in op(access, "org_list")}
            if row["slug"] not in directory:
                raise ValueError(
                    f"the document marks {row['slug']!r} as from_directory, and the directory has no such "
                    "organisation -- run marketplace_import, or fix the slug"
                )
            orgs[row["slug"]] = directory[row["slug"]]
            continue
        # A NEW slug under a name another organisation already answers to is
        # a second row of that organisation. The demo made three this way;
        # the copies then shadowed the directory's rows on the network page.
        # A real partner is `from_directory`, always. A slug that already
        # exists is an update of that row, not a new one, and is left alone.
        if directory is None:
            directory = {org["slug"]: org for org in op(access, "org_list")}
        holder = None
        if row["slug"] not in directory:
            holder = next(
                (
                    org["slug"]
                    for org in directory.values()
                    if org["name"].strip().lower() == row["name"].strip().lower()
                ),
                None,
            )
        if holder:
            raise ValueError(
                f"the document would write {row['slug']!r} as {row['name']!r}, which {holder!r} already is -- "
                f'mark it {{"slug": "{holder}", "from_directory": true}} instead'
            )
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
    return orgs


def _catalogue_slice(commodity_slugs):
    """Which products a scope gets -- and there is no "all of them" default.

    `commodity_slugs` is keyword-only and required on both seeders below, so
    the call that used to put every chain's products into one program is now
    a `TypeError` at the call site rather than a quietly wrong demo. `None`
    is refused here for the same reason: a permissive value would be the
    default in everything but name, and it was a default that made the wrong
    call the easy one. (The plan's own Task 5 code block still contains that
    call.)

    Seeding by hand rather than through `seed_scopes` is fine -- it just has
    to say which products, which is one call to `commodities_for`.
    """
    if commodity_slugs is None:
        raise ValueError(
            "a scope's catalogue needs the products that scope's chain names: pass "
            "commodity_slugs=commodities_for(section, data['commodities']), or seed every scope "
            "at once with seed_scopes(data). There is deliberately no 'all of them' default -- "
            "one catalogue holding every chain's products is the thing this split exists to "
            "prevent, and it is how chlorine ends up in the CHC picker"
        )
    return set(commodity_slugs)


def seed_catalogue(access, data, *, commodity_slugs):
    """This scope's products -- and only this scope's.

    `commodity_slugs` is the whole point of the split. The catalogue is
    program-scoped, so seeding the document's seven products into every
    scope would put chlorine in the CHC catalogue and RUTF in with ORS, and
    every product picker in the program would then offer things that program
    has never bought.

    Order is the document's, which matters for a kit: `_kit_components`
    refuses a component that is not already a product in the same catalogue,
    so a co-pack's contents have to be upserted before the co-pack. The
    document lists them that way and this does not resort them.
    """
    wanted = _catalogue_slice(commodity_slugs)
    rows = [row for row in data["commodities"] if row["slug"] in wanted]
    return {row["slug"]: op(access, "commodity_upsert", data=row) for row in rows}


def seed_reference(access, data, *, commodity_slugs):
    """The organisations and products one scope's chain is made of.

    Two halves with two different scopes, which is why they are separate
    functions above: organisations are labs-wide and shared, the catalogue is
    this program's alone.

    Seeding one of this demo's four scopes goes through `seed_scopes`. This
    is for a caller with exactly one, and it still has to say which products
    that one holds -- the slice is checked here, before the organisations are
    written, so a call that does not say leaves nothing behind.
    """
    commodity_slugs = _catalogue_slice(commodity_slugs)
    return {
        "orgs": seed_orgs(access, data),
        "commodities": seed_catalogue(access, data, commodity_slugs=commodity_slugs),
    }


def _commodities_named(value, found):
    """Every product slug anywhere in a document section."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "commodity_slug" and isinstance(item, str):
                found.add(item)
            elif key == "commodity_slugs" and isinstance(item, list):
                found.update(slug for slug in item if isinstance(slug, str))
            else:
                _commodities_named(item, found)
    elif isinstance(value, list):
        for item in value:
            _commodities_named(item, found)
    return found


def commodities_for(section, catalogue):
    """The products one chain needs: the ones it names, and their contents.

    Derived from the section rather than listed per scope in the document,
    so the split cannot drift from the chain it describes: a round that gains
    a line gains its product in the same edit.

    A kit's components come with it. They are products in their own right and
    `_kit_components` refuses one that is not in the same catalogue, so a
    co-pack seeded without its ORS sachet is a co-pack whose specification
    can never be checked -- and "no requirement to fail" reads as a pass.

    A product the section names and the document does not define is refused
    rather than skipped: the chain would otherwise be seeded against a
    catalogue that is missing exactly the thing it is about.
    """
    by_slug = {row["slug"]: row for row in catalogue}
    wanted, pending = set(), sorted(_commodities_named(section, set()))
    while pending:
        slug = pending.pop()
        if slug in wanted:
            continue
        row = by_slug.get(slug)
        if row is None:
            raise ValueError(
                f"this chain names the product {slug!r}, which the document's `commodities` does "
                "not define; add it there or correct the chain"
            )
        wanted.add(slug)
        pending.extend(component["commodity_slug"] for component in row.get("components") or [])
    return wanted


def seed_scopes(data):
    """One program per chain, each with its own catalogue.

    The organisations are seeded first and once, because they are not
    program-scoped (`seed_orgs`). Everything else here is per scope: the
    access object, the catalogue, and -- once the chain seeders run against
    it -- the items, suppliers and the ledger itself.

    This seeds REFERENCE data only. The chains are seeded against these
    scopes afterwards -- `seed_chc_chain` against "chc", `seed_supply_only`
    against "supply_only" -- and RUTF and chlorine have their own tasks and
    their own sections, so until those land their scopes hold a catalogue and
    no chain. That is the correct intermediate state: a program with the
    right products and nothing bought yet is exactly what a chain about to be
    seeded looks like.
    """
    # Every section resolved, and every product it names found, BEFORE the
    # first write: a document missing its chlorine section should not leave a
    # half-seeded environment that looks like a working one.
    sections = {}
    for name, scope in SCOPES.items():
        section = data.get(scope["section"])
        if section is None:
            raise ValueError(
                f"the seed document has no {scope['section']!r} section, which is what the "
                f"{name!r} program is seeded from"
            )
        sections[name] = (section, commodities_for(section, data["commodities"]))

    scopes, orgs = {}, None
    for name, scope in SCOPES.items():
        section, slugs = sections[name]
        access = access_for(scope["program_id"])
        if orgs is None:
            orgs = seed_orgs(access, data)
        commodities = seed_catalogue(access, data, commodity_slugs=slugs)
        scopes[name] = {
            "name": name,
            "program_id": scope["program_id"],
            "access": access,
            "section": section,
            "reference": {"orgs": orgs, "commodities": commodities},
        }
    return scopes


def seed_portfolio(data):
    """The portfolio: a name, and which of the scopes above belong to it.

    The document names its members by SCOPE SLUG -- `chc`, `rutf`,
    `chlorine` -- rather than by program id, so the ids stay written down in
    exactly one place (`SCOPES`) and the document cannot drift from them.

    **An unknown slug is refused by name rather than skipped.** A portfolio
    silently short by one chain, saying nothing about the one it dropped, is
    precisely the failure the master view exists to prevent -- and a seeder
    that produces one has put the misinformation into the data where no view
    can correct it.

    Written straight through the ORM rather than through an operation, and
    that is a decision rather than a shortcut: every supply write goes through
    the registry because the registry validates a payload and stamps who
    recorded it, and both exist to protect SUPPLY DATA. A name and a list of
    ids is neither, and a `Portfolio` carries no programme scope for an
    operation to be called with. See its model docstring.
    """
    from django.urls import reverse

    from connect_labs.supply_chain.portfolio.models import Portfolio

    section = data.get("portfolio")
    if section is None:
        raise ValueError(
            "the seed document has no 'portfolio' section, which is what the master view at "
            "/supply/portfolios/<slug>/ is built from"
        )
    slugs = list(section.get("program_slugs") or [])
    if not slugs:
        raise ValueError("the portfolio names no programs, so there would be nothing for it to span")
    unknown = [slug for slug in slugs if slug not in SCOPES]
    if unknown:
        raise ValueError(
            f"the portfolio names {', '.join(repr(s) for s in unknown)}, which SCOPES does not "
            f"know -- it holds {', '.join(sorted(SCOPES))}. Refusing rather than seeding a "
            "portfolio that is short by a chain and says nothing about it."
        )

    portfolio, _ = Portfolio.objects.update_or_create(
        slug=section["slug"],
        defaults={
            "name": section["name"],
            # In the document's stated order. The master view renders its rows
            # in it, because ranking them would be a judgement the database
            # cannot make.
            "program_ids": [SCOPES[slug]["program_id"] for slug in slugs],
        },
    )
    return {
        "id": portfolio.pk,
        "slug": portfolio.slug,
        "program_ids": portfolio.program_ids,
        "url": reverse("supply_chain:portfolio", args=[portfolio.slug]),
    }


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
            return _placed_supplier(access, existing, org["slug"])
    created = op(
        access,
        "supplier_create",
        data={"name": org["name"], "type": kind, "status": "awarded", "org_id": org["id"]},
    )
    return _placed_supplier(access, created, org["slug"])


def supplier_for_label(access, label, kind="manufacturer"):
    """A supplier that is a name and nothing else -- no organisation behind it.

    Sibling of `supplier_for_org`, for the case that function's docstring
    says is the reason the two are different functions: a quote can come
    from someone the document can only NAME, not identify. There is no
    `LabsOrg` row this supplier IS, so unlike `supplier_for_org` this must
    never carry an `org_id` -- inventing one would assert an organisation the
    document does not claim.

    Matched by name before creating, for the same reason `supplier_for_org`
    matches by name: re-running the seed must not leave two suppliers of the
    same name quoting against each other.

    `kind` defaults to `manufacturer` -- the nearest fit in
    `records.SUPPLIER_TYPES` for an unidentified quoting party that is not
    the (already-modelled) distributor.
    """
    for existing in op(access, "supplier_list", search=label):
        if existing["name"] == label:
            return _placed_supplier(access, existing, label)
    created = op(access, "supplier_create", data={"name": label, "type": kind, "status": "quoting"})
    return _placed_supplier(access, created, label)


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
    into whatever program is standing.
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
    program's side the fact is "we paid this". The invoice exists so the
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


def _chain_supplier(access, chain, orgs):
    """Who sold us this, as the chain itself says.

    Two chains in this demo are sourced two different ways, and the difference
    is real rather than a modelling convenience.

    The CHC basket is bought from a DISTRIBUTOR that is also one of our
    partners -- it pays the manufacturers, holds the goods and releases them
    to the collecting partners. It is an organisation of ours, so it is named
    by `distributor_slug` and gets a supplier carrying `org_id`: the two roles
    are one body, which is what makes the partner seat at beat 6 worth
    showing, and `update_links/service.py` decides whose word a submission is
    by asking whether the link's organisation supplies the order.

    RUTF is bought from a MANUFACTURER, which is not an organisation of ours
    and never will be -- it quotes us and that is the whole relationship. So
    that chain names `supplier_label` and gets a supplier with no `org_id`,
    because inventing an organisation for it would assert something the
    document does not claim.

    Exactly one of the two keys, and the refusal names both: a chain with
    neither is a purchase from nobody, and a chain with both is two different
    answers to "who sold us this" with no rule for choosing.
    """
    label = chain.get("supplier_label")
    slug = chain.get("distributor_slug")
    if label and slug:
        raise ValueError(
            f"this chain names both supplier_label ({label!r}) and distributor_slug ({slug!r}); "
            "they are two different answers to who sold us this, so name one"
        )
    if label:
        return supplier_for_label(access, label)
    if slug:
        return supplier_for_org(access, orgs[slug])
    raise ValueError(
        "this chain names neither `supplier_label` nor `distributor_slug`, so there is nobody it " "was bought from"
    )


def opened(access, round_):
    """Open a round that has not been opened, and leave any other alone.

    `round_open` used to follow `round_create` and so always acted on a fresh
    draft. `tender_for` broke that precondition the day it landed: it may hand
    back a round this seeder created on an earlier run, and that round may
    already be awarded -- at which point opening it again drags a bought
    round back onto the market.

    That is not cosmetic. Since the supplier marketplace shipped, an OPEN
    round is public, so re-running the seeder would have re-published rounds
    that were decided weeks ago and invited quotes for goods already bought.

    Found by the session building the portfolio map, re-seeding far more
    often than I did.
    """
    if round_.get("status") != "draft":
        return round_
    return op(access, "tender_open", tender_id=round_["id"])


def tender_for(access, data):
    """This scope's round with that label, or a new one.

    Matched by label before creating, for the same reason `supplier_for_org`
    matches by name: re-running a seeder must not leave two rounds of the
    same label sitting beside each other. `ensure_demo` already refuses to
    seed a scope that holds rows, and that remains the real protection -- but
    it guards the WHOLE run, and a seeder called on its own while iterating
    slips past it. One did, and left the programme showing "CHC basket - Q1
    top-up" twice, both awarded.

    That mattered more than a tidy list: since #2021 an open round is public
    on the supplier marketplace, so a duplicate is not just untidy internally,
    it is two identical requests for quotes shown to suppliers.
    """
    label = (data or {}).get("label")
    if label:
        for existing in op(access, "tender_list"):
            if existing.get("label") == label:
                return existing
    return op(access, "tender_create", data=data)


# ======================================================================
# Seeding twice leaves what seeding once did
# ======================================================================
#
# A seeder gets re-run constantly: after a schema change, after somebody
# edits the document, while iterating on a screen, when a demo needs
# resetting an hour before a call. `ensure_demo` REFUSES to seed a scope that
# already holds rows, and that stays as the safety net -- but refusing is not
# convergence. It leaves the only route being a full purge, which throws away
# the partner links and their tokens along with everything else.
#
# So every record this seeder writes is found before it is made, keyed on
# something the DOCUMENT supplies rather than on a database id:
#
#   organisations, products, trade items, stores   upsert, by slug or SKU
#   suppliers                                      by name
#   rounds                                         by label
#   orders, receipts, payments                     by reference
#   quotes                                         by round, supplier and item
#   awards, invoices                               one per round / per order
#
# **The ledger is the exception, and deliberately.** A movement, a stock
# count and a receipt are EVENTS. Two identical receipts on one order are a
# real thing that can happen, so they cannot be deduplicated by looking at
# them -- there is nothing in a second one that says it is a mistake. Those
# the document does not reference are therefore written exactly once, on the
# run that creates the order, and a later run that FINDS the order leaves the
# ledger alone. See `seed_chain`.


def _found(rows, match):
    """The first row that matches, or None. `rows` is an operation's result."""
    return next((row for row in rows if match(row)), None)


def by_reference(access, list_name, reference, **query):
    """A record this scope already holds under that reference, or None.

    A reference is the document's own key for a row -- a purchase order
    number, a goods received note number, a payment reference -- and it is
    the only identifier that survives a re-seed, because ids do not.
    """
    if not reference:
        return None
    return _found(op(access, list_name, **query), lambda row: row.get("reference") == reference)


def seed_chain(access, chain, reference):
    """One procurement, from the round to the stock sitting in the warehouse.

    Shared by the program's own CHC chain and by the supply-only
    organisation's, because the document gives them the same shape: they
    differ in what they carry, not in how they are built. The difference that
    matters -- the supply-only chain has no opportunity binding and no
    user-held points -- is data, so `summary._deliver()` stops at the last
    store on its own rather than being made to.
    """
    chain = without_commentary(chain)
    orgs = reference["orgs"]
    # The document's own key still reads `programme_org_slug`; it is data in
    # Drive, so it is left as written rather than churned by a rename here.
    program_org = orgs[chain["programme_org_slug"]]

    # Tier 2 in the design's table: our own hand, first-hand. Everything the
    # program itself does carries this, and `witnessed` is true of it.
    ours = {"source": "we_recorded", "recorded_by_org_id": program_org["id"]}
    # Tier 1: our hand, their word. The spreadsheet world, told honestly --
    # `told_by_for` renders it "Dimagi, for EHA Clinics (they told us)".
    their_word = {"source": "partner_reported", "recorded_by_org_id": program_org["id"]}

    supplier = _chain_supplier(access, chain, orgs)

    round_ = tender_for(access, chain["round"])
    # A round that received quotes was open when it received them -- but only
    # if it is still a draft. See `opened`.
    round_ = opened(access, round_)

    items, quotes = {}, []
    for quoted in chain["quotes"]:
        quoted = dict(quoted)
        item = op(
            access,
            "item_upsert",
            data={**quoted.pop("item"), "commodity_slug": quoted["commodity_slug"]},
        )
        items[item["sku"]] = item
        # No reference on a quote, so its key is who quoted what against
        # which round -- which is exactly what makes two of them a duplicate
        # here, and what `round_compare` would show side by side.
        already = _found(
            op(access, "quote_list", tender_id=round_["id"]),
            lambda row: row.get("supplier_id") == supplier["id"] and row.get("item_id") == item["id"],
        )
        quotes.append(
            already
            or op(
                access,
                "quote_record",
                data={
                    **quoted,
                    "tender_id": round_["id"],
                    "supplier_id": supplier["id"],
                    "item_id": item["id"],
                },
            )
        )

    awarded_index = chain["awarded_quote_index"]
    awarded_quote = chain["quotes"][awarded_index]
    awarded_item = items[awarded_quote["item"]["sku"]]
    # One award per round in this seeder, so the round IS the key.
    award = _found(op(access, "award_list", tender_id=round_["id"]), lambda row: True) or op(
        access,
        "award_create",
        tender_id=round_["id"],
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
    # The order's own purchase-order number, which is what a person would use
    # to say "that one" and the only identifier that survives a re-seed.
    contract = by_reference(access, "contract_list", contract_row.get("reference"))
    # Whether the ORDER was found or made decides whether the ledger rows
    # below are written at all -- see the block that posts them.
    chain_is_new = contract is None
    contract = contract or op(
        access,
        "contract_create",
        data={
            **ours,
            **contract_row,
            "tender_id": round_["id"],
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

    # One invoice per order in this seeder, so the order is the key.
    invoice = _found(op(access, "invoice_list", contract_id=contract["id"]), lambda row: True) or op(
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

    # The ledger, written exactly once: on the run that CREATED this order.
    #
    # Everything above has a key the document supplies, so a second run finds
    # it. These do not, and they cannot: a receipt, a payment and a stock
    # count are EVENTS, and two identical receipts against one order are a
    # real thing that happens. Nothing about a second one says it is a
    # mistake rather than a second delivery, so deduplicating them by
    # inspection would mean this seeder deciding which real events are
    # allowed to exist.
    #
    # So the order's own existence is the record of whether they have been
    # posted. A run that finds the order leaves the ledger exactly as it is,
    # including anything a person has added to it since -- which is the
    # behaviour somebody re-seeding a demo they have been using actually
    # wants.
    reported_to_us, we_did = [], []
    if chain_is_new:
        # Tier 1 -- the spreadsheet world. The distributor told us over
        # WhatsApp that the goods had landed and read us a stock figure off
        # its own sheet; we typed both in. Our hand, their word, and the
        # screen says so.
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

    The program buys from the distributor; the distributor pays the
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


def seed_supply_only(data, scopes):
    """The second organisation: supply, without Connect's verified delivery.

    Same chain, same comparison, same approval gate, same stock ledger. What
    it does not have is any binding to a Connect opportunity -- no
    `opportunity_id` on its supply points and no user-held points -- so the
    ledger stops at its last store and the product makes no claim that
    anything reached a beneficiary. That is the honest answer for an
    implementer that runs its own last mile, and most of a funder's
    portfolio looks like this (design section 6, beat 9).

    **The stop is data, not code.** `summary._deliver()` keys off exactly
    those two fields, so this seeds the document's `supply_only` section
    through the same `seed_chain` the CHC chain goes through and changes
    nothing about it. There is no branch here that suppresses delivery: if
    that section ever gained an `opportunity_id` or a `user_held` point, the
    close would quietly start claiming reach, and that is a defect in the
    document rather than something to guard against in this function.

    Takes `scopes` rather than building its own access: this scope's
    catalogue was already seeded from its own section by `seed_scopes`, and
    `seed_reference(access, data)` -- which the plan's own Task 5 block
    calls -- would put every chain's products into this one program. It no
    longer runs at all, but the right call is still the cheaper one: take
    the access and the reference this scope already has.
    """
    scope = scopes["supply_only"]
    return {
        "program_id": SUPPLY_ONLY_PROGRAM_ID,
        "chain": seed_chain(scope["access"], data["supply_only"], scope["reference"]),
    }


def seed_rutf_round_two(access, round_two):
    """Round 2: open, quoted by three suppliers, and deliberately unawarded.

    Not `seed_chain`. `seed_chain` awards, contracts and orders -- and round
    2's entire point (design section 7/8, beats 1-3) is that it CANNOT yet be
    decided: three suppliers, each incomparable for exactly one reason in the
    product's own vocabulary, with no award and no contract to follow. Awarding
    one here to reuse `seed_chain` would answer the question the beat exists
    to leave open.

    A round that received quotes was open when it received them -- the same
    rule `seed_chain` applies to round 1, applied here by hand since this
    function is not going through it.

    The suppliers are named only by `supplier_label`: unlike round 1's
    distributor, there is no organisation behind them (`supplier_for_label`),
    and no `item_id` is ever passed -- the document's quotes name no trade
    item, and that absence is itself part of what makes the first supplier's
    quote incomparable. Giving the other two one would make their stated
    `base_per_pack_stated` redundant and change which figure blocks them.

    `supplier_label`, and the document's descriptive-only `supplier_country`
    / `supplier_note` (when present -- they name real firms, so this file
    never repeats them), are popped off before the row reaches
    `quote_record`: none of the three is a field that operation understands,
    and this does not rely on `_columns` silently dropping unrecognised keys
    to keep them out of the write.
    """
    round_two = without_commentary(round_two)
    round_ = tender_for(access, round_two["round"])
    # A round that received quotes was open when it received them -- but only
    # if it is still a draft. See `opened`.
    round_ = opened(access, round_)

    quotes, suppliers = [], []
    for quoted in round_two["quotes"]:
        quoted = dict(quoted)
        supplier = supplier_for_label(access, quoted.pop("supplier_label"))
        quoted.pop("supplier_country", None)
        quoted.pop("supplier_note", None)
        suppliers.append(supplier)
        # Keyed on round and supplier alone, not on the trade item: these
        # quotes deliberately identify no item -- that is what makes the
        # first of them uncostable -- so there is nothing else to key on, and
        # one supplier quotes a round once here.
        already = _found(
            op(access, "quote_list", tender_id=round_["id"]),
            lambda row, s=supplier: row.get("supplier_id") == s["id"],
        )
        quotes.append(
            already
            or op(
                access,
                "quote_record",
                data={**quoted, "tender_id": round_["id"], "supplier_id": supplier["id"]},
            )
        )
    return {"round": round_, "quotes": quotes, "suppliers": suppliers}


def seed_rutf_rounds(data, scopes):
    """The RUTF chain: round 1 (ran, comparable, awarded) and round 2 (open, not).

    Takes `scopes` rather than building its own access, for the reason
    `seed_supply_only` records in its docstring: the `rutf` scope's catalogue
    was already seeded from its own section by `seed_scopes`, and calling
    `seed_reference` again here would put every chain's products into this
    one program.

    Round 1 is already in exactly the shape `seed_chain` consumes, so it goes
    straight through it -- same as the CHC chain and the supply-only one.
    Round 2 is a different shape (no award, three anonymous suppliers) and
    goes through `seed_rutf_round_two` instead.
    """
    scope = scopes["rutf"]
    section = data["rutf_rounds"]
    return {
        "program_id": RUTF_PROGRAM_ID,
        "round_one": seed_chain(scope["access"], section["round_one"], scope["reference"]),
        "round_two": seed_rutf_round_two(scope["access"], section["round_two"]),
    }


def seed_chlorine_blocked(data, scopes):
    """The chain that is blocked, with no date anybody can stand behind.

    Evidence Action donates the chlorine in kind and imports it. The import
    was due in December and is behind, and nobody knows when it will land.

    This chain therefore lacks two things every other chain here has, and
    neither absence is an untidiness to be finished off later.

    **No quotes and no award.** Nothing was competed, because an in-kind
    donation is not a purchase, and an award would record a decision that was
    never made. The round is still here, because the DEMAND is real -- 400
    jerry cans are needed -- and demand going unmet is the thing the screen
    exists to show. A round with no award is not a half-finished sourcing
    exercise; it is an accurate account of one that has not happened.

    **No `promised_lead_time_days`.** `stock/services/network.py`
    `_expected_inbound` derives `expected_on` from `signed_on` plus a promised
    lead time, and with neither it returns None -- correctly, because there is
    no date to return. The stock page says so in words rather than trailing
    off after the donor's name. Adding a lead time here to make the row look
    complete would delete the only beat this chain exists for (design
    section 6a, beat 8b).

    Takes `scopes` rather than building its own access, for the reason
    `seed_supply_only` records: this scope's catalogue was seeded from its own
    section by `seed_scopes`, and calling `seed_reference` again would put
    every chain's products into this one program.
    """
    scope = scopes["chlorine"]
    access, reference = scope["access"], scope["reference"]
    section = without_commentary(data["chlorine_blocked"])

    donor_org = reference["orgs"][section["donor_slug"]]
    # `donor`, not `distributor`: records.SUPPLIER_TYPES carries the word for
    # exactly this relationship, and Evidence Action sells us nothing.
    donor = supplier_for_org(access, donor_org, kind=section["supplier"]["type"])

    # Tier 2. We wrote this down ourselves, from the agreement we are party to.
    ours = {
        "source": "we_recorded",
        "recorded_by_org_id": reference["orgs"][section["programme_org_slug"]]["id"],
    }

    round_ = tender_for(access, section["round"])
    round_ = opened(access, round_)

    # One store or several. The import is delivered to the FIRST; any others
    # are the partners it restocks, so they sit on the map owed the same
    # blocked goods. `store` (one) is still read, so an older document seeds.
    rows = section.get("stores") or [section["store"]]
    store = _supply_point(access, rows[0], reference, ours)
    stores = [store] + [
        _supply_point(access, {**row, "parent_supply_point_id": store["id"]}, reference, ours) for row in rows[1:]
    ]

    contract_row = dict(section["contract"])
    buyer_slug = contract_row.pop("buyer_org_slug")
    line = section["round"]["lines"][0]
    contract = op(
        access,
        "contract_create",
        data={
            **ours,
            **contract_row,
            "tender_id": round_["id"],
            "supplier_id": donor["id"],
            "commodity_slug": line["commodity_slug"],
            "buyer_org_id": reference["orgs"][buyer_slug]["id"],
            "delivery_supply_point_id": store["id"],
            # No `unit_price`: it is a donation, and MONEY_NONZERO would
            # refuse a zero anyway -- rightly, since "free" is a
            # consideration, not a price of nought.
            # No `signed_on` and no `promised_lead_time_days`. See the
            # docstring; this is the whole point.
        },
    )

    return {
        "program_id": CHLORINE_PROGRAM_ID,
        "round": round_,
        "supplier": donor,
        "store": store,
        "stores": stores,
        "contract": contract,
    }


def seed_history(access, data, reference, chain, last_mile):
    """The months before the demo's own story: what moved, and what was dispensed.

    Cover -- months of stock -- is a balance over a RATE, and a rate needs at
    least thirty days of releases or dispensing (`resupply.py`). The chain
    above is weeks old, so without this every place's cover reads "cannot be
    said" and a map coloured by it is grey. These rows are the previous
    cycle: an opening balance at the warehouse, releases to the partners, the
    partner's runs out to its workers, and the workers dispensing.

    Every row is the document's, named by place: `warehouse`, a partner's org
    slug, or a worker's slug. Figures live in Drive, never here.
    """
    rows = without_commentary((data.get("chc_chain") or {}).get("history") or [])
    if not rows:
        return []
    orgs = reference["orgs"]
    program_org = orgs[chain_programme_org(data)]
    item = chain["context"]["item"]
    places = {"warehouse": chain["warehouse"], **chain["partner_points"], **(last_mile or {}).get("points", {})}

    def place(name):
        if name is None:
            return None
        if name not in places:
            raise ValueError(f"history names {name!r}, which is no place in this chain")
        return places[name]["id"]

    posted = []
    for row in rows:
        data_row = {
            "kind": row["kind"],
            "occurred_on": day(row["days_ago"]),
            "commodity_slug": item["commodity_slug"],
            "item_id": item["id"],
            "quantity": row["quantity"],
            "quantity_unit": row["quantity_unit"],
            "reference": row.get("reference", ""),
            "source": row.get("source", "partner_reported"),
            "recorded_by_org_id": program_org["id"],
        }
        if row.get("from") is not None:
            data_row["from_supply_point_id"] = place(row["from"])
        if row.get("to") is not None:
            data_row["to_supply_point_id"] = place(row["to"])
        posted.append(op(access, "movement_record", data=data_row))
    return posted


def seed_on_the_road(access, data, reference, chain):
    """Stock the distributor has sent and a partner has not yet received.

    The movement the rest of the chain cannot show: every release above is
    already in the partner's store, so nothing between the warehouse and a
    partner office was ever on its way. A consignment is (models.Consignment):
    it leaves the warehouse's stock at once and does not count at the office
    until it arrives, so the map draws it moving and the office's stock page
    reports it as in transit, never as cover.

    Tier 1 -- the distributor told us, and we typed it: the partner links do
    not yet carry a consignment, and a row written as theirs would claim a
    hand that did not write it.

    A row with no `expected_days_ago` is dispatched with no date given, which
    is a real state and is shown as one.
    """
    rows = without_commentary((data.get("chc_chain") or {}).get("on_the_road") or [])
    if not rows:
        return []
    orgs = reference["orgs"]
    program_org = orgs[chain_programme_org(data)]
    their_word = {"source": "partner_reported", "recorded_by_org_id": program_org["id"]}
    warehouse = chain["warehouse"]
    item = chain["context"]["item"]
    sent = []
    for row in rows:
        destination = chain["partner_points"].get(row["to_org_slug"])
        if destination is None:
            raise ValueError(f"on_the_road names {row['to_org_slug']!r}, which runs no store in this chain")
        payload = {
            "from_supply_point_id": warehouse["id"],
            "to_supply_point_id": destination["id"],
            "commodity_slug": item["commodity_slug"],
            "item_id": item["id"],
            "quantity": row["quantity"],
            "quantity_unit": row["quantity_unit"],
            "dispatched_on": day(row["dispatched_days_ago"]),
            "reference": row.get("reference", ""),
            "carrier": row.get("carrier", ""),
            **their_word,
        }
        if row.get("expected_days_ago") is not None:
            payload["expected_on"] = day(row["expected_days_ago"])

        # Found before it is dispatched, like everything else this seeder
        # writes -- and this one matters more than most. A consignment MOVES
        # stock: it takes the quantity out of the warehouse and into the
        # programme's in-transit point. A duplicate is therefore not a spare
        # row, it is the warehouse balance dropping a second time for goods
        # that only ever left once, and a lorry on the map that never existed.
        #
        # Keyed on the document's own reference where it gives one, and on
        # where-to-where-and-when otherwise, because a consignment leaving the
        # same store for the same store on the same day is this seeder
        # repeating itself rather than two real lorries.
        already = _found(
            op(access, "consignment_list", supply_point_id=destination["id"]),
            lambda c, p=payload: (
                c.get("reference") == p["reference"]
                if p["reference"]
                else c.get("from_supply_point_id") == p["from_supply_point_id"]
                and c.get("dispatched_on") == p["dispatched_on"]
            ),
        )
        sent.append(already or op(access, "consignment_dispatch", data=payload))
    return sent


def seed_chc_last_mile(access, data, reference, chain):
    """Beat 10: the ledger runs to the worker, and the worker answers back.

    This is the close, and it only means anything against beat 9. The
    supply-only organisation's chain stops at its last store because nothing
    binds it to Connect -- no `opportunity_id`, no user-held points -- and
    `summary._deliver()` reads that off the data rather than being told. Ours
    does not stop, and this is what makes the difference real rather than
    asserted: a field worker IS a supply point, so distributing to one is an
    ordinary ledger movement, and the count they submit sits beside the
    balance instead of overwriting it.

    **The disagreement is the point, not a flaw in the seed.** One worker's
    report matches, one is nine cartons short, and one is at zero. If all
    three agreed there would be nothing to look at, and if all three differed
    the variance would read as noise in the screen rather than as a finding
    about a worker. `stock_on_hand` returns both figures for exactly this
    reason, and its own summary says they routinely disagree.

    **A zero is recorded, not skipped.** A worker with nothing left is a
    stockout, which is the single most actionable row on the page --
    `_STOCK_COUNT_DATA` takes the zero-accepting quantity here while the
    money schemas refuse a zero, and that difference is deliberate.

    The counts carry `source: connect_visit` because that is the kind of row
    they stand for. In this environment nothing was ingested -- see the
    document's own `_provenance_warning`, which says so in the one place a
    reader will look before repeating it to a funder.
    """
    section = without_commentary(data["chc_last_mile"])
    orgs = reference["orgs"]
    program_org = orgs[chain_programme_org(data)]
    ours = {"source": "we_recorded", "recorded_by_org_id": program_org["id"]}

    store = chain["partner_points"][_store_org_slug(data, section)]
    opportunity_id = section["opportunity_id"]
    commodity_slug = section["commodity_slug"]
    item_id = chain["context"]["item"]["id"]

    # The workers first: a distribution line names where it went, and a line
    # naming a worker who is not a supply point yet has nowhere to put the
    # stock.
    points = {}
    for worker in section["workers"]:
        points[worker["slug"]] = op(
            access,
            "supply_point_upsert",
            data={
                **ours,
                "slug": worker["slug"],
                "name": worker["name"],
                "kind": "user_held",
                "connect_username": worker["connect_username"],
                "opportunity_id": opportunity_id,
                # `parent_supply_point_id` is the operation's own name for it;
                # `parent_id` was silently overridden, so the workers had no
                # store to be restocked from.
                "parent_supply_point_id": store["id"],
                "managed_by_org_id": store.get("managed_by_org_id") or program_org["id"],
                # The band the worker is managed to, when the document gives
                # one: what lets cover read as low or fine rather than unknown.
                **{
                    key: worker[key]
                    for key in ("min_months_of_stock", "max_months_of_stock")
                    if worker.get(key) is not None
                },
            },
        )

    distribution = op(
        access,
        "distribution_record",
        data={
            **ours,
            "supply_point_id": store["id"],
            "opportunity_id": opportunity_id,
            "commodity_slug": commodity_slug,
            "distributed_on": day(section["distributed_on_days_ago"]),
            "reference": "RESUPPLY-CHC-01",
            "lines": [
                {
                    "to_supply_point_id": points[worker["slug"]]["id"],
                    "item_id": item_id,
                    "quantity": worker["distributed"],
                    "quantity_unit": "carton",
                }
                for worker in section["workers"]
            ],
        },
    )

    # What each worker then said. Recorded BY us against their username,
    # because nothing was really ingested -- the honest stamp for a row this
    # seeder wrote, with the kind it stands for named in `source`.
    counts = [
        op(
            access,
            "stock_count_record",
            data={
                "recorded_by_org_id": program_org["id"],
                "source": "connect_visit",
                "supply_point_id": points[worker["slug"]]["id"],
                "item_id": item_id,
                "commodity_slug": commodity_slug,
                "kind": "self_reported",
                "counted_on": day(section["counted_on_days_ago"]),
                "quantity": worker["reported"],
                "quantity_unit": "carton",
                "opportunity_id": opportunity_id,
                "connect_username": worker["connect_username"],
            },
        )
        for worker in section["workers"]
    ]

    return {"points": points, "distribution": distribution, "counts": counts}


def chain_programme_org(data):
    """The org that records the programme's own rows, from the CHC chain."""
    return data["chc_chain"]["programme_org_slug"]


def _store_org_slug(data, section):
    """Which partner store the resupply runs out of, by its own slug.

    The document names the store by `store_slug` (a supply-point slug) but
    `seed_chain` returns partner points keyed by ORGANISATION slug, because
    that is the resolution a row naming `to_org_slug` needs. One lookup
    reconciles the two rather than making the document say it twice and risk
    the two drifting.
    """
    wanted = section["store_slug"]
    for row in data["chc_chain"]["partner_points"]:
        if row["slug"] == wanted:
            return row["org_slug"]
    raise ValueError(
        f"chc_last_mile resupplies from {wanted!r}, which is not one of the CHC chain's "
        "partner_points; name a store that exists or add it there"
    )


def seed_awaiting_approval(access, data, reference):
    """Beat 5: an award that cannot become an order yet, and says who is holding it.

    An award is a decision; an order is a commitment. Somebody other than the
    decider has to agree before the second follows the first, and
    `_require_approved_award` REFUSES `contract_create` while any approval on
    the award is requested or declined -- naming whose answer is outstanding.
    That is a rule the database enforces, not a label on a screen, which is
    the only reason this beat is worth showing at all.

    **It needed a round of its own.** The CHC and RUTF awards both already
    carry orders, and an approval asked for after the goods were bought would
    have shown the trail while quietly inverting the point: the gate is that
    the purchase has NOT happened. So this is the next quarter's top-up,
    awarded and waiting.

    **The approval is deliberately left unanswered.** Deciding it would tidy
    the screen and delete the beat. The document says so beside the data, in
    `approval._why_pending`, because the temptation to "finish" a pending row
    is exactly what a later reader will feel.

    Stops at the award and records no contract. That is not an omission this
    function could correct even if it wanted to -- the write would be refused,
    which is the whole demonstration.
    """
    section = without_commentary(data["awaiting_approval"])
    orgs = reference["orgs"]
    program_org = orgs[section["programme_org_slug"]]
    ours = {"source": "we_recorded", "recorded_by_org_id": program_org["id"]}

    supplier = _chain_supplier(access, section, orgs)
    round_ = tender_for(access, section["round"])
    round_ = opened(access, round_)

    quotes, items = [], {}
    for quoted in section["quotes"]:
        quoted = dict(quoted)
        item = op(
            access,
            "item_upsert",
            data={**quoted.pop("item"), "commodity_slug": quoted["commodity_slug"]},
        )
        items[item["sku"]] = item
        already = _found(
            op(access, "quote_list", tender_id=round_["id"]),
            lambda row, s=supplier, i=item: row.get("supplier_id") == s["id"] and row.get("item_id") == i["id"],
        )
        quotes.append(
            already
            or op(
                access,
                "quote_record",
                data={
                    **quoted,
                    "tender_id": round_["id"],
                    "supplier_id": supplier["id"],
                    "item_id": item["id"],
                },
            )
        )

    index = section["awarded_quote_index"]
    award = _found(op(access, "award_list", tender_id=round_["id"]), lambda row: True) or op(
        access,
        "award_create",
        tender_id=round_["id"],
        quote_id=quotes[index]["id"],
        rationale=section["award_rationale"],
        decided_on=day(section["awarded_days_ago"]),
        **({"decided_by": section["award_decided_by"]} if section.get("award_decided_by") else {}),
    )

    asked = section["approval"]
    # An approval asked for twice reads as two approvers waiting, which would
    # make the gate look worse than it is. Keyed on who was asked and in what
    # role -- a second request from the SAME approver in the same role is how
    # this domain records a reversal, so it is only a duplicate when the
    # seeder makes it.
    asked_of = orgs[asked["approver_org_slug"]]["id"]
    approval = _found(
        op(access, "approval_list", award_id=award["id"]),
        lambda row: row.get("approver_org_id") == asked_of and row.get("role") == asked["role"],
    ) or op(
        access,
        "approval_request",
        data={
            **ours,
            "award_id": award["id"],
            "approver_org_id": orgs[asked["approver_org_slug"]]["id"],
            "role": asked["role"],
            "requested_on": day(asked["requested_days_ago"]),
            **({"note": asked["note"]} if asked.get("note") else {}),
        },
    )

    return {
        "round": round_,
        "supplier": supplier,
        "items": items,
        "quotes": quotes,
        "award": award,
        "approval": approval,
    }


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
    nothing. Anything but the redirect is raised, carrying the errors as the
    page printed them (`_why`, which reads the page rather than the test
    client's `response.context` -- that is empty outside pytest).
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


def _visible(html):
    """One line of the text a person would have read, entities and all."""
    return unescape(" ".join(re.sub(r"<[^>]+>", " ", html).split()))


def _why(response):
    """What the page said was wrong, read off the page it rendered.

    Out of the HTML, and deliberately NOT out of `response.context`. That
    attribute is filled from the `template_rendered` signal, which is only
    ever SENT by the instrumented renderer `setup_test_environment()` installs
    -- so it is there under pytest and is `None` in a `manage.py shell` on the
    deployment. Reading it would have made this helper articulate in tests and
    silent on the one run that matters: the seed against a real program,
    refused by a scope or a form, which is the entire reason it exists.

    Two shapes, because the page prints two: a field's own error, which crispy
    gives an id naming the field, and a refusal of the whole submission, which
    the page heads "Not saved:".
    """
    body = response.content.decode(errors="replace")
    errors = [
        f"{field.partition('-')[2] or field}: {_visible(message)}"
        for field, message in re.findall(r'<p id="error_\d+_id_([^"]+)"[^>]*>\s*<strong>(.*?)</strong>', body, re.S)
    ]
    errors += [_visible(message) for message in re.findall(r"<strong>Not saved:</strong>(.*?)</p>", body, re.S)]
    return " / ".join(error for error in errors if error) or (
        "the page printed no error -- it may not have been the page at all (a wrong host is answered "
        "before the view, with a 400)"
    )


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
    program buys from every quarter actually holds one: an order placed next
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
