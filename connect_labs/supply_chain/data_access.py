"""Persistence for the supply domain, over the labs database.

This used to wrap `LabsRecordAPIClient` and write every record through to
production Connect. It now talks to real tables (see models.py for why), but
it keeps the same class name and the same method names, because those are
what the operation registry and the services call: swapping the storage
should not ripple into 30-odd call sites.

What it still owns, and what it deliberately does not:

  - **Scoping.** Reference data (commodities, items, suppliers, parties) is
    shared across a programme's rounds, and ideally across an organisation's
    programmes. Which of the two you get depends on the caller, so the choice
    is made here, once, via `scope_key`.
  - **Referential existence.** An orphan quote pointing at a round that does
    not exist would never appear in any comparison -- invisible rather than
    merely wrong -- so the checks raise with the name of the thing missing.
  - **Not deduplication.** A client that submits the same quote twice can
    void one; labs does not guess which was meant. That is why `void_quote`
    exists and no uniqueness constraint does.
"""

import logging
from datetime import date

from django.db import transaction

from connect_labs.supply_chain import gs1, scopes
from connect_labs.supply_chain.models import (
    Award,
    Commodity,
    Contract,
    Distribution,
    Document,
    Invoice,
    Item,
    Movement,
    Outreach,
    Party,
    Quote,
    Receipt,
    Round,
    Shipment,
    StockCount,
    Supplier,
    SupplyPoint,
    scope_key,
)
from connect_labs.supply_chain.stock.repository import StockRepositoryMixin

logger = logging.getLogger(__name__)

GTIN_FIELDS = ("gtin_base", "gtin_pack", "gtin_case")

# Keys a caller may send that are not columns. `commodity_slug` and the
# various *_id fields are resolved to relations; the rest are dropped rather
# than silently persisted somewhere they will never be read from again.
_RESOLVED = {"commodity_slug", "round_id", "supplier_id", "item_id", "quote_id", "buyer_party_id"}

# Never settable by a caller: the identity and the audit timestamps.
_NOT_SETTABLE = {"id", "pk", "created_at", "updated_at", "scope_key", "program_id"}


