"""First-class Django models for the supply domain.

**Why these are real tables and not LabsRecords.** The procurement tier was
built on `LabsRecord`s written through to production Connect, because that is
how labs apps normally persist: the data belongs to Connect and labs is a
client of it. Supply is different on both counts.

  1. It is *primary* data that originates here -- a stock ledger, a contract,
     a receipt, a worker's reported count. Nothing in Connect is its source.
  2. It carries no PII. The reason labs round-trips data through Connect is so
     that person-level data lives where its access controls live. A carton
     count does not need that.
  3. It needs real relational work. A balance is an aggregate over a ledger
     filtered by supply point, item and batch; average monthly consumption is
     a windowed aggregate; a three-way match is a join. Doing that over JSON
     blobs fetched by HTTP is the wrong tool.

So the labs database is the system of record for supply, and whether to sync
any of it back to Connect is a later, separate decision. Nothing here assumes
it will not happen: every model carries the Connect identifiers
(`program_id`, `opportunity_id`, `connect_username`) needed to push upward.

**Connect entities are integer ids, not foreign keys.** `program_id`,
`opportunity_id`, `organization_id` and `connect_user_id` reference rows that
live in production Connect. The `opportunity`/`program`/`organization` tables
exist in this database only to satisfy migrations and are empty (see
CLAUDE.md), so a ForeignKey to them would fail on every real id. Indexed
integers keep the query plans and drop the integrity we cannot honour anyway.

Rules live in `procurement/services/` and `stock/services/`, never here. The
one exception is arithmetic that must not be expressible two ways -- the
ledger sign convention on `MovementQuerySet` -- which is a property of the
schema, not a policy.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum

from connect_labs.supply_chain import records

# Quantities: 4 decimal places is enough for a carton, a sachet or a
# millilitre and keeps every sum exact. Money is 4 as well -- a unit price
# divided out of a lot total is routinely fractional, and rounding it at
# storage time is the silent precision loss the pricing rules refuse.
QTY = {"max_digits": 18, "decimal_places": 4}
MONEY = {"max_digits": 18, "decimal_places": 4}


def _choices(values):
    return [(v, v.replace("_", " ")) for v in values]


def scope_key(organization_id=None, program_id=None) -> str:
    """The reference tier's scope, as one indexable string.

    Reference data (commodities, items, suppliers, parties) is shared across a
    programme's rounds and ideally across an organisation's programmes. Which
    of the two we get depends on the caller: `labs_context` hands a numeric
    organisation id for a real org and a slug for a labs-only synthetic one.

    Encoding both cases in a single column rather than two nullable ones is
    deliberate: Postgres treats NULLs as distinct, so `unique_together` over
    nullable scope columns does not actually prevent duplicate slugs. A
    non-null scope_key makes the uniqueness constraint real.
    """
    if organization_id not in (None, ""):
        return f"org:{organization_id}"
    if program_id not in (None, ""):
        return f"prog:{program_id}"
    raise ValueError("reference data needs an organization_id or a program_id to be scoped by")


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SourcedModel(TimestampedModel):
    """Provenance, compulsory below the contract (design doc section 17.3).

    Between "raise the purchase order" and "receive the goods" there are five
    consecutive stages we do not witness, because the buyer of record may not
    be us. Every record in the fulfilment, network and stock tiers therefore
    says who put it here and how they knew -- and `source` has no default, so
    a caller cannot omit it and have the record read as first-hand.
    """

    source = models.CharField(max_length=32, choices=_choices(records.SOURCES))
    recorded_by_party = models.ForeignKey(
        "supply_chain.Party", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    note = models.TextField(blank=True, default="")

    class Meta:
        abstract = True

    @property
    def witnessed(self) -> bool:
        """True only for what we saw ourselves or hold a document for.

        Deliberately strict: a missing source is weaker than a partner's
        claim, not stronger.
        """
        return self.source in ("we_recorded", "document")


# ======================================================================
# Reference tier -- reused across a programme's rounds
# ======================================================================


class Party(TimestampedModel):
    """An organisation that can act in the chain, including us.

    Separate from `Supplier` on purpose. A supplier has a sourcing lifecycle
    (identified, contacted, quoting, awarded) and prequalification state; a
    party is simply an actor that can buy, receive, distribute or pay. A
    supplier that also acts -- holding consignment stock, say -- links through
    `Supplier.party` rather than being crammed into one table with two
    lifecycles.

    `connect_organization_id` is nullable because a local partner is usually
    working with us before anybody creates its Connect organisation, and
    refusing to record the party until that link exists would make the system
    unusable exactly when it is most needed.
    """

    scope_key = models.CharField(max_length=64, db_index=True)
    slug = models.SlugField(max_length=64)
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=32, choices=_choices(records.PARTY_KINDS))
    connect_organization_id = models.IntegerField(null=True, blank=True, db_index=True)
    roles = models.JSONField(default=list, blank=True)
    country = models.CharField(max_length=2, blank=True, default="")
    contacts = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scope_key", "slug"], name="uniq_party_scope_slug")]
        ordering = ["name"]
        verbose_name_plural = "parties"

    def __str__(self):
        return self.name

    @property
    def is_linked(self) -> bool:
        """Whether this party is bound to a real Connect organisation.

        An unlinked party can still record everything; the binding is what
        lets its own staff sign in and do it themselves.
        """
        return self.connect_organization_id is not None


class Commodity(TimestampedModel):
    """The type of thing bought -- RUTF, not a particular manufacturer's RUTF."""

    scope_key = models.CharField(max_length=64, db_index=True)
    slug = models.SlugField(max_length=64)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=32, blank=True, default="")
    base_unit = models.CharField(max_length=32, blank=True, default="")
    pack_unit = models.CharField(max_length=32, blank=True, default="")
    base_per_pack = models.IntegerField(null=True, blank=True)
    base_unit_grams = models.IntegerField(null=True, blank=True)
    shelf_life_months_minimum = models.IntegerField(null=True, blank=True)
    spec_requirements = models.JSONField(default=list, blank=True)
    # {base_units_per_day, days_per_course, base_units_per_course, source}.
    # Empty is the normal starting state and is why per-course figures come
    # back Unconfirmed rather than guessed -- our gap, not a supplier's.
    course_definition = models.JSONField(default=dict, blank=True)
    spec_reference = models.CharField(max_length=255, blank=True, default="")
    unicef_material_number = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scope_key", "slug"], name="uniq_commodity_scope_slug")]
        ordering = ["name"]
        verbose_name_plural = "commodities"

    def __str__(self):
        return self.name

    @property
    def base_units_per_course(self):
        return (self.course_definition or {}).get("base_units_per_course")


