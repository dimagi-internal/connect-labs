"""Procurement-side domain models for the OES supply satellite app.

All tables are prefixed ``supply_`` (satellite-site convention: the app's
data layer must be separable from the host by table name alone). Execution-
side models (SupplyNode, Contract, Shipment, SupplyEvent, ...) arrive in
Phase 2.
"""
from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.db import models


class Category(models.TextChoices):
    RUTF = "rutf", "RUTF"
    THERAPEUTIC_MILK = "therapeutic_milk", "Therapeutic milk"
    TRANSPORT = "transport", "Road transport"
    WAREHOUSING = "warehousing", "Warehousing"


class SupplierOrg(models.Model):
    legal_name = models.CharField(max_length=255, unique=True)
    registration_number = models.CharField(max_length=64, blank=True)
    country = models.CharField(max_length=2)  # ISO-3166 alpha-2
    hq_city = models.CharField(max_length=128, blank=True)
    description = models.TextField(blank=True)
    contact_name = models.CharField(max_length=128, blank=True)
    contact_email = models.EmailField(blank=True)
    gln = models.CharField(max_length=13, blank=True)
    gs1_company_prefix = models.CharField(max_length=12, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_supplier_org"

    def __str__(self):
        return self.legal_name


class SupplierMember(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="supply_membership")
    org = models.ForeignKey(SupplierOrg, on_delete=models.CASCADE, related_name="members")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_supplier_member"


class Certification(models.Model):
    org = models.ForeignKey(SupplierOrg, on_delete=models.CASCADE, related_name="certifications")
    cert_type = models.CharField(max_length=64)  # e.g. "ISO 22000", "GMP", "UNICEF RUTF approval"
    issuer = models.CharField(max_length=128, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    document_name = models.CharField(max_length=255, blank=True)  # demo stub, no uploads

    class Meta:
        db_table = "supply_certification"


class EOIRound(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        OPEN = "open"
        CLOSED = "closed"

    title = models.CharField(max_length=255)
    brief = models.TextField(blank=True)
    categories = models.JSONField(default=list)  # list[Category]
    opens_at = models.DateField(null=True, blank=True)
    closes_at = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_eoi_round"


class EOISubmission(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        SUBMITTED = "submitted"
        QUALIFIED = "qualified"
        REJECTED = "rejected"

    org = models.ForeignKey(SupplierOrg, on_delete=models.CASCADE, related_name="eoi_submissions")
    round = models.ForeignKey(EOIRound, on_delete=models.CASCADE, related_name="submissions")
    categories = models.JSONField(default=list)
    # {category: {"capacity": str, "regions": [iso2...], "lead_time_days": int, "notes": str}}
    commitments = models.JSONField(default=dict)
    profile_snapshot = models.JSONField(null=True, blank=True)  # frozen at submit
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_eoi_submission"
        constraints = [models.UniqueConstraint(fields=["org", "round"], name="uniq_submission_per_org_round")]


class EOIReview(models.Model):
    submission = models.ForeignKey(EOISubmission, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    decisions = models.JSONField(default=dict)  # {category: "qualify"|"reject"}
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_eoi_review"


class Qualification(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        EXPIRED = "expired"
        REVOKED = "revoked"

    org = models.ForeignKey(SupplierOrg, on_delete=models.CASCADE, related_name="qualifications")
    category = models.CharField(max_length=32, choices=Category.choices)
    source_submission = models.ForeignKey(EOISubmission, null=True, on_delete=models.SET_NULL, related_name="+")
    granted_at = models.DateField()
    expires_at = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "supply_qualification"


class RFP(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        PUBLISHED = "published"
        CLOSED = "closed"
        AWARDED = "awarded"

    title = models.CharField(max_length=255)
    brief = models.TextField(blank=True)
    categories = models.JSONField(default=list)
    countries = models.JSONField(default=list)  # ISO-3166 alpha-2
    bid_deadline = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_rfp"


class Lot(models.Model):
    rfp = models.ForeignKey(RFP, on_delete=models.CASCADE, related_name="lots")
    category = models.CharField(max_length=32, choices=Category.choices)
    description = models.TextField()
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=32, default="cartons")  # cartons | MT | truck-months | pallet-months
    delivery_country = models.CharField(max_length=2)
    delivery_place = models.CharField(max_length=128)
    delivery_deadline = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "supply_lot"


class Bid(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        SUBMITTED = "submitted"

    org = models.ForeignKey(SupplierOrg, on_delete=models.CASCADE, related_name="bids")
    rfp = models.ForeignKey(RFP, on_delete=models.CASCADE, related_name="bids")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_bid"
        constraints = [models.UniqueConstraint(fields=["org", "rfp"], name="uniq_bid_per_org_rfp")]


class LotBid(models.Model):
    bid = models.ForeignKey(Bid, on_delete=models.CASCADE, related_name="lot_bids")
    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="lot_bids")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    lead_time_days = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "supply_lot_bid"
        constraints = [models.UniqueConstraint(fields=["bid", "lot"], name="uniq_lotbid_per_bid_lot")]


class BidScore(models.Model):
    lot_bid = models.ForeignKey(LotBid, on_delete=models.CASCADE, related_name="scores")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    technical_score = models.PositiveSmallIntegerField()  # 0-100
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_bid_score"
        constraints = [models.UniqueConstraint(fields=["lot_bid", "reviewer"], name="uniq_score_per_reviewer")]


class Award(models.Model):
    lot = models.OneToOneField(Lot, on_delete=models.CASCADE, related_name="award")
    lot_bid = models.ForeignKey(LotBid, on_delete=models.PROTECT, related_name="awards")
    awarded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_award"


class StaffRole(models.Model):
    class Role(models.TextChoices):
        PROCUREMENT_ADMIN = "procurement_admin"
        REVIEWER = "reviewer"
        GOV_OBSERVER = "gov_observer"
        FUNDER = "funder"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="supply_staff_role")
    role = models.CharField(max_length=32, choices=Role.choices)
    country = models.CharField(max_length=2, blank=True)  # gov_observer scoping

    class Meta:
        db_table = "supply_staff_role"


class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=64)
    obj_type = models.CharField(max_length=64)
    obj_id = models.CharField(max_length=64)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_audit_log"
        ordering = ["-created_at"]


# ---------------------------------------------------------------------------
# Execution side — what happens after award.
#
# Award is the immutable decision record; Contract is the execution container.
# Shipment state is DERIVED from the append-only SupplyEvent log, never set by
# hand, so the ingestion feed is the single source of truth.
# ---------------------------------------------------------------------------


class SupplyNode(models.Model):
    """A physical facility in the network, identified by a GLN."""

    class Kind(models.TextChoices):
        FACTORY = "factory"
        PORT = "port"
        WAREHOUSE = "warehouse"
        DISTRIBUTION_HUB = "distribution_hub"
        DELIVERY_POINT = "delivery_point"

    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=32, choices=Kind.choices)
    country = models.CharField(max_length=2)  # transit nodes may sit outside member countries
    gln = models.CharField(max_length=13, blank=True)
    location = gis_models.PointField(geography=True, null=True, blank=True)
    # null owner means an OES-network facility rather than a supplier's own
    owner = models.ForeignKey(SupplierOrg, null=True, blank=True, on_delete=models.SET_NULL, related_name="nodes")

    class Meta:
        db_table = "supply_node"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Appropriation(models.Model):
    """A funder's money envelope. Roots the funder view's flow of money."""

    funder_name = models.CharField(max_length=160)
    title = models.CharField(max_length=255)
    fiscal_year = models.CharField(max_length=16, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    iati_activity_id = models.CharField(max_length=128, blank=True)

    class Meta:
        db_table = "supply_appropriation"

    def __str__(self):
        return self.title


class Contract(models.Model):
    """Execution container created from an Award.

    Obligated / disbursed / delivered are kept as three distinct stages —
    collapsing them into one number is what makes funder reporting read as spin.
    """

    class Status(models.TextChoices):
        ACTIVE = "active"
        COMPLETED = "completed"
        CANCELLED = "cancelled"

    award = models.OneToOneField(Award, on_delete=models.CASCADE, related_name="contract")
    appropriation = models.ForeignKey(
        Appropriation, null=True, blank=True, on_delete=models.SET_NULL, related_name="contracts"
    )
    org = models.ForeignKey(SupplierOrg, on_delete=models.PROTECT, related_name="contracts")
    reference = models.CharField(max_length=64, unique=True)
    total_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=32, default="cartons")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    iati_activity_id = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_contract"

    def __str__(self):
        return self.reference

    @property
    def obligated_value(self):
        return self.total_quantity * self.unit_price

    @property
    def delivered_quantity(self):
        from django.db.models import Sum

        total = self.shipments.filter(status__in=[Shipment.Status.DELIVERED, Shipment.Status.CONFIRMED]).aggregate(
            n=Sum("quantity")
        )["n"]
        return total or 0

    @property
    def shipped_quantity(self):
        from django.db.models import Sum

        total = self.shipments.exclude(status=Shipment.Status.PLANNED).aggregate(n=Sum("quantity"))["n"]
        return total or 0

    @property
    def disbursed_value(self):
        """Only confirmed deliveries are paid for."""
        from django.db.models import Sum

        total = self.shipments.filter(status=Shipment.Status.CONFIRMED).aggregate(n=Sum("quantity"))["n"]
        return (total or 0) * self.unit_price


class Shipment(models.Model):
    """One consignment moving between two nodes. Status is derived from events."""

    class Status(models.TextChoices):
        PLANNED = "planned"
        IN_TRANSIT = "in_transit"
        DELIVERED = "delivered"
        CONFIRMED = "confirmed"

    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="shipments")
    reference = models.CharField(max_length=64, unique=True)
    asn_reference = models.CharField(max_length=64, blank=True)
    origin = models.ForeignKey(SupplyNode, on_delete=models.PROTECT, related_name="departures")
    destination = models.ForeignKey(SupplyNode, on_delete=models.PROTECT, related_name="arrivals")
    waypoints = models.JSONField(default=list)  # ordered SupplyNode ids between origin and destination
    route = gis_models.LineStringField(geography=True, null=True, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=32, default="cartons")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    departed_at = models.DateTimeField(null=True, blank=True)
    eta = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_shipment"
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference

    @property
    def value(self):
        return self.quantity * self.contract.unit_price


class ShipmentLine(models.Model):
    """A GTIN + batch/lot line within a shipment (the ASN item level)."""

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="lines")
    gtin = models.CharField(max_length=14)
    batch_lot = models.CharField(max_length=32, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=32, default="cartons")
    sscc = models.CharField(max_length=18, blank=True)  # pallet licence plate

    class Meta:
        db_table = "supply_shipment_line"


class Milestone(models.Model):
    """A planned/estimated/actual event at a node.

    The three timestamps follow DCSA's PLN/EST/ACT classifier, which is what
    makes an honest "ETA versus plan" delta possible.
    """

    class Kind(models.TextChoices):
        DEPART = "depart"
        ARRIVE = "arrive"

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="milestones")
    node = models.ForeignKey(SupplyNode, on_delete=models.PROTECT, related_name="milestones")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    sequence = models.PositiveSmallIntegerField(default=0)
    planned_at = models.DateTimeField(null=True, blank=True)
    estimated_at = models.DateTimeField(null=True, blank=True)
    actual_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "supply_milestone"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["shipment", "node", "kind", "sequence"], name="uniq_milestone_per_leg")
        ]

    @property
    def delta_days(self):
        """Actual (or current estimate) minus plan, in days. None when unknowable."""
        reference = self.actual_at or self.estimated_at
        if not reference or not self.planned_at:
            return None
        return round((reference - self.planned_at).total_seconds() / 86400, 1)


