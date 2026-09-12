"""LabsRecord persistence for the supply domain.

Two client instances, not one. LabsRecordAPIClient.create_record() stamps
program_id and opportunity_id onto every payload from the client instance
whenever they are set, so a single client carrying both scopes would write
reference data pinned to one programme.

The reference tier picks its scope HERE and nowhere else. The client coerces
int(organization_id) unconditionally, and labs_context hands us a slug for
labs-only synthetic organisations, so a hard organisation scope would raise
ValueError in the environment this app is developed in. A numeric id gets an
organisation-scoped registry shared across the org's programmes; anything else
falls back to programme scope. Each reference record records which tier wrote
it, so the fallback ones can be lifted later.

Reads pass BARE filter kwargs: get_records() prepends data__ itself.
"""

import logging
from datetime import UTC, datetime

from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient
from connect_labs.labs.models import LocalLabsRecord
from connect_labs.labs.synthetic.models import LABS_ONLY_OPP_ID_FLOOR
from connect_labs.supply_chain import gs1, records
from connect_labs.supply_chain.models import (
    AwardRecord,
    CommodityRecord,
    ItemRecord,
    OutreachRecord,
    PurchaseRecord,
    QuoteRecord,
    RoundRecord,
    SupplierRecord,
)

logger = logging.getLogger(__name__)

GTIN_FIELDS = ("gtin_base", "gtin_pack", "gtin_case")