class Item(TimestampedModel):
    """A trade item: one manufacturer's product, with its own pack configuration.

    The layer that exists because two suppliers' RUTF can be 144 and 150 to
    the carton. A commodity cannot know that; only the item can.
    """

    scope_key = models.CharField(max_length=64, db_index=True)
    sku = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    commodity = models.ForeignKey(Commodity, on_delete=models.PROTECT, related_name="items")
    manufacturer = models.CharField(max_length=255, blank=True, default="")
    base_unit = models.CharField(max_length=32, blank=True, default="")
    pack_unit = models.CharField(max_length=32, blank=True, default="")
    base_per_pack = models.IntegerField(null=True, blank=True)
    pack_per_case = models.IntegerField(null=True, blank=True)
    base_unit_grams = models.IntegerField(null=True, blank=True)
    shelf_life_months = models.IntegerField(null=True, blank=True)
    gtin_base = models.CharField(max_length=14, blank=True, default="")
    gtin_pack = models.CharField(max_length=14, blank=True, default="")
    gtin_case = models.CharField(max_length=14, blank=True, default="")
    gpc_brick = models.CharField(max_length=16, blank=True, default="")
    spec_attributes = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, default="active", choices=_choices(("active", "discontinued")))

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scope_key", "sku"], name="uniq_item_scope_sku")]
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.sku})"

    @property
    def commodity_slug(self):
        return self.commodity.slug


class Supplier(TimestampedModel):
    scope_key = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=255)
    # `type` rather than `kind` because the sourcing services already read
    # supplier.type; renaming it here would buy consistency with Party.kind at
    # the cost of touching working, tested code for no behavioural gain.
    type = models.CharField(max_length=32, blank=True, default="")
    country = models.CharField(max_length=2, blank=True, default="")
    city = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(max_length=32, blank=True, default="identified")
    contacts = models.JSONField(default=list, blank=True)
    qualifications = models.JSONField(default=list, blank=True)
    connect_organization_id = models.IntegerField(null=True, blank=True, db_index=True)
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.SET_NULL, related_name="supplier_roles")
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# ======================================================================
# Procurement tier -- source to award. Programme-scoped.
# ======================================================================