def _as_int(value):
    """int, or None for anything that is not one. Never raises."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _columns(model, data: dict) -> dict:
    """Only the keys that are actual fields on `model`.

    A caller that sends an unknown key gets it ignored rather than stored: a
    typo'd field name used to land in the JSON blob and read back as absent
    forever, which looked like data loss and was impossible to spot.
    """
    names = {f.name for f in model._meta.get_fields() if hasattr(f, "attname")}
    return {
        key: value for key, value in data.items() if key in names and key not in _RESOLVED and key not in _NOT_SETTABLE
    }


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


class SupplyDataAccess(StockRepositoryMixin):
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
    ):
        if request is not None and hasattr(request, "labs_context"):
            context = request.labs_context
            organization_id = organization_id or context.get("organization_id")
            program_id = program_id or context.get("program_id")
            opportunity_id = opportunity_id or context.get("opportunity_id")

        # Kept on the signature because every caller passes it and because a
        # future sync back to Connect will need it again. Nothing in this
        # class uses it now: the labs database is the system of record.
        self.access_token = access_token
        self.organization_id = organization_id
        self.program_id = program_id
        self.opportunity_id = opportunity_id

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
        than in each caller that might forget. Deletion order follows the
        foreign keys inward-out, since the relations are PROTECTed precisely
        so that a partial delete cannot silently orphan a ledger.
        """
        program_id = self._require_program()
        scopes.require_synthetic(program_id, "purge supply data")

        counts: dict[str, int] = {}

        def drop(label, queryset):
            deleted, _ = queryset.delete()
            if deleted:
                counts[label] = deleted

        drop("documents", Document.objects.filter(program_id=program_id))
        drop("stock counts", StockCount.objects.filter(program_id=program_id))
        drop("movements", Movement.objects.filter(program_id=program_id))
        drop("distributions", Distribution.objects.filter(program_id=program_id))
        drop("invoices", Invoice.objects.filter(contract__program_id=program_id))
        drop("receipts", Receipt.objects.filter(supply_point__program_id=program_id))
        drop("shipments", Shipment.objects.filter(contract__program_id=program_id))
        drop("contracts", Contract.objects.filter(program_id=program_id))
        drop("awards", Award.objects.filter(round__program_id=program_id))
        # Quotes reference each other through supersession, so the back-link
        # goes first or the delete trips its own PROTECT.
        Quote.objects.filter(round__program_id=program_id).update(superseded_by=None)
        drop("quotes", Quote.objects.filter(round__program_id=program_id))
        drop("outreach", Outreach.objects.filter(round__program_id=program_id))
        drop("rounds", Round.objects.filter(program_id=program_id))
        drop("supply points", SupplyPoint.objects.filter(program_id=program_id))
        # Reference data is shared across programmes when the scope is an
        # organisation, so it is only purged when this programme owns it.
        if self.reference_scope == "program":
            key = self.scope_key
            drop("items", Item.objects.filter(scope_key=key))
            drop("suppliers", Supplier.objects.filter(scope_key=key))
            drop("commodities", Commodity.objects.filter(scope_key=key))
            drop("parties", Party.objects.filter(scope_key=key))
        return counts

    # ---- scoping --------------------------------------------------------

    @property
    def reference_scope(self) -> str:
        return "organization" if _as_int(self.organization_id) is not None else "program"

    @property
    def scope_key(self) -> str:
        return scope_key(organization_id=_as_int(self.organization_id), program_id=self.program_id)

    def _require_program(self) -> int:
        """A round, quote, award or contract has no meaning outside a programme.

        Refusing is better than defaulting: a stand-in scope would make
        records written by different callers indistinguishable, which is
        exactly the cross-programme leak the scoping exists to prevent.
        """
        if self.program_id is None:
            raise ValueError(
                "SupplyDataAccess has no program_id: procurement and fulfilment records "
                "(rounds, outreach, quotes, awards, contracts) require a programme scope "
                "and cannot be read or written without one"
            )
        return self.program_id

    # ---- reference tier -------------------------------------------------

    def _reference(self, model):
        return model.objects.filter(scope_key=self.scope_key)

    def list_commodities(self):
        return list(self._reference(Commodity).all())

    def get_commodity(self, slug: str):
        return self._reference(Commodity).filter(slug=slug).first()

    def upsert_commodity(self, data: dict):
        obj, _ = Commodity.objects.update_or_create(
            scope_key=self.scope_key,
            slug=data["slug"],
            defaults=_columns(Commodity, {k: v for k, v in data.items() if k != "slug"}),
        )
        return _fresh(obj)

    def list_items(self):
        return list(self._reference(Item).select_related("commodity").all())

    def items_by_id(self) -> dict:
        return {item.pk: item for item in self.list_items()}

    def get_item(self, item_id: int):
        return self._reference(Item).select_related("commodity").filter(pk=item_id).first()

    def get_item_by_sku(self, sku: str):
        return self._reference(Item).select_related("commodity").filter(sku=sku).first()

    def upsert_item(self, data: dict):
        """Create or update a trade item by SKU, validating any GS1 keys.

        A mistyped GTIN that silently persists is worse than a rejected one,
        and a mod-10 check digit is the cheapest guard there is. An item with
        no GTIN at all is perfectly normal and passes.
        """
        for field_name in GTIN_FIELDS:
            value = data.get(field_name)
            if value and not gs1.is_valid(str(value)):
                raise ValueError(f"{field_name} {value!r} fails its GS1 check digit")

        commodity = self._resolve_commodity(data.get("commodity_slug"))
        defaults = _columns(Item, {k: v for k, v in data.items() if k != "sku"})
        if commodity is not None:
            defaults["commodity"] = commodity
        obj, _ = Item.objects.update_or_create(scope_key=self.scope_key, sku=data["sku"], defaults=defaults)
        return _fresh(obj)

    def list_suppliers(self, search: str | None = None):
        found = list(self._reference(Supplier).all())
        if not search:
            return found
        needle = search.lower().strip()
        return [
            supplier
            for supplier in found
            if needle in (supplier.name or "").lower()
            or any(needle in (c.get("email") or "").lower() for c in supplier.contacts)
        ]

    def get_supplier(self, supplier_id: int):
        return self._reference(Supplier).filter(pk=supplier_id).first()

    def create_supplier(self, data: dict):
        return _fresh(Supplier.objects.create(scope_key=self.scope_key, **_columns(Supplier, data)))

    def update_supplier(self, supplier_id: int, data: dict):
        supplier = self.get_supplier(supplier_id)
        if supplier is None:
            raise ValueError(f"supplier {supplier_id} not found")
        for key, value in _columns(Supplier, data).items():
            setattr(supplier, key, value)
        supplier.save()
        return _fresh(supplier)

    def list_parties(self):
        return list(self._reference(Party).all())

    def get_party(self, party_id: int):
        return self._reference(Party).filter(pk=party_id).first()

    def upsert_party(self, data: dict):
        obj, _ = Party.objects.update_or_create(
            scope_key=self.scope_key,
            slug=data["slug"],
            defaults=_columns(Party, {k: v for k, v in data.items() if k != "slug"}),
        )
        return _fresh(obj)

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

    def _resolve_round(self, round_id):
        if round_id is None:
            return None
        found = self.get_round(round_id)
        if found is None:
            raise ValueError(f"round {round_id} does not exist")
        return found

    def _require_round(self, round_id):
        return self._resolve_round(round_id)

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

    # ---- rounds ---------------------------------------------------------

    def _rounds(self):
        return Round.objects.filter(program_id=self._require_program())

    def list_rounds(self):
        return list(self._rounds().all())

    def get_round(self, round_id):
        return self._rounds().filter(pk=round_id).first()

    def create_round(self, data):
        return _fresh(
            Round.objects.create(
                program_id=self._require_program(),
                **{"status": "draft", **_columns(Round, data)},
            )
        )

    def update_round(self, round_id, data):
        found = self.get_round(round_id)
        if found is None:
            raise ValueError(f"round {round_id} not found")
        for key, value in _columns(Round, data).items():
            setattr(found, key, value)
        found.save()
        return _fresh(found)

    def open_round(self, round_id):
        """A round cannot open without a delivery point.

        Suppliers will not quote without knowing where the goods go, because
        freight dominates the price -- so an open round that cannot say is
        not a round anyone can answer.
        """
        found = self.get_round(round_id)
        if found is None:
            raise ValueError(f"round {round_id} not found")
        point = found.delivery_point or {}
        if not point.get("city") and not point.get("name"):
            raise ValueError("a round needs a delivery point before it can open")
        found.status = "open"
        found.save(update_fields=["status", "updated_at"])
        return found

    def close_round(self, round_id):
        found = self.get_round(round_id)
        if found is None:
            raise ValueError(f"round {round_id} not found")
        found.status = "closed"
        found.save(update_fields=["status", "updated_at"])
        return found

    # ---- outreach -------------------------------------------------------

    def list_outreach(self, round_id=None):
        qs = Outreach.objects.filter(round__program_id=self._require_program())
        if round_id is not None:
            qs = qs.filter(round_id=round_id)
        return list(qs.all())

    def create_outreach(self, data):
        return _fresh(
            Outreach.objects.create(
                round=self._require_round(data["round_id"]),
                supplier=self._resolve_supplier(data.get("supplier_id")),
                **_columns(Outreach, data),
            )
        )

    def update_outreach(self, outreach_id, data):
        found = Outreach.objects.filter(round__program_id=self._require_program(), pk=outreach_id).first()
        if found is None:
            raise ValueError(f"outreach {outreach_id} not found")
        for key, value in _columns(Outreach, data).items():
            setattr(found, key, value)
        found.save()
        return _fresh(found)

    # ---- quotes ---------------------------------------------------------

    def _quotes(self):
        return Quote.objects.filter(round__program_id=self._require_program()).select_related(
            "commodity", "superseded_by", "supersedes"
        )

    def list_quotes(self, round_id=None):
        qs = self._quotes()
        if round_id is not None:
            qs = qs.filter(round_id=round_id)
        return list(qs.all())

    def get_quote(self, quote_id):
        return self._quotes().filter(pk=quote_id).first()

    def create_quote(self, data):
        return _fresh(
            Quote.objects.create(
                round=self._require_round(data["round_id"]),
                commodity=self._require_commodity(data["commodity_slug"]),
                supplier=self._resolve_supplier(data.get("supplier_id")),
                item=self._resolve_item(data.get("item_id")),
                **_columns(Quote, data),
            )
        )

    @transaction.atomic
    def supersede_quote(self, quote_id, data, reason):
        """Corrections create a new version; the original stays readable.

        Overwriting would make a past award's frozen comparison
        unreproducible, and a comparison shown to a funder has to stay
        reconstructible.

        The two writes are one transaction. They used to be two statements
        with a logged failure in between, which could leave both versions
        reading as live and the superseded quote back in comparisons; a
        rollback is strictly better than a loud log.
        """
        existing = self.get_quote(quote_id)
        if existing is None:
            raise ValueError(f"quote {quote_id} not found")
        if not reason:
            raise ValueError("a correction needs a reason")

        merged = _copy_of(
            existing,
            {
                **_columns(Quote, data),
                "version": (existing.version or 1) + 1,
                "correction_reason": reason,
            },
        )
        replacement = Quote.objects.create(
            round=existing.round,
            commodity=self._resolve_commodity(data.get("commodity_slug")) or existing.commodity,
            supplier=self._resolve_supplier(data.get("supplier_id")) or existing.supplier,
            item=self._resolve_item(data.get("item_id")) or existing.item,
            **merged,
        )
        replacement = _fresh(replacement)
        existing.superseded_by = replacement
        existing.save(update_fields=["superseded_by", "updated_at"])
        return replacement

    def void_quote(self, quote_id, reason):
        """Leave the record readable; drop it out of comparisons.

        This is how a client cleans up its own duplicates, which is why labs
        needs no duplicate prevention of its own.
        """
        existing = self.get_quote(quote_id)
        if existing is None:
            raise ValueError(f"quote {quote_id} not found")
        if not reason:
            raise ValueError("voiding a quote needs a reason")
        existing.voided = True
        existing.void_reason = reason
        existing.save(update_fields=["voided", "void_reason", "updated_at"])
        return existing

    # ---- awards ---------------------------------------------------------

    def list_awards(self, round_id=None):
        qs = Award.objects.filter(round__program_id=self._require_program()).select_related("commodity")
        if round_id is not None:
            qs = qs.filter(round_id=round_id)
        return list(qs.all())

    def create_award(self, data):
        if not data.get("rationale"):
            raise ValueError("an award needs a rationale")
        found = self._require_round(data["round_id"])
        quote = self.get_quote(data["quote_id"]) if data.get("quote_id") else None
        if data.get("quote_id") and quote is None:
            raise ValueError(f"quote {data['quote_id']} does not exist")
        award = Award.objects.create(
            round=found,
            quote=quote,
            supplier=self._resolve_supplier(data.get("supplier_id")) or (quote.supplier if quote else None),
            commodity=self._resolve_commodity(data.get("commodity_slug")) or (quote.commodity if quote else None),
            **{"decided_on": date.today(), **_columns(Award, data)},
        )
        return _fresh(award)

    # ---- contracts ------------------------------------------------------

    def _contracts(self):
        return Contract.objects.filter(program_id=self._require_program()).select_related("commodity")

    def list_contracts(self, round_id=None, status=None):
        qs = self._contracts()
        if round_id is not None:
            qs = qs.filter(round_id=round_id)
        if status is not None:
            qs = qs.filter(status=status)
        return list(qs.all())

    def get_contract(self, contract_id):
        return self._contracts().filter(pk=contract_id).first()

    def create_contract(self, data):
        """The commitment. Separate from the award, which is only a decision.

        `buyer_party_id` must name a party that exists, because the whole
        point of the field is that somebody other than us may be buying --
        and a contract whose buyer is a dangling id cannot answer the one
        question it was added to answer.
        """
        party = self.get_party(data["buyer_party_id"])
        if party is None:
            raise ValueError(f"party {data['buyer_party_id']} does not exist")
        return _fresh(
            Contract.objects.create(
                program_id=self._require_program(),
                round=self._resolve_round(data.get("round_id")),
                commodity=self._require_commodity(data["commodity_slug"]),
                supplier=self._resolve_supplier(data.get("supplier_id")),
                item=self._resolve_item(data.get("item_id")),
                buyer_party=party,
                **_columns(Contract, data),
            )
        )

    def update_contract(self, contract_id, data):
        found = self.get_contract(contract_id)
        if found is None:
            raise ValueError(f"contract {contract_id} not found")
        for key, value in _columns(Contract, data).items():
            setattr(found, key, value)
        found.save()
        return _fresh(found)
