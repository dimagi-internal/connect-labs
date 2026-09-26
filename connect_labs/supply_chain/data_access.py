"""Persistence for the supply domain, over the labs database.

This used to wrap `LabsRecordAPIClient` and write every record through to
production Connect. It now talks to real tables (see models.py for why), but
it keeps the same class name and the same method names, because those are
what the operation registry and the services call: swapping the storage
should not ripple into 30-odd call sites.

What it still owns, and what it deliberately does not:

  - **Scoping.** Reference data (commodities, items, suppliers) is
    shared across a programme's tenders, and ideally across an organisation's
    programmes. Which of the two you get depends on the caller, so the choice
    is made here, once, via `scope_key`.
  - **Authorising that scope.** Because the labs database is the system of
    record, no downstream Connect call stands behind a caller-supplied
    organisation or programme. The constructor consults
    `labs/access/scopes.py` -- the same caller resolution it already uses for
    attribution, finally used to decide as well as to record.
  - **Referential existence.** An orphan quote pointing at a tender that does
    not exist would never appear in any comparison -- invisible rather than
    merely wrong -- so the checks raise with the name of the thing missing.
  - **Not deduplication.** A client that submits the same quote twice can
    void one; labs does not guess which was meant. That is why `void_quote`
    exists and no uniqueness constraint does.
"""

import logging
from datetime import date

from django.core.exceptions import PermissionDenied

from connect_labs.labs.access.scopes import Caller, may_use
from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain import scopes
from connect_labs.supply_chain.fulfilment.repository import FulfilmentRepositoryMixin
from connect_labs.supply_chain.models import (
    Award,
    Commodity,
    Consignment,
    Contract,
    Distribution,
    DistributionLine,
    Document,
    Invoice,
    Item,
    Movement,
    Outreach,
    Quote,
    Receipt,
    Shipment,
    StockCount,
    Supplier,
    SupplyPoint,
    Tender,
    fill_profile,
    scope_key,
)
from connect_labs.supply_chain.procurement.repository import ProcurementRepositoryMixin
from connect_labs.supply_chain.reference.repository import ReferenceRepositoryMixin
from connect_labs.supply_chain.stock.repository import StockRepositoryMixin

logger = logging.getLogger(__name__)

GTIN_FIELDS = ("gtin_base", "gtin_pack", "gtin_case")

# Keys some repository method resolves to a model instance itself, so that a
# bad id produces an error naming the thing rather than a foreign-key
# violation naming a column. `_columns` must skip these or they would be set
# twice, once as an id and once as an object.
#
# Anything NOT listed here passes through as a raw id -- which is the point:
# an earlier version matched only field *names*, so every `*_id` a JSON
# caller sent was silently discarded. `recorded_by_org_id` is on every
# provenance-bearing write in the domain, and it was being dropped.
_RESOLVED = {
    "commodity_slug",
    "tender_id",
    "supplier_id",
    "item_id",
    "quote_id",
    "buyer_org_id",
    "supply_point_id",
    "parent_supply_point_id",
    "managed_by_org_id",
    "from_supply_point_id",
    "to_supply_point_id",
    "contract_id",
    "shipment_id",
    "receipt_id",
    "invoice_id",
    "payee_org_id",
}

# Never settable by a caller: the identity and the audit timestamps.
_NOT_SETTABLE = {"id", "pk", "created_at", "updated_at", "scope_key", "program_id"}


def _listing(data: dict, tender=None) -> dict:
    """An organisation's listing fields, checked where every write lands.

    The slug is the listing's address, so it is normalised and must be free;
    a clash is refused by name rather than as a database error. An empty slug
    clears it (the tender stops having a listing address).
    """
    from django.utils.text import slugify

    data = dict(data)
    if "slug" in data:
        slug = slugify(data.get("slug") or "")[:80]
        if slug:
            taken = Tender.objects.filter(slug=slug)
            if tender is not None:
                taken = taken.exclude(pk=tender.pk)
            if taken.exists():
                raise ValueError(f"the address /t/{slug}/ is already another tender's; choose another")
        data["slug"] = slug or None
    return data