class Round(TimestampedModel):
    program_id = models.IntegerField(db_index=True)
    label = models.CharField(max_length=255)
    status = models.CharField(max_length=16, default="draft", choices=_choices(("draft", "open", "closed", "awarded")))
    lines = models.JSONField(default=list, blank=True)
    delivery_point = models.JSONField(default=dict, blank=True)
    response_deadline = models.DateField(null=True, blank=True)
    reminder_interval_days = models.IntegerField(null=True, blank=True)
    shelf_life_months_minimum = models.IntegerField(null=True, blank=True)
    notes_to_supplier = models.TextField(blank=True, default="")
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.label

    def quantity_for(self, commodity_slug):
        """(quantity, unit) for a commodity on this round, or None.

        A single None rather than a (None, None) pair, because all three
        callers test the result for truthiness before unpacking it -- and a
        two-tuple of Nones is truthy, so returning one sends None into
        `quantity_phrase()` and raises where the caller expected a blank.
        """
        for line in self.lines or []:
            if line.get("commodity_slug") == commodity_slug:
                raw = line.get("quantity")
                if raw in (None, ""):
                    return None
                try:
                    return Decimal(str(raw)), line.get("quantity_unit")
                except InvalidOperation:
                    return None
        return None


class Outreach(TimestampedModel):
    """One RFQ invitation. A log, not a state machine -- deliberately not unique
    per (round, supplier), because re-inviting is a real event worth keeping."""

    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name="outreach")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="outreach")
    channel = models.CharField(max_length=16, blank=True, default="manual")
    sent_on = models.DateField(null=True, blank=True)
    responded = models.BooleanField(default=False)
    response_kind = models.CharField(max_length=16, blank=True, default="")
    last_reminder_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-sent_on", "-created_at"]
        indexes = [models.Index(fields=["round", "supplier"])]
        verbose_name_plural = "outreach"


class Quote(TimestampedModel):
    """What a supplier said, recorded on the supplier's own terms.

    Nothing here is normalised. `as_quoted_*` is verbatim and the `*_basis`
    and `pack_spec_source` flags record what was and was not stated, so the
    derivations can refuse to compute rather than assume. Corrections
    supersede rather than overwrite: a quote is a statement someone made, and
    editing it away loses the fact that it was made.
    """

    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name="quotes")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="quotes")
    commodity = models.ForeignKey(Commodity, on_delete=models.PROTECT, related_name="quotes")
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="quotes")

    as_quoted_amount = models.DecimalField(null=True, blank=True, **MONEY)
    as_quoted_unit = models.CharField(max_length=24, blank=True, default="")
    as_quoted_currency = models.CharField(max_length=3, default="USD")
    quantity_basis = models.DecimalField(null=True, blank=True, **QTY)
    quantity_basis_unit = models.CharField(max_length=32, blank=True, default="")

    pack_spec_source = models.CharField(max_length=24, blank=True, default="not_stated")
    base_per_pack_stated = models.IntegerField(null=True, blank=True)
    base_unit_grams_stated = models.IntegerField(null=True, blank=True)

    freight_basis = models.CharField(max_length=16, blank=True, default="not_specified")
    freight_amount = models.DecimalField(null=True, blank=True, **MONEY)
    duties_basis = models.CharField(max_length=16, blank=True, default="not_specified")
    duties_amount = models.DecimalField(null=True, blank=True, **MONEY)
    fx_rate_to_usd = models.DecimalField(null=True, blank=True, max_digits=18, decimal_places=8)

    shelf_life_months_stated = models.IntegerField(null=True, blank=True)
    moq = models.DecimalField(null=True, blank=True, **QTY)
    moq_unit = models.CharField(max_length=32, blank=True, default="")
    lead_time_days = models.IntegerField(null=True, blank=True)
    validity_until = models.DateField(null=True, blank=True)
    incoterm = models.CharField(max_length=16, blank=True, default="")
    stated_spec = models.JSONField(default=dict, blank=True)
    received_on = models.DateField(null=True, blank=True)

    voided = models.BooleanField(default=False)
    void_reason = models.TextField(blank=True, default="")
    # A correction creates a new version and back-links the old one rather
    # than overwriting it. Overwriting would make a past award's frozen
    # comparison unreproducible, and a comparison shown to a funder has to
    # stay reconstructible.
    version = models.IntegerField(default=1)
    superseded_by = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="supersedes"
    )
    correction_reason = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["round", "commodity"])]

    @property
    def commodity_slug(self):
        return self.commodity.slug

    @property
    def superseded_by_quote_id(self):
        """Named for the services that read it; the column is a self relation."""
        return self.superseded_by_id

    @property
    def supersedes_quote_id(self):
        """The quote this one replaced, when that is known without a query.

        The forward side of the supersession link is `superseded_by`, because
        that is the direction the comparison reads on every row to exclude
        stale quotes, and it must be free. This is the reverse side, so
        touching it would cost one query per quote serialised. It returns
        None unless the relation has been selected -- `data_access` selects
        it on list reads -- rather than quietly issuing that query.
        """
        cached = self._state.fields_cache.get("supersedes")
        return cached.pk if cached is not None else None

    @property
    def is_live(self) -> bool:
        return not self.voided and self.superseded_by_id is None