def _as_int(value):
    """int, or None for anything that is not one. Never raises."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _routing_opportunity_id(value):
    """The opportunity_id to hand LabsRecordAPIClient's programme client, or None.

    LabsRecordAPIClient cannot tell a routing hint from a real scope: once its
    opportunity_id is set, create_record stamps it onto every payload and
    get_records sends it on every read. A real (below-floor) opportunity_id is
    NEVER a legitimate scope for a programme-wide procurement tier -- a round
    written while opp A was selected would go silently unreadable the moment
    opp B in the same programme was selected instead. Only a labs-only
    synthetic id (>= LABS_ONLY_OPP_ID_FLOOR) is the routing hint the parameter
    exists for, so only that value is passed through.
    """
    numeric = _as_int(value)
    if numeric is not None and numeric >= LABS_ONLY_OPP_ID_FLOOR:
        return numeric
    return None


class SupplyDataAccess:
    def __init__(
        self,
        access_token: str,
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

        self.organization_id = organization_id
        self.program_id = program_id
        self.opportunity_id = opportunity_id

        # Procurement tier: programme scope. opportunity_id is passed to the
        # client ONLY when it is a labs-only synthetic id -- the routing hint
        # that lets LabsRecordAPIClient dispatch to the local backend instead
        # of prod. A real opportunity_id is a genuine scope to the client, not
        # a hint, so passing one through would silently narrow every
        # round/quote/award/purchase to that one opportunity within the
        # programme. See _routing_opportunity_id.
        self.program_client = LabsRecordAPIClient(
            access_token=access_token,
            program_id=program_id,
            opportunity_id=_routing_opportunity_id(opportunity_id),
        )

        # Reference tier: organisation scope when the identifier is numeric, so a
        # commodity written for one programme is readable from another. A slug
        # cannot be used — the client would raise on int() — so we fall back to
        # the programme client rather than crash.
        numeric_org = _as_int(organization_id)
        if numeric_org is not None:
            self.reference_scope = "organization"
            self.reference_client = LabsRecordAPIClient(
                access_token=access_token,
                organization_id=numeric_org,
            )
        else:
            self.reference_scope = "program"
            self.reference_client = self.program_client

    # ---- experiment keys ------------------------------------------------

    @property
    def reference_experiment(self) -> str:
        return f"{records.EXPERIMENT_PREFIX}:reference"

    @property
    def program_experiment(self) -> str:
        """The procurement tier's experiment key -- never a stand-in.

        str(None) would silently collapse every programme-less caller onto the
        experiment "None", making rounds/quotes/awards/purchases written by
        different callers indistinguishable -- exactly the cross-programme leak
        the two-tier scoping exists to prevent. A round, a quote and an award
        have no meaning outside a programme, so refuse rather than invent one.
        """
        if self.program_id is None:
            raise ValueError(
                "SupplyDataAccess has no program_id: procurement records "
                "(rounds, outreach, quotes, awards, purchases) require a "
                "programme scope and cannot be read or written without one"
            )
        return str(self.program_id)

    # ---- reference tier -------------------------------------------------

    def list_commodities(self) -> list[CommodityRecord]:
        return self.reference_client.get_records(
            experiment=self.reference_experiment,
            type=records.COMMODITY_TYPE,
            model_class=CommodityRecord,
        )

    def get_commodity(self, slug: str) -> CommodityRecord | None:
        found = self.reference_client.get_records(
            experiment=self.reference_experiment,
            type=records.COMMODITY_TYPE,
            model_class=CommodityRecord,
            slug=slug,
        )
        return found[0] if found else None

    def _reference_data(self, data: dict) -> dict:
        """Stamp which tier wrote this, so a fallback registry can be lifted later."""
        return {**data, "reference_scope": self.reference_scope}

    def upsert_commodity(self, data: dict) -> CommodityRecord:
        """Create or update, and hand back the typed record either way.

        create_record/update_record return a bare LocalLabsRecord (neither
        takes model_class), which has no typed properties -- so the write
        itself is re-read through get_commodity rather than returned directly.
        """
        existing = self.get_commodity(data["slug"])
        if existing:
            self.reference_client.update_record(
                record_id=existing.id,
                experiment=self.reference_experiment,
                type=records.COMMODITY_TYPE,
                data={**existing.data, **self._reference_data(data)},
                current_record=existing,
            )
        else:
            self.reference_client.create_record(
                experiment=self.reference_experiment,
                type=records.COMMODITY_TYPE,
                data=self._reference_data(data),
            )
        return self.get_commodity(data["slug"])

    # ---- items: the master item list -----------------------------------

    def list_items(self) -> list[ItemRecord]:
        return self.reference_client.get_records(
            experiment=self.reference_experiment,
            type=records.ITEM_TYPE,
            model_class=ItemRecord,
        )

    def items_by_id(self) -> dict:
        """{record_id: ItemRecord} — what compare_round wants."""
        return {item.id: item for item in self.list_items()}

    def get_item(self, item_id: int) -> ItemRecord | None:
        return self.reference_client.get_record_by_id(
            record_id=item_id,
            experiment=self.reference_experiment,
            type=records.ITEM_TYPE,
            model_class=ItemRecord,
        )

    def get_item_by_sku(self, sku: str) -> ItemRecord | None:
        found = self.reference_client.get_records(
            experiment=self.reference_experiment,
            type=records.ITEM_TYPE,
            model_class=ItemRecord,
            sku=sku,
        )
        return found[0] if found else None

    def upsert_item(self, data: dict) -> ItemRecord:
        """Create or update a trade item by SKU, validating any GS1 keys.

        A mistyped GTIN that silently persists is worse than a rejected one, and
        a mod-10 check digit is the cheapest guard there is. An item with no GTIN
        at all is perfectly normal and passes.
        """
        for field_name in GTIN_FIELDS:
            value = data.get(field_name)
            if value and not gs1.is_valid(str(value)):
                raise ValueError(f"{field_name} {value!r} fails its GS1 check digit")

        existing = self.get_item_by_sku(data["sku"])
        if existing:
            self.reference_client.update_record(
                record_id=existing.id,
                experiment=self.reference_experiment,
                type=records.ITEM_TYPE,
                data={**existing.data, **self._reference_data(data)},
                current_record=existing,
            )
        else:
            self.reference_client.create_record(
                experiment=self.reference_experiment,
                type=records.ITEM_TYPE,
                data=self._reference_data(data),
            )
        return self.get_item_by_sku(data["sku"])

    def list_suppliers(self, search: str | None = None) -> list[SupplierRecord]:
        found = self.reference_client.get_records(
            experiment=self.reference_experiment,
            type=records.SUPPLIER_TYPE,
            model_class=SupplierRecord,
        )
        if not search:
            return found
        needle = search.lower().strip()
        return [
            supplier
            for supplier in found
            if needle in (supplier.name or "").lower()
            or any(needle in (c.get("email") or "").lower() for c in supplier.contacts)
        ]

    def get_supplier(self, supplier_id: int) -> SupplierRecord | None:
        return self.reference_client.get_record_by_id(
            record_id=supplier_id,
            experiment=self.reference_experiment,
            type=records.SUPPLIER_TYPE,
            model_class=SupplierRecord,
        )

    def create_supplier(self, data: dict) -> LocalLabsRecord:
        """Returns the bare write result, honestly: create_record takes no
        model_class, so this is never actually a typed SupplierRecord."""
        return self.reference_client.create_record(
            experiment=self.reference_experiment,
            type=records.SUPPLIER_TYPE,
            data=self._reference_data(data),
        )

    def update_supplier(self, supplier_id: int, data: dict) -> LocalLabsRecord:
        """Returns the bare write result, honestly -- see create_supplier."""
        existing = self.get_supplier(supplier_id)
        if existing is None:
            raise ValueError(f"supplier {supplier_id} not found")
        return self.reference_client.update_record(
            record_id=supplier_id,
            experiment=self.reference_experiment,
            type=records.SUPPLIER_TYPE,
            data={**existing.data, **self._reference_data(data)},
            current_record=existing,
        )

    # ---- procurement tier ----------------------------------------------

    def _list(self, record_type, model_class, **filters):
        return self.program_client.get_records(
            experiment=self.program_experiment,
            type=record_type,
            model_class=model_class,
            **{k: v for k, v in filters.items() if v is not None},
        )

    def _create(self, record_type, data):
        return self.program_client.create_record(
            experiment=self.program_experiment,
            type=record_type,
            data=data,
        )

    def _get(self, record_type, record_id, model_class):
        return self.program_client.get_record_by_id(
            record_id=record_id,
            experiment=self.program_experiment,
            type=record_type,
            model_class=model_class,
        )

    def _update(self, record_type, record_id, data, current_record=None):
        return self.program_client.update_record(
            record_id=record_id,
            experiment=self.program_experiment,
            type=record_type,
            data=data,
            current_record=current_record,
        )

    def _require_round(self, round_id):
        """Existence needs a read, so it belongs here, not to a schema validator.

        An orphan quote/outreach/award/purchase pointing at a non-existent
        round would never appear in any comparison -- it would be invisible,
        not just wrong.
        """
        if self.get_round(round_id) is None:
            raise ValueError(f"round {round_id} does not exist")

    def _require_commodity(self, commodity_slug):
        if self.get_commodity(commodity_slug) is None:
            raise ValueError(f"commodity {commodity_slug!r} does not exist")

    def list_rounds(self):
        return self._list(records.ROUND_TYPE, RoundRecord)

    def get_round(self, round_id):
        return self._get(records.ROUND_TYPE, round_id, RoundRecord)

    def create_round(self, data):
        return self._create(records.ROUND_TYPE, {"status": "draft", **data})

    def update_round(self, round_id, data):
        existing = self.get_round(round_id)
        if existing is None:
            raise ValueError(f"round {round_id} not found")
        return self._update(records.ROUND_TYPE, round_id, {**existing.data, **data}, current_record=existing)

    def open_round(self, round_id):
        """A round cannot open without a delivery point.

        Suppliers will not quote without knowing where the goods go, because
        freight dominates the price — so an open round that cannot say is not
        a round anyone can answer.
        """
        existing = self.get_round(round_id)
        if existing is None:
            raise ValueError(f"round {round_id} not found")
        point = existing.delivery_point or {}
        if not point.get("city") and not point.get("name"):
            raise ValueError("a round needs a delivery point before it can open")
        return self._update(records.ROUND_TYPE, round_id, {**existing.data, "status": "open"}, current_record=existing)

    def close_round(self, round_id):
        existing = self.get_round(round_id)
        if existing is None:
            raise ValueError(f"round {round_id} not found")
        return self._update(
            records.ROUND_TYPE, round_id, {**existing.data, "status": "closed"}, current_record=existing
        )

    def list_outreach(self, round_id=None):
        return self._list(records.OUTREACH_TYPE, OutreachRecord, round_id=round_id)

    def create_outreach(self, data):
        self._require_round(data["round_id"])
        return self._create(records.OUTREACH_TYPE, data)

    def update_outreach(self, outreach_id, data):
        existing = self._get(records.OUTREACH_TYPE, outreach_id, OutreachRecord)
        if existing is None:
            raise ValueError(f"outreach {outreach_id} not found")
        return self._update(records.OUTREACH_TYPE, outreach_id, {**existing.data, **data}, current_record=existing)

    def list_quotes(self, round_id=None):
        return self._list(records.QUOTE_TYPE, QuoteRecord, round_id=round_id)

    def get_quote(self, quote_id):
        return self._get(records.QUOTE_TYPE, quote_id, QuoteRecord)

    def create_quote(self, data):
        self._require_round(data["round_id"])
        self._require_commodity(data["commodity_slug"])
        return self._create(records.QUOTE_TYPE, {"version": 1, **data})

    def supersede_quote(self, quote_id, data, reason):
        """Corrections create a new version; the original stays readable.

        Overwriting would make a past award's comparison_snapshot
        unreproducible, and a comparison shown to a funder has to stay
        reconstructible.
        """
        existing = self.get_quote(quote_id)
        if existing is None:
            raise ValueError(f"quote {quote_id} not found")
        if not reason:
            raise ValueError("a correction needs a reason")

        new_data = {
            **existing.data,
            **data,
            "version": (existing.version or 1) + 1,
            "supersedes_quote_id": quote_id,
            "correction_reason": reason,
            "superseded_by_quote_id": None,
        }
        created = self._create(records.QUOTE_TYPE, new_data)
        try:
            self._update(
                records.QUOTE_TYPE,
                quote_id,
                {**existing.data, "superseded_by_quote_id": created.id},
                current_record=existing,
            )
        except Exception:
            # The replacement already exists; if THIS write fails, both
            # versions read as live and the superseded quote re-enters
            # comparisons until someone notices and fixes the back-link by
            # hand. That's worth a loud log, not a swallowed exception.
            logger.error(
                "supersede_quote: created replacement quote %s for quote %s but "
                "failed to back-link the original -- both versions currently "
                "read as live",
                created.id,
                quote_id,
            )
            raise
        return created

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
        return self._update(
            records.QUOTE_TYPE,
            quote_id,
            {**existing.data, "voided": True, "void_reason": reason},
            current_record=existing,
        )

    def list_awards(self, round_id=None):
        return self._list(records.AWARD_TYPE, AwardRecord, round_id=round_id)

    def create_award(self, data):
        if not data.get("rationale"):
            raise ValueError("an award needs a rationale")
        self._require_round(data["round_id"])
        return self._create(
            records.AWARD_TYPE,
            {"decided_on": datetime.now(UTC).isoformat(), **data},
        )

    def list_purchases(self):
        return self._list(records.PURCHASE_TYPE, PurchaseRecord)

    def create_purchase(self, data):
        self._require_round(data["round_id"])
        self._require_commodity(data["commodity_slug"])
        return self._create(records.PURCHASE_TYPE, data)