def _delivery(data: dict) -> dict:
    """A tender's delivery places, however the caller named them.

    A caller written when a tender had exactly one place still sends
    `delivery_point` (a dict, with the Incoterm inside it); that becomes a
    one-place `delivery_points` list and a top-level `incoterm_requested`.
    Every place gets a stable `key` -- a supplier's bid names the places its
    price covers by key, so a place renamed later is still the same place.
    """
    data = dict(data)
    single = data.pop("delivery_point", None)
    if single is not None and "delivery_points" not in data:
        single = dict(single)
        incoterm = single.pop("incoterm_requested", None)
        if incoterm and "incoterm_requested" not in data:
            data["incoterm_requested"] = incoterm
        single = {k: v for k, v in single.items() if v}
        data["delivery_points"] = [single] if single else []
    if "delivery_points" in data:
        from django.utils.text import slugify

        keyed, used = [], set()
        for index, point in enumerate(data["delivery_points"] or [], start=1):
            point = {k: v for k, v in dict(point).items() if v not in (None, "")}
            if not (point.get("name") or point.get("city")):
                continue
            key = point.get("key") or slugify(point.get("name") or point.get("city")) or f"place-{index}"
            base, n = key, 2
            while key in used:
                key, n = f"{base}-{n}", n + 1
            used.add(key)
            keyed.append({**point, "key": key})
        data["delivery_points"] = keyed
    return data


def _check_delivery(tender, mode: str, keys) -> None:
    """A bid's delivery must be one the tender offers.

    A delivered bid names only places the tender lists; a collected bid is
    refused unless the tender accepts collection, and names no places. Checked
    here, where every write lands, not only on the form that offers the choice.
    """
    if mode == "pickup":
        if not tender.pickup_accepted:
            raise ValueError("this tender does not accept collection from the supplier")
        if keys:
            raise ValueError("a collected bid names no delivery places")
        return
    listed = {p.get("key") for p in tender.delivery_points or []}
    unknown = [key for key in keys if key not in listed]
    if unknown:
        raise ValueError(f"the tender lists no delivery place {', '.join(unknown)}")


def _columns(model, data: dict) -> dict:
    """The keys of `data` that this model can actually be given.

    Two kinds count: a field's name, and a relation's attname -- `award_id`,
    `recorded_by_org_id`, `delivery_supply_point_id`. The attnames matter
    because that is how a JSON caller names a relation, and an earlier
    version matched names only, so every `*_id` sent over the API was
    silently discarded. `recorded_by_org_id` rides on every
    provenance-bearing write in the domain, and it was being dropped.

    Anything unrecognised is ignored rather than stored: a typo'd field name
    that persists reads back as absent forever, which looks like data loss
    and is nearly impossible to spot.
    """
    nullable = {}
    for field in model._meta.get_fields():
        if not hasattr(field, "attname"):
            continue
        nullable[field.name] = field.null
        nullable[field.attname] = field.null

    out = {}
    for key, value in data.items():
        if key not in nullable or key in _RESOLVED or key in _NOT_SETTABLE:
            continue
        # A None against a non-nullable column means "the caller did not
        # supply this", not "set it to NULL". Operations give optional
        # arguments a default of None, so passing it through turned every
        # omitted string into an integrity error on a column whose real
        # default is "". Leaving the key out lets the field's own default
        # apply. A caller CAN still clear a genuinely nullable field.
        if value is None and not nullable[key]:
            continue
        out[key] = value
    return out


def _fresh(obj):
    """Re-read a just-written row so its values have the database's types.

    `Model.objects.create(amount="50.00")` leaves the string on the
    in-memory instance -- a DecimalField only coerces on load. Every
    derivation downstream does arithmetic on these values, and `"50.00" * 3`
    is a silent disaster, so a write hands back the loaded row rather than
    the one it was given.
    """
    obj.refresh_from_db()
    return obj


def _refuse_a_price_on_what_is_not_bought(consideration, unit_price):
    """A unit price on a donation is two statements that cannot both be true.

    Coherence, not policing: the landed cost of an in-kind contract is "not
    purchased", and a price stored beside that would either be ignored
    silently or contradict the statement the page makes.
    """
    if consideration != "priced" and unit_price not in (None, ""):
        label = "in kind" if consideration == "in_kind" else "bundled into another cost"
        raise ValueError(
            f"this contract's goods are {label}, so it takes no unit price; set consideration "
            "to priced if the goods are being bought"
        )