class Award(TimestampedModel):
    """The decision. Not a commitment -- see Contract."""

    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name="awards")
    quote = models.ForeignKey(Quote, on_delete=models.PROTECT, related_name="awards")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="awards")
    commodity = models.ForeignKey(Commodity, on_delete=models.PROTECT, related_name="awards")
    decided_on = models.DateField(null=True, blank=True)
    decided_by = models.CharField(max_length=255, blank=True, default="")
    rationale = models.TextField(blank=True, default="")
    # Frozen at the moment of the decision, including which buyer of record
    # its landed totals assumed (design doc section 17.1). Without that, a
    # replayed comparison can silently disagree with the award it justified.
    comparison_snapshot = models.JSONField(default=dict, blank=True)
    provisional = models.BooleanField(default=False)
    assumed_buyer_of_record = models.CharField(
        max_length=16, blank=True, default="", choices=_choices(records.BUYER_OF_RECORD)
    )

    class Meta:
        ordering = ["-decided_on", "-created_at"]


# ======================================================================
# Fulfilment tier -- contract to receipt
# ======================================================================


class Contract(SourcedModel):
    """The commitment, and the record that names who is actually buying.

    `buyer_of_record` has no default. Import duty and VAT depend on who
    imports, so a landed total computed without knowing the buyer would be a
    number with an invisible assumption inside it -- the exact failure mode
    the whole domain is built to refuse.
    """

    program_id = models.IntegerField(db_index=True)
    round = models.ForeignKey(Round, null=True, blank=True, on_delete=models.PROTECT, related_name="contracts")
    award = models.ForeignKey(Award, null=True, blank=True, on_delete=models.PROTECT, related_name="contracts")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="contracts")
    commodity = models.ForeignKey(Commodity, on_delete=models.PROTECT, related_name="contracts")
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="contracts")

    buyer_of_record = models.CharField(max_length=16, choices=_choices(records.BUYER_OF_RECORD))
    buyer_party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="contracts")

    # May be the partner's PO number rather than ours, which is why it is
    # nullable and why `source` says who told us it.
    reference = models.CharField(max_length=64, blank=True, default="")
    signed_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, default="draft", choices=_choices(records.CONTRACT_STATUSES))

    currency = models.CharField(max_length=3, default="USD")
    quantity = models.DecimalField(null=True, blank=True, **QTY)
    quantity_unit = models.CharField(max_length=32, blank=True, default="")
    unit_price = models.DecimalField(null=True, blank=True, **MONEY)
    unit_price_unit = models.CharField(max_length=24, blank=True, default="")

    freight_basis = models.CharField(max_length=16, default="not_specified", choices=_choices(records.BASIS))
    freight_amount = models.DecimalField(null=True, blank=True, **MONEY)
    duties_basis = models.CharField(max_length=16, default="not_specified", choices=_choices(records.BASIS))
    duties_amount = models.DecimalField(null=True, blank=True, **MONEY)
    vat_basis = models.CharField(max_length=16, default="not_specified", choices=_choices(records.BASIS))
    vat_amount = models.DecimalField(null=True, blank=True, **MONEY)

    # A claimed relief is not a relief: with no document attached the duty
    # line derives as Unconfirmed, not as zero (design doc section 17.1).
    duty_relief_claimed = models.BooleanField(default=False)
    duty_relief_document = models.ForeignKey(
        "supply_chain.Document", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    incoterm = models.CharField(max_length=16, blank=True, default="")
    delivery_supply_point = models.ForeignKey(
        "supply_chain.SupplyPoint", null=True, blank=True, on_delete=models.PROTECT, related_name="inbound_contracts"
    )
    promised_lead_time_days = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-signed_on", "-created_at"]
        indexes = [models.Index(fields=["program_id", "status"])]

    def __str__(self):
        return self.reference or f"contract {self.pk}"

    @property
    def duty_relief_evidenced(self) -> bool:
        return self.duty_relief_claimed and self.duty_relief_document_id is not None


class Shipment(SourcedModel):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="shipments")
    reference = models.CharField(max_length=64, blank=True, default="")
    sscc = models.CharField(max_length=18, blank=True, default="")
    status = models.CharField(max_length=16, default="planned", choices=_choices(records.SHIPMENT_STATUSES))
    dispatched_on = models.DateField(null=True, blank=True)
    expected_on = models.DateField(null=True, blank=True)
    carrier = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-dispatched_on", "-created_at"]

    @property
    def is_in_transit(self) -> bool:
        """Dispatched and not yet received. Never counted as stock (section 19.1)."""
        return self.status in records.IN_TRANSIT_STATUSES


