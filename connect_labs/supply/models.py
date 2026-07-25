"""Procurement-side domain models for the OES supply satellite app.

All tables are prefixed ``supply_`` (satellite-site convention: the app's
data layer must be separable from the host by table name alone). Execution-
side models (SupplyNode, Contract, Shipment, SupplyEvent, ...) arrive in
Phase 2.
"""
from django.conf import settings
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
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="supply_membership"
    )
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
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
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
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
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
    source_submission = models.ForeignKey(
        EOISubmission, null=True, on_delete=models.SET_NULL, related_name="+"
    )
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
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
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
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    technical_score = models.PositiveSmallIntegerField()  # 0-100
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_bid_score"
        constraints = [models.UniqueConstraint(fields=["lot_bid", "reviewer"], name="uniq_score_per_reviewer")]


class Award(models.Model):
    lot = models.OneToOneField(Lot, on_delete=models.CASCADE, related_name="award")
    lot_bid = models.ForeignKey(LotBid, on_delete=models.PROTECT, related_name="awards")
    awarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_award"


class StaffRole(models.Model):
    class Role(models.TextChoices):
        PROCUREMENT_ADMIN = "procurement_admin"
        REVIEWER = "reviewer"
        GOV_OBSERVER = "gov_observer"
        FUNDER = "funder"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="supply_staff_role"
    )
    role = models.CharField(max_length=32, choices=Role.choices)
    country = models.CharField(max_length=2, blank=True)  # gov_observer scoping

    class Meta:
        db_table = "supply_staff_role"


class AuditLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField(max_length=64)
    obj_type = models.CharField(max_length=64)
    obj_id = models.CharField(max_length=64)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "supply_audit_log"
        ordering = ["-created_at"]
