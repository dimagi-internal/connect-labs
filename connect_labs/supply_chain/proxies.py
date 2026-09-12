"""Proxy models over LocalLabsRecord for the LabsRecord-backed procurement tier.

BEING RETIRED. The supply domain is moving to first-class Django models in
models.py (real tables in the labs DB, real foreign keys, SQL aggregation for
the stock ledger). These proxies stay until data_access.py is ported onto the
ORM, so the tree keeps working through the move; they hold no rules, so the
port is mechanical.


Read-only typed access to record.data. These classes hold no rules: every
derived figure comes from services/, so there is exactly one place to look
for how a number was produced.

LocalLabsRecord is transient and cannot be .save()d — persistence is
LabsRecordAPIClient's job, via data_access.py.
"""

from decimal import Decimal, InvalidOperation

from connect_labs.labs.models import LocalLabsRecord


def _decimal(raw) -> Decimal | None:
    """Decimal, or None for absent/unparseable. Never a silent zero."""
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


class _Base(LocalLabsRecord):
    def _get(self, key, default=None):
        return self.data.get(key, default)


class CommodityRecord(_Base):
    @property
    def slug(self):
        return self._get("slug")

    @property
    def name(self):
        return self._get("name", "")

    @property
    def category(self):
        return self._get("category")

    @property
    def base_unit(self):
        return self._get("base_unit")

    @property
    def pack_unit(self):
        return self._get("pack_unit")

    @property
    def base_per_pack(self):
        return self._get("base_per_pack")

    @property
    def base_unit_grams(self):
        return self._get("base_unit_grams")

    @property
    def course_definition(self):
        return self._get("course_definition") or {}

    @property
    def base_units_per_course(self):
        """None when the programme has not entered its treatment protocol."""
        return self.course_definition.get("base_units_per_course")

    @property
    def spec_requirements(self):
        return self._get("spec_requirements") or []

    @property
    def shelf_life_months_minimum(self):
        return self._get("shelf_life_months_minimum")

    @property
    def gpc_brick(self):
        return self._get("gpc_brick")

    @property
    def unspsc(self):
        return self._get("unspsc")


class ItemRecord(_Base):
    """A trade item — the specific, pack-configured thing that is counted and scanned.

    `commodity` says what kind of thing it is; this says which one. Two suppliers'
    RUTF can be 144 and 150 sachets per carton, and that difference is invisible at
    commodity level while silently corrupting every per-sachet comparison.
    """

    @property
    def sku(self):
        return self._get("sku")

    @property
    def name(self):
        return self._get("name", "")

    @property
    def commodity_slug(self):
        return self._get("commodity_slug")

    @property
    def manufacturer(self):
        return self._get("manufacturer", "")

    @property
    def brand(self):
        return self._get("brand", "")

    @property
    def base_unit(self):
        return self._get("base_unit")

    @property
    def base_per_pack(self):
        return self._get("base_per_pack")

    @property
    def pack_unit(self):
        return self._get("pack_unit")

    @property
    def pack_per_case(self):
        return self._get("pack_per_case")

    @property
    def base_unit_grams(self):
        return self._get("base_unit_grams")

    @property
    def gross_weight_kg(self):
        return _decimal(self._get("gross_weight_kg"))

    @property
    def gtin_base(self):
        return self._get("gtin_base")

    @property
    def gtin_pack(self):
        return self._get("gtin_pack")

    @property
    def gtin_case(self):
        return self._get("gtin_case")

    @property
    def unicef_material_no(self):
        return self._get("unicef_material_no")

    @property
    def unspsc(self):
        return self._get("unspsc")

    @property
    def atc(self):
        return self._get("atc")

    @property
    def gpc_brick(self):
        return self._get("gpc_brick")

    @property
    def spec_attributes(self):
        """The item's actual specification — durable fact, unlike a quote's claim."""
        return self._get("spec_attributes") or {}

    @property
    def shelf_life_months(self):
        return self._get("shelf_life_months")

    @property
    def status(self):
        return self._get("status", "active")

    @property
    def source(self):
        return self._get("source") or {}


class SupplierRecord(_Base):
    @property
    def name(self):
        return self._get("name", "")

    @property
    def supplier_type(self):
        return self._get("type")

    @property
    def country(self):
        return self._get("country")

    @property
    def city(self):
        return self._get("city")

    @property
    def origin_note(self):
        return self._get("origin_note", "")

    @property
    def contacts(self):
        return self._get("contacts") or []

    @property
    def qualifications(self):
        return self._get("qualifications") or []

    @property
    def connect_organization_id(self):
        return self._get("connect_organization_id")

    @property
    def gln(self):
        """GS1 Global Location Number, when the supplier has one."""
        return self._get("gln")

    @property
    def status(self):
        return self._get("status", "identified")

    @property
    def status_reason(self):
        return self._get("status_reason", "")


class RoundRecord(_Base):
    @property
    def label(self):
        return self._get("label", "")

    @property
    def status(self):
        return self._get("status", "draft")

    @property
    def lines(self):
        return self._get("lines") or []

    @property
    def delivery_point(self):
        return self._get("delivery_point") or {}

    @property
    def response_deadline(self):
        return self._get("response_deadline")

    @property
    def reminder_interval_days(self):
        return self._get("reminder_interval_days")

    @property
    def notes_to_supplier(self):
        return self._get("notes_to_supplier", "")

    @property
    def shelf_life_months_minimum(self):
        return self._get("shelf_life_months_minimum")

    def quantity_for(self, commodity_slug):
        """(quantity, unit) for a commodity on this round, or None."""
        for line in self.lines:
            if line.get("commodity_slug") == commodity_slug:
                quantity = _decimal(line.get("quantity"))
                if quantity is None:
                    return None
                return quantity, line.get("quantity_unit")
        return None