class ShipmentLine(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    batch = models.CharField(max_length=64, blank=True, default="")
    expiry = models.DateField(null=True, blank=True)
    quantity = models.DecimalField(**QTY)
    quantity_unit = models.CharField(max_length=32)


class Receipt(SourcedModel):
    """A goods received note. The only event that brings stock into existence."""

    contract = models.ForeignKey(Contract, null=True, blank=True, on_delete=models.PROTECT, related_name="receipts")
    shipment = models.ForeignKey(Shipment, null=True, blank=True, on_delete=models.PROTECT, related_name="receipts")
    supply_point = models.ForeignKey("supply_chain.SupplyPoint", on_delete=models.PROTECT, related_name="receipts")
    reference = models.CharField(max_length=64, blank=True, default="")
    received_on = models.DateField()

    class Meta:
        ordering = ["-received_on", "-created_at"]


class ReceiptLine(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    batch = models.CharField(max_length=64, blank=True, default="")
    expiry = models.DateField(null=True, blank=True)
    quantity_accepted = models.DecimalField(**QTY)
    quantity_rejected = models.DecimalField(default=Decimal("0"), **QTY)
    rejection_reason = models.CharField(max_length=255, blank=True, default="")
    quantity_unit = models.CharField(max_length=32)


class Invoice(SourcedModel):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="invoices")
    reference = models.CharField(max_length=64, blank=True, default="")
    issued_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, default="received", choices=_choices(records.INVOICE_STATUSES))
    currency = models.CharField(max_length=3, default="USD")
    amount = models.DecimalField(null=True, blank=True, **MONEY)
    quantity_billed = models.DecimalField(null=True, blank=True, **QTY)
    quantity_unit = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        ordering = ["-issued_on", "-created_at"]


class Payment(SourcedModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    paid_on = models.DateField()
    amount = models.DecimalField(**MONEY)
    currency = models.CharField(max_length=3, default="USD")
    method = models.CharField(max_length=32, blank=True, default="")
    reference = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-paid_on"]


class Document(SourcedModel):
    """Evidence. A stored file, or a link to where it legitimately lives.

    Six explicit nullable links rather than a GenericForeignKey: the targets
    are a closed set, and explicit columns stay joinable and queryable, which
    a generic relation is not.
    """

    program_id = models.IntegerField(db_index=True)
    kind = models.CharField(max_length=32, choices=_choices(records.DOCUMENT_KINDS))
    title = models.CharField(max_length=255, blank=True, default="")
    filename = models.CharField(max_length=255, blank=True, default="")
    content_type = models.CharField(max_length=128, blank=True, default="")
    size_bytes = models.BigIntegerField(null=True, blank=True)
    # Stored through Django's configured storage: S3 on the deployment,
    # filesystem locally. Either this or external_url, never neither.
    storage_key = models.CharField(max_length=512, blank=True, default="")
    external_url = models.URLField(max_length=1024, blank=True, default="")
    sha256 = models.CharField(max_length=64, blank=True, default="")
    uploaded_at = models.DateTimeField(null=True, blank=True)

    contract = models.ForeignKey(Contract, null=True, blank=True, on_delete=models.CASCADE, related_name="documents")
    shipment = models.ForeignKey(Shipment, null=True, blank=True, on_delete=models.CASCADE, related_name="documents")
    receipt = models.ForeignKey(Receipt, null=True, blank=True, on_delete=models.CASCADE, related_name="documents")
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.CASCADE, related_name="documents")
    supply_point = models.ForeignKey(
        "supply_chain.SupplyPoint", null=True, blank=True, on_delete=models.CASCADE, related_name="documents"
    )
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.CASCADE, related_name="documents")

    class Meta:
        ordering = ["-uploaded_at", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(storage_key="") | ~Q(external_url=""),
                name="document_has_a_location",
            )
        ]

    @property
    def is_stored(self) -> bool:
        return bool(self.storage_key)