def _copy_of(obj, overrides: dict) -> dict:
    """Every plain column of `obj`, with `overrides` applied on top.

    Used to version a quote: the replacement starts as a copy of the
    original so a correction naming one field does not blank the other
    thirty, which is what a partial write against a fresh row would do.
    Relations and identity are excluded -- the caller sets those explicitly.
    """
    plain = {
        field.name: getattr(obj, field.name)
        for field in obj._meta.fields
        if not field.is_relation and field.name not in _NOT_SETTABLE
    }
    return {**plain, **overrides}


class SupplyDataAccess(
    FulfilmentRepositoryMixin,
    ProcurementRepositoryMixin,
    ReferenceRepositoryMixin,
    StockRepositoryMixin,
):
    """One object, one scope, every tier.

    The tiers are mixins rather than separate access classes so that a client
    -- a view, an API request, an MCP call -- carries a single scoped object
    and the operation handlers stay uniformly `access.<verb>`. Each mixin
    lives with the tier it serves.
    """

    def __init__(
        self,
        access_token: str | None = None,
        organization_id=None,
        program_id=None,
        opportunity_id=None,
        request=None,
        user=None,
        caller: Caller | None = None,
    ):
        if request is not None and hasattr(request, "labs_context"):
            context = request.labs_context
            organization_id = organization_id or context.get("organization_id")
            program_id = program_id or context.get("program_id")
            opportunity_id = opportunity_id or context.get("opportunity_id")

        # Authorise the scope AFTER the labs_context merge, so what gets checked
        # is what this object will actually query with -- a scope inherited from
        # the session is exactly as caller-supplied as one passed by argument.
        #
        # This is the only place that can do it. The labs database is this app's
        # system of record, so unlike every other labs app there is no downstream
        # LabsRecord call at which Connect would check membership: an unchecked
        # program_id here reaches Postgres and is answered. A caller that cannot
        # be resolved is refused rather than trusted; `SYSTEM` is the explicit,
        # greppable escape for entry points that have no user.
        #
        # `organization_id` is deliberately NOT authorised. It selects nothing --
        # mcp_tools.py's own schema says so: "Connect organisation, carried as
        # context. It does NOT select a registry: commodities, trade items and
        # suppliers are scoped to the programme." Checking a value that reaches
        # no query protects nothing and only adds a way to refuse a legitimate
        # caller, so only the scopes that actually select data are checked.
        denied = may_use(
            caller,
            program_id=program_id,
            opportunity_id=opportunity_id,
        )
        if denied:
            raise PermissionDenied(denied)

        # Kept on the signature because every caller passes it and because a
        # future sync back to Connect will need it again. Nothing in this
        # class uses it now: the labs database is the system of record.
        self.access_token = access_token
        self.organization_id = organization_id
        self.program_id = program_id
        self.opportunity_id = opportunity_id
        # Kept so a write can derive WHO recorded it rather than believe what
        # the payload claims (design doc section 27). The request is preferred
        # where there is one: its organisation list was fetched at login and
        # includes the labs-only synthetic orgs an entitled user can see, so
        # resolution costs no round trip. `user` is the MCP route, which has a
        # Connect token but no session.
        self.request = request
        self.user = user or getattr(request, "user", None)

    # ---- synthetic scopes ------------------------------------------------

    @property
    def is_synthetic(self) -> bool:
        """Whether this programme's supply data is labs-only demo data.

        Derived from the programme id rather than stored on each row, so it
        cannot drift: a row cannot claim to be real while sitting under a
        synthetic programme. See scopes.py for what this does and does not
        permit -- notably, synthetic data is written through the same
        operations with the same validation, because a seeder that can write
        a shape the API would reject demos a system that does not exist.
        """
        return scopes.is_synthetic(self.program_id)

    def purge(self) -> dict:
        """Delete every supply row for this programme. Synthetic scopes only.

        What a seeder's `--reset` needs, and the reason `scopes.require_synthetic`
        exists: the guard is here, at the only place that can do it, rather
        than in each caller that might forget.
        """
        program_id = self._require_program()
        scopes.require_synthetic(program_id, "purge supply data")

        counts: dict[str, int] = {}

        def drop(label, queryset):
            """Record how many of THIS model went, not the cascade total.

            `QuerySet.delete()` returns every row it touched, children
            included, so deleting one distribution with one line reported
            "2 distributions". The per-model breakdown is the number a reader
            of `--reset` output is actually looking for.
            """
            _, by_model = queryset.delete()
            own = by_model.get(queryset.model._meta.label, 0)
            if own:
                counts[label] = own

        # Sever every protected reference BEFORE deleting anything. This
        # graph is PROTECT-heavy on purpose -- that is what stops a stray
        # delete orphaning a ledger in normal use -- but it means there is no
        # delete ORDER that works, because several pairs protect each other in
        # both directions: a Movement points at its Distribution while that
        # Distribution's lines point back at the Movement, and a worker's
        # supply point names the store above it as its parent. So unlink
        # first, then delete, rather than weakening the constraints that make
        # the ledger trustworthy the rest of the time.
        movements = Movement.objects.filter(program_id=program_id)
        DistributionLine.objects.filter(distribution__program_id=program_id).update(movement=None)
        StockCount.objects.filter(program_id=program_id).update(adjustment_movement=None)
        movements.update(distribution=None, receipt=None, shipment=None, stock_count=None)
        Contract.objects.filter(program_id=program_id).update(duty_relief_document=None)
        Quote.objects.filter(tender__program_id=program_id).update(superseded_by=None)
        # Self-references are the same problem one table in: a worker's
        # holding names the store above it as its parent, so no ordering of
        # SupplyPoint deletes can work either.
        SupplyPoint.objects.filter(program_id=program_id).update(parent=None, managed_by_org=None)

        # Links and alerts reach the programme's rows through join tables and
        # nullable FKs, so no cascade below takes them: a link would outlive
        # its contracts, still listed as working and still accepted at its URL.
        # Imported here: both apps import this module.
        from connect_labs.supply_chain.alerts.models import AlertSubscription
        from connect_labs.supply_chain.update_links.models import UpdateLink

        drop("update links", UpdateLink.objects.filter(program_id=program_id))
        drop("alert subscriptions", AlertSubscription.objects.filter(program_id=program_id))
        drop("documents", Document.objects.filter(program_id=program_id))
        drop("stock counts", StockCount.objects.filter(program_id=program_id))
        drop("distributions", Distribution.objects.filter(program_id=program_id))
        # A consignment holds its two ledger legs, so it goes before them.
        drop("consignments", Consignment.objects.filter(program_id=program_id))
        drop("movements", movements)
        drop("invoices", Invoice.objects.filter(contract__program_id=program_id))
        drop("receipts", Receipt.objects.filter(supply_point__program_id=program_id))
        drop("shipments", Shipment.objects.filter(contract__program_id=program_id))
        drop("contracts", Contract.objects.filter(program_id=program_id))
        drop("awards", Award.objects.filter(tender__program_id=program_id))
        drop("quotes", Quote.objects.filter(tender__program_id=program_id))
        drop("outreach", Outreach.objects.filter(tender__program_id=program_id))
        drop("tenders", Tender.objects.filter(program_id=program_id))
        drop("supply points", SupplyPoint.objects.filter(program_id=program_id))
        # Reference data is scoped to the programme, so purging the programme
        # purges it. This used to be guarded on the scope being a programme
        # rather than an organisation, because an organisation's registry
        # could be shared with a programme that is not being purged. There is
        # no organisation tier any more (see models.scope_key), so the guard
        # was always true and the branch it protected unreachable.
        key = self.scope_key
        drop("items", Item.objects.filter(scope_key=key))
        drop("suppliers", Supplier.objects.filter(scope_key=key))
        drop("commodities", Commodity.objects.filter(scope_key=key))
        return counts

    # ---- scoping --------------------------------------------------------

    @property
    def scope_key(self) -> str:
        """Which reference registry this caller reads and writes.

        The programme, always. `organization_id` is carried on this object as
        Connect context for a future sync, and is deliberately NOT part of
        this: see `models.scope_key` for what having both did.
        """
        return scope_key(program_id=self.program_id)

    def _require_program(self) -> int:
        """A tender, quote, award or contract has no meaning outside a programme.

        Refusing is better than defaulting: a stand-in scope would make
        records written by different callers indistinguishable, which is
        exactly the cross-programme leak the scoping exists to prevent.
        """
        if self.program_id is None:
            raise ValueError(
                "SupplyDataAccess has no program_id: procurement and fulfilment records "
                "(tenders, outreach, quotes, awards, contracts) require a programme scope "
                "and cannot be read or written without one"
            )
        return self.program_id

    # ---- reference tier -------------------------------------------------

    def _reference(self, model):
        return model.objects.filter(scope_key=self.scope_key)

    def list_commodities(self):
        return list(self._reference(Commodity).all())

    def _kit_components(self, components) -> list[dict]:
        """A kit's contents, each naming a product that exists in this catalogue.

        Checked here because existence needs a read: a component pointing at
        a product nobody defined could never be tested against a
        specification, and "no requirement to fail" would read as a pass.
        The quantity is stored as a decimal string, the way every quantity in
        this domain goes over the wire, so a composition compares equal
        whether it arrived as 10 or "10".
        """
        from decimal import Decimal

        from connect_labs.supply_chain.values import decimal_string

        cleaned = []
        for component in components:
            slug = component["commodity_slug"]
            if self.get_commodity(slug) is None:
                raise ValueError(
                    f"component {slug!r} is not a product in this catalogue; add it as a product "
                    "first, so its specification can be checked"
                )
            entry = {
                "commodity_slug": slug,
                "quantity": decimal_string(Decimal(str(component["quantity"]))),
                "base_unit": component["base_unit"],
            }
            if component.get("spec_attributes"):
                entry["spec_attributes"] = component["spec_attributes"]
            cleaned.append(entry)
        return cleaned

    def mark_supplier_reviewed(self, supplier_id: int):
        """Say somebody on the program team has looked at a self-registered supplier."""
        supplier = self.get_supplier(supplier_id)
        if supplier is None:
            raise ValueError(f"supplier {supplier_id} not found")
        supplier.reviewed_on = date.today()
        user = self.user if getattr(self.user, "is_authenticated", False) else None
        supplier.reviewed_by = user
        supplier.save(update_fields=["reviewed_on", "reviewed_by", "updated_at"])
        return _fresh(supplier)

    def _edit_company(self, org, data):
        identity = {k: data[k] for k in ("name", "country") if data.get(k) not in (None, "")}
        changed = {k: v for k, v in identity.items() if getattr(org, k) != v}
        # Filling a blank is adding what we know; only replacing a value
        # somebody else set is refused.
        replaced = {k for k in changed if getattr(org, k)}
        if replaced and org.connect_organization_id is not None:
            raise ValueError(
                f"{org.name} is named by Connect (organisation {org.connect_organization_id}); "
                "its name and country are changed there, not here"
            )
        if "name" in replaced and hasattr(org, "marketplace_profile"):
            raise ValueError(
                f"{org.name} is in the LLO directory, which names it; rename it in the directory sheet "
                "and the next import will carry the change"
            )
        if changed:
            for key, value in changed.items():
                setattr(org, key, value)
            org.save(update_fields=[*changed, "updated_at"])
        fill_profile(org, data, overwrite=True)

    def list_orgs(self):
        return list(LabsOrg.objects.all())

    def get_org(self, org_id: int):
        return LabsOrg.objects.filter(pk=org_id).first()

    def upsert_org(self, data: dict):
        """Record an organisation.

        Not programme-scoped, unlike the reference tier around it. An
        organisation is the same organisation in every programme it appears
        in, and scoping it per programme is what produced three registries of
        the same thing.

        `kind` and `roles` are accepted and not stored: what an organisation
        IS to a purchase is a fact about the purchase
        (`Contract.buyer_of_record`), not about the organisation. Storing it
        here let one body be "the programme" in a way that could not be true
        in a second programme.
        """
        fields = {k: v for k, v in data.items() if k not in ("slug", "kind", "roles", "contacts")}
        columns = _columns(LabsOrg, fields)

        # Located by the Connect id FIRST where there is one, because that is
        # the identity and the slug is not. Keying only on the slug meant a
        # linked organisation that had been RENAMED looked like a new row, and
        # the insert then hit the unique constraint on connect_organization_id
        # -- a rename failing as a database error rather than updating a name.
        connect_id = columns.get("connect_organization_id")
        existing = LabsOrg.objects.filter(connect_organization_id=connect_id).first() if connect_id else None
        if existing is None:
            existing = LabsOrg.objects.filter(slug=data["slug"]).first()

        if existing is None:
            obj = LabsOrg.objects.create(slug=data["slug"], **columns)
            return _fresh(obj)
        for key, value in {**columns, "slug": data["slug"]}.items():
            setattr(existing, key, value)
        existing.save()
        return _fresh(existing)

    # ---- resolvers ------------------------------------------------------
    #
    # Existence needs a read, so it belongs here rather than to a schema
    # validator, and the message names the thing that is missing.

    def _resolve_commodity(self, slug):
        if slug is None:
            return None
        commodity = self.get_commodity(slug)
        if commodity is None:
            raise ValueError(f"commodity {slug!r} does not exist")
        return commodity

    def _require_commodity(self, slug):
        return self._resolve_commodity(slug)

    def _resolve_supplier(self, supplier_id):
        if supplier_id is None:
            return None
        supplier = self.get_supplier(supplier_id)
        if supplier is None:
            raise ValueError(f"supplier {supplier_id} does not exist")
        return supplier

    def _resolve_item(self, item_id):
        if item_id is None:
            return None
        item = self.get_item(item_id)
        if item is None:
            raise ValueError(f"item {item_id} does not exist")
        return item

    # ---- tenders ---------------------------------------------------------

    def _contracts(self):
        return Contract.objects.filter(program_id=self._require_program()).select_related("commodity")

    def list_contracts(self, tender_id=None, status=None):
        qs = self._contracts()
        if tender_id is not None:
            qs = qs.filter(tender_id=tender_id)
        if status is not None:
            qs = qs.filter(status=status)
        return list(qs.all())

    def get_contract(self, contract_id):
        return self._contracts().filter(pk=contract_id).first()

    def create_contract(self, data):
        """The commitment. Separate from the award, which is only a decision.

        `buyer_org_id` must name an organisation that exists, because the whole
        point of the field is that somebody other than us may be buying --
        and a contract whose buyer is a dangling id cannot answer the one
        question it was added to answer.
        """
        buyer = self.get_org(data["buyer_org_id"])
        if buyer is None:
            raise ValueError(f"organisation {data['buyer_org_id']} does not exist")
        _refuse_a_price_on_what_is_not_bought(data.get("consideration") or "priced", data.get("unit_price"))
        if data.get("award_id") is not None:
            self._require_approved_award(data["award_id"])
        if data.get("covers_shortfall_of_id") is not None:
            self._require_contract_to_cover(data["covers_shortfall_of_id"], covering_id=None)
        return _fresh(
            Contract.objects.create(
                program_id=self._require_program(),
                tender=self._resolve_tender(data.get("tender_id")),
                commodity=self._require_commodity(data["commodity_slug"]),
                supplier=self._resolve_supplier(data.get("supplier_id")),
                item=self._resolve_item(data.get("item_id")),
                buyer_org=buyer,
                **_columns(Contract, data),
            )
        )

    def _require_contract_to_cover(self, short_id, covering_id):
        """The short order a covering one names: in this programme, and not itself."""
        if covering_id is not None and int(short_id) == covering_id:
            raise ValueError("an order cannot cover its own shortfall; name the order that came up short")
        if self.get_contract(short_id) is None:
            raise ValueError(f"contract {short_id} does not exist in this programme")

    def update_contract(self, contract_id, data):
        found = self.get_contract(contract_id)
        if found is None:
            raise ValueError(f"contract {contract_id} not found")
        if data.get("award_id") is not None and data["award_id"] != found.award_id:
            self._require_approved_award(data["award_id"])
        if data.get("covers_shortfall_of_id") is not None:
            self._require_contract_to_cover(data["covers_shortfall_of_id"], covering_id=found.pk)
        for key, value in _columns(Contract, data).items():
            setattr(found, key, value)
        _refuse_a_price_on_what_is_not_bought(found.consideration, found.unit_price)
        found.save()
        return _fresh(found)