class OutreachRecord(_Base):
    @property
    def round_id(self):
        return self._get("round_id")

    @property
    def supplier_id(self):
        return self._get("supplier_id")

    @property
    def contact_email_used(self):
        return self._get("contact_email_used")

    @property
    def sent_on(self):
        return self._get("sent_on")

    @property
    def channel(self):
        return self._get("channel", "manual")

    @property
    def request_text_rendered(self):
        return self._get("request_text_rendered", "")

    @property
    def responded(self):
        return bool(self._get("responded", False))

    @property
    def responded_on(self):
        return self._get("responded_on")

    @property
    def response_kind(self):
        return self._get("response_kind")


class QuoteRecord(_Base):
    @property
    def round_id(self):
        return self._get("round_id")

    @property
    def supplier_id(self):
        return self._get("supplier_id")

    @property
    def commodity_slug(self):
        return self._get("commodity_slug")

    @property
    def as_quoted_amount(self):
        return _decimal(self._get("as_quoted_amount"))

    @property
    def as_quoted_currency(self):
        return self._get("as_quoted_currency", "USD")

    @property
    def as_quoted_unit(self):
        return self._get("as_quoted_unit")

    @property
    def quantity_basis(self):
        return _decimal(self._get("quantity_basis"))

    @property
    def quantity_basis_unit(self):
        return self._get("quantity_basis_unit")

    @property
    def item_id(self):
        """The trade item this quote is for, when the supplier named one."""
        return self._get("item_id")

    @property
    def pack_spec_source(self):
        """Where the pack configuration came from, if anywhere.

        stated_on_quote      — the supplier wrote the sachets-per-carton down
        trade_item_confirmed — the supplier identified a known trade item, which
                               states it just as surely
        not_stated           — nobody has said; every per-sachet figure is Unconfirmed

        Defaults to not_stated: the honest reading of a quote that was silent has
        to be the cheapest one to record.
        """
        return self._get("pack_spec_source", "not_stated")

    @property
    def base_per_pack_stated(self):
        return self._get("base_per_pack_stated")

    @property
    def base_unit_grams_stated(self):
        return self._get("base_unit_grams_stated")

    @property
    def freight_basis(self):
        return self._get("freight_basis", "not_specified")

    @property
    def freight_amount(self):
        return _decimal(self._get("freight_amount"))

    @property
    def duties_basis(self):
        return self._get("duties_basis", "not_specified")

    @property
    def duties_amount(self):
        return _decimal(self._get("duties_amount"))

    @property
    def duties_note(self):
        return self._get("duties_note", "")

    @property
    def incoterm(self):
        return self._get("incoterm")

    @property
    def delivery_point_quoted(self):
        return self._get("delivery_point_quoted") or {}

    @property
    def shelf_life_months_stated(self):
        return self._get("shelf_life_months_stated")

    @property
    def production_or_expiry_date_stated(self):
        return self._get("production_or_expiry_date_stated")

    @property
    def moq(self):
        return _decimal(self._get("moq"))

    @property
    def moq_unit(self):
        return self._get("moq_unit")

    @property
    def lead_time_days(self):
        return self._get("lead_time_days")

    @property
    def validity_until(self):
        return self._get("validity_until")

    @property
    def stated_spec(self):
        return self._get("stated_spec") or {}

    @property
    def fx_rate_to_usd(self):
        return _decimal(self._get("fx_rate_to_usd"))

    @property
    def fx_rate_as_of(self):
        return self._get("fx_rate_as_of")

    @property
    def version(self):
        return self._get("version", 1)

    @property
    def supersedes_quote_id(self):
        return self._get("supersedes_quote_id")

    @property
    def superseded_by_quote_id(self):
        return self._get("superseded_by_quote_id")

    @property
    def correction_reason(self):
        return self._get("correction_reason", "")

    @property
    def voided(self):
        return bool(self._get("voided", False))

    @property
    def void_reason(self):
        return self._get("void_reason", "")

    @property
    def source(self):
        return self._get("source") or {}


class AwardRecord(_Base):
    @property
    def round_id(self):
        return self._get("round_id")

    @property
    def quote_id(self):
        return self._get("quote_id")

    @property
    def decided_on(self):
        return self._get("decided_on")

    @property
    def decided_by(self):
        return self._get("decided_by")

    @property
    def rationale(self):
        return self._get("rationale", "")

    @property
    def comparison_snapshot(self):
        return self._get("comparison_snapshot") or {}


class PurchaseRecord(_Base):
    @property
    def round_id(self):
        return self._get("round_id")

    @property
    def supplier_id(self):
        return self._get("supplier_id")

    @property
    def commodity_slug(self):
        return self._get("commodity_slug")

    @property
    def llo_name(self):
        return self._get("llo_name", "")

    @property
    def quantity(self):
        return _decimal(self._get("quantity"))

    @property
    def quantity_unit(self):
        return self._get("quantity_unit")

    @property
    def amount_paid(self):
        return _decimal(self._get("amount_paid"))

    @property
    def currency(self):
        return self._get("currency", "USD")

    @property
    def paid_on(self):
        return self._get("paid_on")
