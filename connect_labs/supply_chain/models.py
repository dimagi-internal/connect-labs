"""First-class Django models for the supply domain.

**Why these are real tables and not LabsRecords.** The procurement tier was
built on `LabsRecord`s written through to production Connect, because that is
how labs apps normally persist: the data belongs to Connect and labs is a
client of it. Supply is different on both counts.

  1. It is *primary* data that originates here -- a stock ledger, a contract,
     a receipt, a worker's reported count. Nothing in Connect is its source.
  2. It carries no PII. The reason labs tender-trips data through Connect is so
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

from django.conf import settings
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


def scope_key(program_id=None) -> str:
    """The reference tier's scope, as one indexable string.

    Reference data -- commodities, trade items, suppliers -- is shared across
    a programme's tenders. One tier, not two.

    **There used to be an organisation tier above this one**, and it was worse
    than not having it. Whether you got `org:<id>` or `prog:<id>` depended on
    whether the caller happened to have an organisation selected, so ONE
    programme had two catalogues and which one you saw was a property of your
    session rather than of the data. Selecting an organisation alongside a
    programme silently emptied the Catalogue and Suppliers tabs while Sourcing
    carried on working, and the MCP tool description told agents to pass the
    organisation id precisely when it would do that. It never worked for a
    labs-only organisation either: those carry a slug, `int()` on it returned
    None, and the scope quietly fell back.

    Sharing a catalogue across an organisation's programmes is a real feature,
    but it needs to know which organisation owns a programme -- a fact this
    database does not hold -- so half of it was worse than none. Measured
    before removing it: across the whole labs database every reference row was
    `prog:`-scoped. The organisation tier had never once been used.

    A single non-null string rather than nullable columns is still deliberate:
    Postgres treats NULLs as distinct, so `unique_together` over nullable
    scope columns does not actually prevent duplicate slugs.
    """
    if program_id not in (None, ""):
        return f"prog:{program_id}"
    raise ValueError("reference data needs a program_id to be scoped by")


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
    recorded_by_org = models.ForeignKey(
        "labs.LabsOrg", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
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
# Reference tier -- reused across a programme's tenders
# ======================================================================


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

    # A kit: one SKU that bundles several products -- an ORS/zinc co-pack, a
    # three-day treatment packet, a test kit with its reagents. A list of
    # {commodity_slug, quantity, base_unit, spec_attributes?}. The item still
    # belongs to ONE primary commodity and is counted in its own SKUs; the
    # ledger never breaks a kit apart. What the list is for is the three
    # questions a single commodity cannot answer: does the zinc inside meet
    # the zinc specification, do two suppliers' "co-packs" hold the same
    # contents, and is one of these a whole course.
    components = models.JSONField(default=list, blank=True)
    # Whether one of this item is a full treatment course, and at which level:
    # "" (not a course, or not known), "base_unit" or "pack". A packet that IS
    # a three-day course needs no ration table to cost per course -- the
    # manufacturer packed the course. Stated per item rather than inferred,
    # because nothing in a component list says it adds up to a protocol.
    one_course_is = models.CharField(max_length=16, blank=True, default="", choices=_choices(records.ONE_COURSE_IS))
    # consumable (the default) or durable. A dispenser is not consumed, so a
    # consumption rate, months of stock and a resupply quantity for one are
    # meaningless; it still moves through the ledger, because "which site has
    # which dispenser" is a balance like any other.
    stock_class = models.CharField(max_length=16, default="consumable", choices=_choices(records.STOCK_CLASSES))
    # Which unit the `components` describe: one base unit ("base", a co-pack)
    # or one pack ("pack", a test kit of 50 tests). See records.COMPONENTS_PER.
    components_per = models.CharField(max_length=8, default="base", choices=_choices(records.COMPONENTS_PER))

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scope_key", "sku"], name="uniq_item_scope_sku")]
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.sku})"

    @property
    def commodity_slug(self):
        return self.commodity.slug

    @property
    def is_kit(self) -> bool:
        return bool(self.components)

    @property
    def components_unit(self) -> str:
        """The unit one set of `components` fills: "kit", "co-pack"."""
        commodity = self.commodity if self.commodity_id else None
        if self.components_per == "pack":
            return self.pack_unit or (commodity.pack_unit if commodity else "") or "pack"
        return self.base_unit or (commodity.base_unit if commodity else "") or "unit"

    def components_per_base_unit(self) -> list[tuple[str, str, "Decimal"]]:
        """The components restated as what ONE BASE UNIT holds, for comparing kits.

        (slug, unit, quantity), sorted. A kit described per pack is divided by
        its pack size; one with no pack size cannot be restated and keeps its
        own figures, which then simply fail to match a kit that could be.
        """
        from decimal import Decimal

        divisor = Decimal(1)
        if self.components_per == "pack" and self.base_per_pack:
            divisor = Decimal(int(self.base_per_pack))
        return sorted(
            (
                c.get("commodity_slug") or "",
                c.get("base_unit") or "",
                (Decimal(str(c.get("quantity"))) / divisor).normalize(),
            )
            for c in self.components or []
        )

    @property
    def is_durable(self) -> bool:
        return self.stock_class == "durable"


# Company facts a program may supply when it links a supplier. Everything
# here lives on the organisation, not the program's link to it.
SUPPLIER_COMPANY_FIELDS = ("name", "country", "type", "city", "contacts", "qualifications", "website", "description")
_PROFILE_FIELDS = ("type", "city", "contacts", "qualifications", "website", "description")


class SupplierProfile(TimestampedModel):
    """What a company is, as a supplier -- once, for every program.

    `LabsOrg` holds identity only (its docstring), so a supplier's own facts
    live here, on a profile that points at it: the same shape as the
    marketplace's `OrgProfile`. The name and country are the organisation's,
    not repeated here.

    These used to be columns on `Supplier`, one copy per program, because
    #1814 scoped all reference data to the program to fix a broken
    organisation tier in catalogues and suppliers were swept along with it.
    A company is the same company in every program it sells to; two
    programs buying from EHA had two EHAs with two contact lists that
    drifted apart.
    """

    org = models.OneToOneField("labs.LabsOrg", on_delete=models.CASCADE, related_name="supplier_profile")
    # `type` rather than `kind` because the sourcing services already read
    # supplier.type; renaming it would touch working code for no gain.
    type = models.CharField(max_length=32, blank=True, default="")
    city = models.CharField(max_length=128, blank=True, default="")
    contacts = models.JSONField(default=list, blank=True)
    qualifications = models.JSONField(default=list, blank=True)
    website = models.URLField(max_length=500, blank=True, default="")
    description = models.TextField(blank=True, default="")

    def __str__(self):
        return f"supplier profile of {self.org}"


class SupplierOffering(TimestampedModel):
    """Something a supplier says it sells -- its own claim, dated.

    The supply base deliberately has no "supplies" table: "X supplies RUTF"
    typed into a box is an assertion nobody made. This one somebody did make
    -- the supplier, about itself -- so it is kept as exactly that, and read
    as the weakest kind of evidence (`supply_base`'s `declared`). Matched to a
    program's product by category, or exactly by UNICEF material number or
    GTIN when the supplier gives one.
    """

    profile = models.ForeignKey(SupplierProfile, on_delete=models.CASCADE, related_name="offerings")
    category = models.CharField(max_length=32, choices=[(c, label) for c, label in records.COMMODITY_CATEGORIES])
    product_name = models.CharField(max_length=255)
    unicef_material_number = models.CharField(max_length=32, blank=True, default="")
    gtin = models.CharField(max_length=14, blank=True, default="")
    pack_description = models.CharField(max_length=255, blank=True, default="")
    base_per_pack = models.IntegerField(null=True, blank=True)
    typical_lead_time_days = models.IntegerField(null=True, blank=True)
    minimum_order = models.CharField(max_length=128, blank=True, default="")
    countries_served = models.JSONField(default=list, blank=True)
    certifications = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["category", "product_name"]

    def __str__(self):
        return self.product_name


class SupplierManager(models.Manager):
    def enrol(self, scope_key, *, org=None, **company):
        """Link a company into a program as a supplier, and return the link.

        The one way a supplier comes to exist. The company is `org` when
        given, else the organisation `find_or_mint_supplier_org` resolves
        from the company's name and Connect id. Its profile gains only the
        facts it did not already have -- linking a company into a second
        program must not overwrite what the first one knew about it. A
        company already linked into this program returns that link: it is
        the same supplier, not a second one.
        """
        from connect_labs.supply_chain.identity import find_or_mint_supplier_org

        status = company.pop("status", None)
        notes = company.pop("notes", None)
        connect_id = company.pop("connect_organization_id", None)
        if org is None:
            org = find_or_mint_supplier_org(
                company.get("name") or "", country=company.get("country") or "", connect_organization_id=connect_id
            )
        fill_profile(org, company)
        link, created = self.get_or_create(scope_key=scope_key, org=org)
        changed = []
        if status and (created or not link.status or link.status == "identified"):
            link.status = status
            changed.append("status")
        if notes and not link.notes:
            link.notes = notes
            changed.append("notes")
        if changed:
            link.save(update_fields=[*changed, "updated_at"])
        return link


def fill_profile(org, company: dict, *, overwrite=False):
    """Write company facts to `org`'s supplier profile, creating it if needed.

    Blank fields only, unless `overwrite` -- which is an edit, where the
    person means to replace what is there. The organisation's own country is
    filled the same way; its name is never touched here.
    """
    profile, _ = SupplierProfile.objects.get_or_create(org=org)
    changed = []
    for field in _PROFILE_FIELDS:
        value = company.get(field)
        if value is None:
            continue
        current = getattr(profile, field)
        if overwrite:
            if value != current:
                setattr(profile, field, value)
                changed.append(field)
        elif value not in ("", []) and current in ("", [], None):
            setattr(profile, field, value)
            changed.append(field)
    if changed:
        profile.save(update_fields=[*changed, "updated_at"])
    country = company.get("country")
    if country and not org.country:
        org.country = country
        org.save(update_fields=["country", "updated_at"])
    return profile


class Supplier(TimestampedModel):
    """A company, as a supplier to ONE program: the program's side of it.

    What is genuinely per-program is small -- where this program has got
    to with the company (`status`) and its own notes. Everything else is the
    company's, held once on the organisation (`org`, `org.supplier_profile`)
    and read through here, so `supplier.name` and the serialised record read
    exactly as they did when these were columns.

    The table keeps its ids, so quotes, outreach, awards, contracts and
    documents still point at the program's supplier: a quote belongs to a
    program, and `supplier_id` means what it always meant.
    """

    scope_key = models.CharField(max_length=64, db_index=True)
    org = models.ForeignKey("labs.LabsOrg", on_delete=models.PROTECT, related_name="supplier_links")
    status = models.CharField(max_length=32, blank=True, default="identified")
    notes = models.TextField(blank=True, default="")
    # A supplier that arrived by bidding from the marketplace, rather than
    # being added by the program team, is shown as "not yet reviewed" until
    # somebody on the team says they have looked. Its bids count meanwhile:
    # a hidden bid is a silent failure, and a bid the team will not consider
    # is voided with a reason like any other.
    origin = models.CharField(
        max_length=16, default="program", db_default="program", choices=_choices(records.SUPPLIER_ORIGINS)
    )
    reviewed_on = models.DateField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    objects = SupplierManager()

    class Meta:
        ordering = ["org__name"]
        constraints = [
            models.UniqueConstraint(fields=["scope_key", "org"], name="supply_supplier_one_link_per_program")
        ]

    def __str__(self):
        # Said in every picker: a donor supplies in kind, so choosing one for a
        # priced order is the mistake worth making visible at the choice.
        return f"{self.name} (donor)" if self.type == "donor" else self.name

    @property
    def profile(self):
        org = self.org
        if org.pk is None and "supplier_profile" not in org._state.fields_cache:
            # An unsaved organisation (a test's in-memory supplier) has no
            # profile to look up, and asking the database would raise.
            return None
        try:
            return org.supplier_profile
        except SupplierProfile.DoesNotExist:
            return None

    @property
    def awaiting_review(self) -> bool:
        return self.origin == "self_registered" and self.reviewed_on is None

    def _profile_value(self, field, empty):
        profile = self.profile
        return getattr(profile, field) if profile is not None else empty

    @property
    def name(self):
        return self.org.name

    @property
    def country(self):
        return self.org.country

    @property
    def connect_organization_id(self):
        return self.org.connect_organization_id

    @property
    def type(self):
        return self._profile_value("type", "")

    @property
    def city(self):
        return self._profile_value("city", "")

    @property
    def contacts(self):
        return self._profile_value("contacts", [])

    @property
    def qualifications(self):
        return self._profile_value("qualifications", [])


# ======================================================================
# Procurement tier -- source to award. Programme-scoped.
# ======================================================================


class Tender(TimestampedModel):
    program_id = models.IntegerField(db_index=True)
    label = models.CharField(max_length=255)
    status = models.CharField(max_length=16, default="draft", choices=_choices(("draft", "open", "closed", "awarded")))
    lines = models.JSONField(default=list, blank=True)
    # Where the buyer will take delivery: one or more places, each
    # {key, name, city, country, country_name}. A supplier's bid says which of
    # them its price covers. One total quantity per product, not a split per
    # place -- the buyer names the places it can use, the supplier the ones
    # it can serve.
    delivery_points = models.JSONField(default=list, blank=True)
    # The buyer will also collect from the supplier. A collected bid's
    # delivered cost includes the buyer's own transport, entered on the quote.
    pickup_accepted = models.BooleanField(default=False, db_default=False)
    incoterm_requested = models.CharField(max_length=16, blank=True, default="", db_default="")
    response_deadline = models.DateField(null=True, blank=True)
    reminder_interval_days = models.IntegerField(null=True, blank=True)
    shelf_life_months_minimum = models.IntegerField(null=True, blank=True)
    notes_to_supplier = models.TextField(blank=True, default="")
    # On the supplier marketplace an open tender is public unless the program
    # says otherwise; a private one is seen only by the organisations invited.
    visibility = models.CharField(
        max_length=16, default="public", db_default="public", choices=_choices(records.TENDER_VISIBILITIES)
    )
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.label

    @property
    def delivery_point(self):
        """The first place, for readers written when a tender had exactly one."""
        return (self.delivery_points or [{}])[0]

    @delivery_point.setter
    def delivery_point(self, point):
        """One place, as callers written before a tender could have several set it."""
        point = dict(point or {})
        incoterm = point.pop("incoterm_requested", None)
        if incoterm:
            self.incoterm_requested = incoterm
        point = {k: v for k, v in point.items() if v}
        self.delivery_points = [{"key": "main", **point}] if point else []

    def point(self, key):
        return next((p for p in self.delivery_points or [] if p.get("key") == key), None)

    def quantity_for(self, commodity_slug):
        """(quantity, unit) for a commodity on this tender, or None.

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
    per (tender, supplier), because re-inviting is a real event worth keeping."""

    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="outreach")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="outreach")
    channel = models.CharField(max_length=16, blank=True, default="manual")
    sent_on = models.DateField(null=True, blank=True)
    responded = models.BooleanField(default=False)
    response_kind = models.CharField(max_length=16, blank=True, default="")
    last_reminder_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-sent_on", "-created_at"]
        indexes = [models.Index(fields=["tender", "supplier"])]
        verbose_name_plural = "outreach"


class Quote(TimestampedModel):
    """What a supplier said, recorded on the supplier's own terms.

    Nothing here is normalised. `as_quoted_*` is verbatim and the `*_basis`
    and `pack_spec_source` flags record what was and was not stated, so the
    derivations can refuse to compute rather than assume. Corrections
    supersede rather than overwrite: a quote is a statement someone made, and
    editing it away loses the fact that it was made.
    """

    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="quotes")
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
    # `db_default` as well as `default` on each new NOT NULL column here: a
    # model default is applied in Python, so a checkout or an old task still
    # running mid-deploy would insert without the column and fail.
    # Who typed it in. The program team transcribing an email and the
    # supplier entering its own offer on the marketplace are different
    # evidence, so the quote says which it is.
    entered_by = models.CharField(
        max_length=16, default="program", db_default="program", choices=_choices(records.QUOTE_ENTERED_BY)
    )
    # How the goods reach the buyer on THIS bid. A supplier may bid once per
    # option, because the price differs by where the goods go.
    delivery_mode = models.CharField(
        max_length=16, default="delivered", db_default="delivered", choices=_choices(records.DELIVERY_MODES)
    )
    # For a delivered bid: which of the tender's places the price covers.
    delivery_point_keys = models.JSONField(default=list, blank=True)
    # For a collected bid: where the buyer collects from.
    pickup_location = models.CharField(max_length=255, blank=True, default="", db_default="")
    # For a collected bid: what the buyer's own transport will cost, entered
    # by the program team. Until it is, the delivered cost is unconfirmed --
    # a collected bid never looks cheaper merely because freight is missing.
    buyer_transport_amount = models.DecimalField(null=True, blank=True, **MONEY)
    entered_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tender", "commodity"])]

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

    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="awards")
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


class AwardApproval(TimestampedModel):
    """A third party's agreement to an award, asked for and then given or refused.

    The approver is not the person deciding the award -- `Award.decided_by`
    is -- but somebody whose agreement the award needs before money moves: a
    technical partner confirming the product, a funder approving its use, a
    regulator. A contract may not rest on an award with one pending or
    declined, which is a statement of fact, not a recommendation.

    A decision is final on its row. A funder who declines and later relents
    is a second request; overwriting the first would lose that it was ever
    declined.
    """

    award = models.ForeignKey(Award, on_delete=models.CASCADE, related_name="approvals")
    approver_org = models.ForeignKey("labs.LabsOrg", on_delete=models.PROTECT, related_name="supply_approvals")
    role = models.CharField(max_length=16, choices=_choices(records.APPROVAL_ROLES))
    status = models.CharField(max_length=16, default="requested", choices=_choices(records.APPROVAL_STATUSES))
    requested_on = models.DateField()
    decided_on = models.DateField(null=True, blank=True)
    # Whose word the answer is. Blank or `we_recorded` when the programme
    # recorded it; `partner_reported` with the approver as recorder when the
    # approver answered through its own update link -- the difference between
    # "they told us" and "we say they told us" is the gate's whole value.
    decision_source = models.CharField(max_length=32, blank=True, default="")
    decision_recorded_by_org = models.ForeignKey(
        "labs.LabsOrg", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # What was asked, and what they answered, are two facts. The answer used
    # to be written over the request's note, which lost the question.
    note = models.TextField(blank=True, default="")
    decision_note = models.TextField(blank=True, default="")
    # What the approval rests on, when that is a document already on file: a
    # regulator's approval granted against a product registration. Distinct
    # from the documents attached TO the approval (Document.approval), which
    # are the approver's own letter or email. Optional, and a pointer rather
    # than a copy, so the approval and the registration cannot disagree.
    rests_on_document = models.ForeignKey(
        "Document", null=True, blank=True, on_delete=models.SET_NULL, related_name="approvals_resting_on"
    )

    class Meta:
        ordering = ["requested_on", "id"]

    @property
    def is_pending(self) -> bool:
        return self.status == "requested"


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
    tender = models.ForeignKey(Tender, null=True, blank=True, on_delete=models.PROTECT, related_name="contracts")
    award = models.ForeignKey(Award, null=True, blank=True, on_delete=models.PROTECT, related_name="contracts")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="contracts")
    commodity = models.ForeignKey(Commodity, on_delete=models.PROTECT, related_name="contracts")
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="contracts")

    buyer_of_record = models.CharField(max_length=16, choices=_choices(records.BUYER_OF_RECORD))
    buyer_org = models.ForeignKey(
        "labs.LabsOrg", null=True, blank=True, on_delete=models.PROTECT, related_name="supply_contracts"
    )

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

    # Whether the goods are bought at all. Not every contract is a purchase:
    # a donor's in-kind chlorine and a partner's MUAC strips paid out of its
    # setup fee both have a supplier, a quantity, promised dates, shipments
    # and receipts -- the whole physical chain -- and no price that will ever
    # exist. Treating them as priced reported that absence as a gap forever.
    consideration = models.CharField(max_length=16, default="priced", choices=_choices(records.CONSIDERATIONS))
    # Paid on delivery (the default) or in advance. Under advance terms the
    # three-way match reads a payment ahead of the goods as the agreement it
    # is, and what is paid for but refused or never delivered as money owed
    # back -- rather than "billed beyond what arrived" and "safe to pay 0".
    payment_terms = models.CharField(max_length=16, default="on_delivery", choices=_choices(records.PAYMENT_TERMS))
    # The order this one buys the shortfall of: the main supplier delivered
    # 450 of 700, and a partner bought the other 250 locally. The short order
    # then reads as covered, by name, rather than as open for ever.
    covers_shortfall_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="shortfall_covered_by"
    )

    class Meta:
        ordering = ["-signed_on", "-created_at"]
        indexes = [models.Index(fields=["program_id", "status"])]

    def __str__(self):
        return self.reference or f"contract {self.pk}"

    @property
    def duty_relief_evidenced(self) -> bool:
        return self.duty_relief_claimed and self.duty_relief_document_id is not None

    @property
    def is_priced(self) -> bool:
        return self.consideration == "priced"


class Shipment(SourcedModel):
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="shipments")
    reference = models.CharField(max_length=64, blank=True, default="")
    sscc = models.CharField(max_length=18, blank=True, default="")
    status = models.CharField(max_length=16, default="planned", choices=_choices(records.SHIPMENT_STATUSES))
    dispatched_on = models.DateField(null=True, blank=True)
    expected_on = models.DateField(null=True, blank=True)
    carrier = models.CharField(max_length=255, blank=True, default="")
    # What this consignment needs to clear, and who owes each: a list of
    # {kind, owed_by_org_id}. "Follow up with the donor when needed", as data.
    # A requirement is met by a document of that kind attached to THIS
    # shipment -- two consignments under one contract each need their own
    # airway bill.
    required_documents = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-dispatched_on", "-created_at"]

    @property
    def is_in_transit(self) -> bool:
        """Dispatched and not yet received. Never counted as stock (section 19.1)."""
        return self.status in records.IN_TRANSIT_STATUSES


class Charge(SourcedModel):
    """Money paid to land a consignment, to somebody who is not the supplier.

    Customs fees, a clearing agent, the lorry from the port: paid by us to a
    courier or to customs, never to the supplier, so it is neither the
    contract's price nor its freight line. It hangs off the shipment it was
    paid to clear, and the contract's landed cost adds it, itemised.
    """

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="charges")
    kind = models.CharField(max_length=24, choices=_choices(records.CHARGE_KINDS))
    payee_org = models.ForeignKey("labs.LabsOrg", on_delete=models.PROTECT, related_name="supply_charges")
    amount = models.DecimalField(**MONEY)
    currency = models.CharField(max_length=3, default="USD")
    # Local fees are paid in local currency. Without a rate they cannot be
    # added to a USD landed total, and the total says so rather than adding
    # naira to dollars.
    fx_rate_to_usd = models.DecimalField(null=True, blank=True, max_digits=18, decimal_places=8)
    # Null while assessed but not yet paid.
    paid_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["paid_on", "id"]


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
    # When the payee said the money arrived. Separate from `paid_on` because
    # the two are different facts from different people, and "we sent it"
    # is exactly the claim a supplier chasing payment disputes.
    confirmed_by_payee_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-paid_on"]


class Document(SourcedModel):
    """Evidence. A stored file, or a link to where it legitimately lives.

    Explicit nullable links rather than a GenericForeignKey, one per thing a
    document can be evidence FOR (`records.DOCUMENT_LINKS`). A document is not
    decoration here -- two derivations turn on one existing, a claimed duty
    relief and a batch's conformity -- so a row pointing at a deleted contract
    would silently un-evidence a figure. A generic relation gives up the
    database's help with precisely that, and gives up joining.

    At most one link is set. Two would be one row claiming to evidence two
    things, which is two documents that can later disagree; none is a
    programme-level document, which is legitimate. The write path enforces
    it -- see `attach_document`.
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
    quote = models.ForeignKey(
        "supply_chain.Quote", null=True, blank=True, on_delete=models.CASCADE, related_name="documents"
    )
    tender = models.ForeignKey(
        "supply_chain.Tender", null=True, blank=True, on_delete=models.CASCADE, related_name="documents"
    )
    award = models.ForeignKey(
        "supply_chain.Award", null=True, blank=True, on_delete=models.CASCADE, related_name="documents"
    )
    payment = models.ForeignKey(
        "supply_chain.Payment", null=True, blank=True, on_delete=models.CASCADE, related_name="documents"
    )
    distribution = models.ForeignKey(
        "supply_chain.Distribution", null=True, blank=True, on_delete=models.CASCADE, related_name="documents"
    )
    stock_count = models.ForeignKey(
        "supply_chain.StockCount", null=True, blank=True, on_delete=models.CASCADE, related_name="documents"
    )
    item = models.ForeignKey(
        "supply_chain.Item", null=True, blank=True, on_delete=models.CASCADE, related_name="documents"
    )
    charge = models.ForeignKey(
        "supply_chain.Charge", null=True, blank=True, on_delete=models.CASCADE, related_name="documents"
    )
    approval = models.ForeignKey(
        "supply_chain.AwardApproval", null=True, blank=True, on_delete=models.CASCADE, related_name="documents"
    )

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
    managed_by_org = models.ForeignKey(
        "labs.LabsOrg", null=True, blank=True, on_delete=models.PROTECT, related_name="supply_points"
    )

    connect_username = models.CharField(max_length=150, blank=True, default="", db_index=True)
    connect_user_id = models.IntegerField(null=True, blank=True, db_index=True)

    admin_area = models.CharField(max_length=255, blank=True, default="")
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    # How the point got its coordinates. `recorded` is a real one somebody
    # entered; anything else is a stand-in (the managing organisation's head
    # office, the parent point, or the country's centre) that is refreshed on
    # every write and never mistaken for a survey. See stock/services/placement.
    # db_default as well as default: an older checkout (or an old task during
    # a rolling deploy) inserts without naming these columns, and a Python-only
    # default would fail its NOT NULL.
    location_source = models.CharField(
        max_length=16,
        blank=True,
        default="",
        db_default="",
        choices=_choices(("recorded", "org_hq", "parent", "country")),
    )
    # For a stand-in, how fine it is: city | region | country (the directory's
    # own precision for the head office it came from).
    location_precision = models.CharField(max_length=8, blank=True, default="", db_default="")
    location_label = models.CharField(max_length=255, blank=True, default="", db_default="")

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


# ======================================================================
# Alerts and supplier update links -- in their own modules, registered here
# ======================================================================
#
# Declared in `alerts/models.py`, `update_links/models.py` and
# `portfolio/models.py` next to the code that uses them, and imported at the
# bottom of this module so Django finds them when it loads the app. At the
# bottom because all three import from here.
from connect_labs.supply_chain.alerts.models import AlertCheckState, AlertNotice, AlertSubscription  # noqa: E402,F401

# `Portfolio` is the ONE model in this app with no programme scope, and it says
# why in its own docstring. Read it before adding a `program_id` to it.
from connect_labs.supply_chain.portfolio.models import Portfolio  # noqa: E402,F401
from connect_labs.supply_chain.update_links.models import UpdateLink, UpdateLinkSubmission  # noqa: E402,F401