# ======================================================================
# Network tier -- where stock can rest
# ======================================================================


class SupplyPoint(SourcedModel):
    """Anywhere stock can rest, including a field worker's own holding.

    `kind="user_held"` is the ruling the whole stock model rests on (design
    doc section 18). A worker is a supply point, not a special case with its
    own arithmetic, which is what lets:

      - distributing to a worker reuse the same movement ledger as a
        store-to-store transfer;
      - a worker's stock on hand be a balance rather than a parallel concept;
      - a worker's self-reported count and a warehouse stock take be one
        record type;
      - resupply planning not care which level it is planning for.

    Points form a tree through `parent`, so a programme's network reads as a
    hierarchy without a second structure to keep in step.
    """

    program_id = models.IntegerField(db_index=True)
    # Set when the point belongs to one opportunity -- every user_held point
    # does. Programme-level stores leave it null, so one query with a filter
    # serves both "this opportunity's workers" and "the whole network".
    opportunity_id = models.IntegerField(null=True, blank=True, db_index=True)
    slug = models.SlugField(max_length=96)
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=24, choices=_choices(records.SUPPLY_POINT_KINDS))
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    managed_by_party = models.ForeignKey(
        Party, null=True, blank=True, on_delete=models.PROTECT, related_name="supply_points"
    )

    connect_username = models.CharField(max_length=150, blank=True, default="", db_index=True)
    connect_user_id = models.IntegerField(null=True, blank=True, db_index=True)

    admin_area = models.CharField(max_length=255, blank=True, default="")
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    # Policy as data: the min/max months-of-stock band this point is managed
    # to. Resupply reads it rather than hardcoding a programme's rule.
    min_months_of_stock = models.DecimalField(null=True, blank=True, max_digits=6, decimal_places=2)
    max_months_of_stock = models.DecimalField(null=True, blank=True, max_digits=6, decimal_places=2)
    status = models.CharField(max_length=16, default="active", choices=_choices(("active", "inactive")))

    class Meta:
        constraints = [models.UniqueConstraint(fields=["program_id", "slug"], name="uniq_supply_point_program_slug")]
        ordering = ["name"]
        indexes = [models.Index(fields=["program_id", "kind"])]

    def __str__(self):
        return self.name

    @property
    def is_user_held(self) -> bool:
        return self.kind == "user_held"

    def clean(self):
        if self.kind == "user_held" and not (self.connect_username or self.connect_user_id):
            raise ValidationError(
                {"connect_username": "A user_held supply point must name the Connect user whose stock it is."}
            )


# ======================================================================
# Stock tier -- the append-only ledger, and what is reported over it
# ======================================================================