class SupplyEvent(models.Model):
    """Append-only, EPCIS-shaped visibility event. The source of truth.

    ``source_tier`` records HOW the event reached us, which is the honest part
    of the demo: a Kano factory posts EPCIS, a despatch posts an ASN, and the
    Port Sudan corridor arrives as a phone check-in.
    """

    class EventType(models.TextChoices):
        OBJECT = "object"
        AGGREGATION = "aggregation"
        TRANSFORMATION = "transformation"

    class BizStep(models.TextChoices):
        COMMISSIONING = "commissioning"
        PACKING = "packing"
        LOADING = "loading"
        DEPARTING = "departing"
        ARRIVING = "arriving"
        RECEIVING = "receiving"
        INSPECTING = "inspecting"
        STORING = "storing"

    class SourceTier(models.TextChoices):
        EPCIS = "epcis"
        ASN = "asn"
        CHECKIN = "checkin"
        PORTAL = "portal"

    org = models.ForeignKey(SupplierOrg, null=True, on_delete=models.SET_NULL, related_name="events")
    shipment = models.ForeignKey(Shipment, null=True, blank=True, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=24, choices=EventType.choices, default=EventType.OBJECT)
    biz_step = models.CharField(max_length=24, choices=BizStep.choices)
    disposition = models.CharField(max_length=32, blank=True)
    event_time = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    read_point = models.ForeignKey(SupplyNode, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    epc_list = models.JSONField(default=list)  # SSCCs / serialised keys
    quantity_list = models.JSONField(default=list)  # [{gtin, batch_lot, quantity, uom}]
    biz_transactions = models.JSONField(default=dict)  # {po, desadv, ...}
    source_tier = models.CharField(max_length=16, choices=SourceTier.choices)
    external_id = models.CharField(max_length=128, blank=True)  # idempotency key
    raw = models.JSONField(default=dict)

    class Meta:
        db_table = "supply_event"
        ordering = ["event_time", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["org", "external_id"],
                condition=models.Q(external_id__gt=""),
                name="uniq_event_external_id_per_org",
            )
        ]


class Discrepancy(models.Model):
    """A receipt that does not reconcile with what was despatched."""

    class Status(models.TextChoices):
        OPEN = "open"
        RESOLVED = "resolved"

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="discrepancies")
    event = models.ForeignKey(
        SupplyEvent, null=True, blank=True, on_delete=models.SET_NULL, related_name="discrepancies"
    )
    expected_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    received_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "supply_discrepancy"
        ordering = ["-created_at"]

    @property
    def shortfall(self):
        return self.expected_quantity - self.received_quantity


class ApiToken(models.Model):
    """Org-scoped bearer token for the ingestion API. Stored hashed."""

    org = models.ForeignKey(SupplierOrg, on_delete=models.CASCADE, related_name="api_tokens")
    label = models.CharField(max_length=80)
    token_hash = models.CharField(max_length=64, unique=True)
    prefix = models.CharField(max_length=12)  # shown in the UI so a token is recognisable
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)

    class Meta:
        db_table = "supply_api_token"
        ordering = ["-created_at"]