class MovementQuerySet(models.QuerySet):
    """The ledger's arithmetic, in one place.

    **The sign convention.** A balance at a point is

        sum(quantity where to_supply_point = point)
      - sum(quantity where from_supply_point = point)

    and that single rule covers every movement kind, because each kind simply
    chooses which sides it sets: a receipt sets `to` only, consumption sets
    `from` only, a transfer/issue/distribution sets both, and an adjustment
    sets `to` with a quantity that may be negative. No kind needs a special
    case, so there is no table of which kinds add and which subtract to get
    wrong -- which is exactly the bug that silently doubles or zeroes a
    balance.

    **Units are not summed across.** Quantities are stored in the unit they
    were stated in. Cartons and sachets cannot be added without the pack
    specification, and the supplier may never have stated it, so these
    methods return a total *per unit* and leave reconciliation to
    `stock/services/`, which can return Unconfirmed. Collapsing units here
    would bury the one finding this domain exists to surface.
    """

    def for_program(self, program_id):
        return self.filter(program_id=program_id)

    def for_opportunity(self, opportunity_id):
        return self.filter(opportunity_id=opportunity_id)

    def as_of(self, on_date):
        return self.filter(occurred_on__lte=on_date) if on_date else self

    def between(self, start, end):
        qs = self
        if start:
            qs = qs.filter(occurred_on__gte=start)
        if end:
            qs = qs.filter(occurred_on__lte=end)
        return qs

    def touching(self, supply_point):
        """Every movement that changes this point's balance, either direction."""
        return self.filter(Q(to_supply_point=supply_point) | Q(from_supply_point=supply_point))

    def _totals(self, group_by):
        """{(group values...): Decimal} -- one grouped SUM, executed in the database."""
        rows = self.values(*group_by).annotate(total=Sum("quantity"))
        return {tuple(row[key] for key in group_by): (row["total"] or Decimal("0")) for row in rows}

    def balance_by_unit(self, supply_point, item=None):
        """{quantity_unit: Decimal} held at this point, aggregated in the database."""
        qs = self.filter(item=item) if item is not None else self
        inbound = qs.filter(to_supply_point=supply_point)._totals(["quantity_unit"])
        outbound = qs.filter(from_supply_point=supply_point)._totals(["quantity_unit"])
        units = set(inbound) | set(outbound)
        return {unit[0]: inbound.get(unit, Decimal("0")) - outbound.get(unit, Decimal("0")) for unit in units}

    def balance_by_batch(self, supply_point, item=None):
        """{(batch, quantity_unit): Decimal} -- what first-expired-first-out needs."""
        qs = self.filter(item=item) if item is not None else self
        keys = ["batch", "quantity_unit"]
        inbound = qs.filter(to_supply_point=supply_point)._totals(keys)
        outbound = qs.filter(from_supply_point=supply_point)._totals(keys)
        return {
            key: inbound.get(key, Decimal("0")) - outbound.get(key, Decimal("0"))
            for key in set(inbound) | set(outbound)
        }

    def consumption_by_unit(self):
        """{quantity_unit: Decimal} dispensed. The input to average monthly consumption."""
        return {unit[0]: total for unit, total in self.filter(kind="consumption")._totals(["quantity_unit"]).items()}


class Movement(SourcedModel):
    """One line of the stock ledger. Append-only: never updated, never deleted.

    A correction is an `adjustment` movement naming its cause, not an edit.
    That is what makes a balance reproducible: replaying the ledger at any
    date gives the figure the system showed on that date.
    """

    program_id = models.IntegerField(db_index=True)
    opportunity_id = models.IntegerField(null=True, blank=True, db_index=True)
    kind = models.CharField(max_length=16, choices=_choices(records.MOVEMENT_KINDS))
    occurred_on = models.DateField(db_index=True)

    from_supply_point = models.ForeignKey(
        SupplyPoint, null=True, blank=True, on_delete=models.PROTECT, related_name="movements_out"
    )
    to_supply_point = models.ForeignKey(
        SupplyPoint, null=True, blank=True, on_delete=models.PROTECT, related_name="movements_in"
    )

    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
    commodity = models.ForeignKey(Commodity, on_delete=models.PROTECT, related_name="movements")
    batch = models.CharField(max_length=64, blank=True, default="")
    expiry = models.DateField(null=True, blank=True)
    quantity = models.DecimalField(**QTY)
    quantity_unit = models.CharField(max_length=32)
    reference = models.CharField(max_length=64, blank=True, default="")

    receipt = models.ForeignKey(Receipt, null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
    shipment = models.ForeignKey(Shipment, null=True, blank=True, on_delete=models.PROTECT, related_name="movements")
    distribution = models.ForeignKey(
        "supply_chain.Distribution", null=True, blank=True, on_delete=models.PROTECT, related_name="movements"
    )
    stock_count = models.ForeignKey(
        "supply_chain.StockCount", null=True, blank=True, on_delete=models.PROTECT, related_name="movements"
    )

    objects = MovementQuerySet.as_manager()

    class Meta:
        ordering = ["-occurred_on", "-id"]
        indexes = [
            models.Index(fields=["program_id", "item", "occurred_on"]),
            models.Index(fields=["to_supply_point", "item"]),
            models.Index(fields=["from_supply_point", "item"]),
            models.Index(fields=["opportunity_id", "kind"]),
        ]
        constraints = [
            # A movement that touches no point changes nothing and would sit
            # in the ledger as an untraceable quantity.
            models.CheckConstraint(
                condition=Q(from_supply_point__isnull=False) | Q(to_supply_point__isnull=False),
                name="movement_touches_a_point",
            ),
            # Only an adjustment may be negative: it is how a stock count's
            # variance gets into the ledger. A negative receipt or
            # consumption is a sign error, and silently accepting one
            # corrupts every balance downstream of it.
            models.CheckConstraint(
                condition=Q(quantity__gt=0) | Q(kind="adjustment"),
                name="movement_positive_unless_adjustment",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise ValueError(
                "Movements are append-only: record an adjustment movement naming this one "
                "as its cause rather than editing it."
            )
        return super().save(*args, **kwargs)


class StockCount(SourcedModel):
    """What somebody says is actually there.

    Kept beside the ledger balance rather than replacing it: the variance
    between the two is the finding, and overwriting one with the other
    destroys it. An `override` additionally writes a compensating
    `adjustment` movement, so the ledger continues to agree with the working
    figure while remaining the authority (design doc section 20).
    """

    program_id = models.IntegerField(db_index=True)
    opportunity_id = models.IntegerField(null=True, blank=True, db_index=True)
    supply_point = models.ForeignKey(SupplyPoint, on_delete=models.PROTECT, related_name="stock_counts")
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="stock_counts")
    commodity = models.ForeignKey(Commodity, on_delete=models.PROTECT, related_name="stock_counts")
    batch = models.CharField(max_length=64, blank=True, default="")
    kind = models.CharField(max_length=16, choices=_choices(records.STOCK_COUNT_KINDS))
    counted_on = models.DateField(db_index=True)
    quantity = models.DecimalField(**QTY)
    quantity_unit = models.CharField(max_length=32)
    reason = models.TextField(blank=True, default="")

    # Where a self-reported count came from. A worker's periodic CommCare
    # submission arrives through Connect, so the count is traceable back to
    # the form that produced it rather than being an unattributable number.
    form_submission_id = models.CharField(max_length=64, blank=True, default="")
    visit_id = models.CharField(max_length=64, blank=True, default="")
    connect_username = models.CharField(max_length=150, blank=True, default="", db_index=True)

    adjustment_movement = models.OneToOneField(
        Movement, null=True, blank=True, on_delete=models.PROTECT, related_name="caused_by_count"
    )

    class Meta:
        ordering = ["-counted_on", "-id"]
        indexes = [models.Index(fields=["supply_point", "item", "counted_on"])]

    def clean(self):
        if self.kind == "override" and not (self.reason or "").strip():
            raise ValidationError(
                {"reason": "An override asserts a figure over both the ledger and the last count; say why."}
            )


class Distribution(SourcedModel):
    """One resupply run from a store out to field workers.

    A batch header over many movements: a run covers every worker served that
    day and emits one movement per line, so the ledger has no special case
    for distribution and a worker's balance needs no separate arithmetic.
    """

    program_id = models.IntegerField(db_index=True)
    opportunity_id = models.IntegerField(db_index=True)
    supply_point = models.ForeignKey(SupplyPoint, on_delete=models.PROTECT, related_name="distributions_out")
    distributed_on = models.DateField(db_index=True)
    reference = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-distributed_on", "-id"]


class DistributionLine(models.Model):
    distribution = models.ForeignKey(Distribution, on_delete=models.CASCADE, related_name="lines")
    to_supply_point = models.ForeignKey(SupplyPoint, on_delete=models.PROTECT, related_name="distribution_lines")
    connect_username = models.CharField(max_length=150, blank=True, default="")
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    batch = models.CharField(max_length=64, blank=True, default="")
    quantity = models.DecimalField(**QTY)
    quantity_unit = models.CharField(max_length=32)
    movement = models.OneToOneField(
        Movement, null=True, blank=True, on_delete=models.PROTECT, related_name="distribution_line"
    )
