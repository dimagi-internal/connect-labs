"""
SQL cache models for the analysis framework.

Three tables for different cache levels:
- RawVisitCache: Raw visit data from CSV (one row per visit)
- ComputedVisitCache: Computed visit fields (one row per visit per config)
- ComputedFLWCache: Aggregated FLW results (one row per FLW per config)

All tables use TTL-based cleanup via expires_at field.
Cache invalidation is based on visit_count changes.
"""

from django.db import models
from django.utils import timezone


class RawVisitCache(models.Model):
    """
    Cached raw visit data from the Connect API.

    One row per visit. Mirrors the columns from UserVisitDataSerializer.
    Shared across all configs for the same opportunity.
    """

    # Cache metadata
    opportunity_id = models.IntegerField(db_index=True)
    visit_count = models.IntegerField(help_text="Visit count when cached, for invalidation")
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Visit data (mirrors UserVisitDataSerializer columns)
    visit_id = models.CharField(
        max_length=255, db_index=True, help_text="Visit ID (numeric for Connect, UUID for CCHQ)"
    )
    username = models.CharField(max_length=255, db_index=True)
    deliver_unit = models.CharField(max_length=500, blank=True)
    deliver_unit_id = models.IntegerField(null=True, blank=True)
    entity_id = models.CharField(max_length=255, blank=True)
    entity_name = models.CharField(max_length=500, blank=True)
    visit_date = models.DateField(db_index=True, null=True, blank=True)
    status = models.CharField(max_length=50, db_index=True, blank=True)
    reason = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    flagged = models.BooleanField(default=False)
    flag_reason = models.JSONField(default=dict, blank=True)
    form_json = models.JSONField(default=dict, blank=True)
    completed_work = models.JSONField(default=dict, blank=True)
    status_modified_date = models.DateTimeField(null=True, blank=True)
    review_status = models.CharField(max_length=50, blank=True)
    review_created_on = models.DateTimeField(null=True, blank=True)
    justification = models.TextField(blank=True)
    date_created = models.DateTimeField(null=True, blank=True)
    completed_work_id = models.IntegerField(null=True, blank=True)
    images = models.JSONField(default=list, blank=True)

    class Meta:
        app_label = "labs"
        db_table = "labs_raw_visit_cache"
        indexes = [
            models.Index(fields=["opportunity_id", "visit_count"]),
            models.Index(fields=["opportunity_id", "username"]),
        ]

    @classmethod
    def cleanup_expired(cls):
        """Delete all expired cache entries."""
        deleted, _ = cls.objects.filter(expires_at__lt=timezone.now()).delete()
        return deleted

    @classmethod
    def invalidate_opportunity(cls, opportunity_id: int):
        """Delete all cache entries for an opportunity."""
        deleted, _ = cls.objects.filter(opportunity_id=opportunity_id).delete()
        return deleted


class ComputedVisitCache(models.Model):
    """
    Cached computed visit results.

    One row per visit per config. Stores computed fields as JSON.
    Different configs produce different rows (identified by config_hash).
    """

    # Cache metadata
    opportunity_id = models.IntegerField(db_index=True)
    config_hash = models.CharField(max_length=32, db_index=True, help_text="Hash of analysis config")
    visit_count = models.IntegerField(help_text="Visit count when cached, for invalidation")
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Visit identification
    visit_id = models.CharField(max_length=255, db_index=True)
    username = models.CharField(max_length=255, db_index=True)

    # Base visit fields (denormalized from RawVisitCache to avoid joins)
    visit_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=50, blank=True, null=True)
    flagged = models.BooleanField(default=False)
    location = models.CharField(max_length=255, blank=True, null=True)
    deliver_unit = models.CharField(max_length=500, blank=True, null=True)
    deliver_unit_id = models.IntegerField(null=True, blank=True)
    entity_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    entity_name = models.CharField(max_length=500, blank=True, null=True)

    # Computed data
    computed_fields = models.JSONField(default=dict, help_text="Computed field values from config")

    class Meta:
        app_label = "labs"
        db_table = "labs_computed_visit_cache"
        indexes = [
            models.Index(fields=["opportunity_id", "config_hash", "visit_count"]),
        ]

    @classmethod
    def cleanup_expired(cls):
        """Delete all expired cache entries."""
        deleted, _ = cls.objects.filter(expires_at__lt=timezone.now()).delete()
        return deleted

    @classmethod
    def invalidate_opportunity_config(cls, opportunity_id: int, config_hash: str):
        """Delete cache entries for a specific opportunity and config."""
        deleted, _ = cls.objects.filter(
            opportunity_id=opportunity_id,
            config_hash=config_hash,
        ).delete()
        return deleted


class ComputedFLWCache(models.Model):
    """
    Cached aggregated FLW results.

    One row per FLW per config. Stores aggregated fields as JSON.
    This is the final output level for FLW analysis dashboards.
    """

    # Cache metadata
    opportunity_id = models.IntegerField(db_index=True)
    config_hash = models.CharField(max_length=32, db_index=True, help_text="Hash of analysis config")
    visit_count = models.IntegerField(help_text="Visit count when cached, for invalidation")
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # FLW identification
    username = models.CharField(max_length=255, db_index=True)

    # Aggregated data
    aggregated_fields = models.JSONField(default=dict, help_text="Aggregated field values")

    # Standard FLW metrics (computed during aggregation)
    total_visits = models.IntegerField(default=0)
    approved_visits = models.IntegerField(default=0)
    pending_visits = models.IntegerField(default=0)
    rejected_visits = models.IntegerField(default=0)
    flagged_visits = models.IntegerField(default=0)
    first_visit_date = models.DateField(null=True, blank=True)
    last_visit_date = models.DateField(null=True, blank=True)

    class Meta:
        app_label = "labs"
        db_table = "labs_computed_flw_cache"
        indexes = [
            models.Index(fields=["opportunity_id", "config_hash", "visit_count"]),
        ]

    @classmethod
    def cleanup_expired(cls):
        """Delete all expired cache entries."""
        deleted, _ = cls.objects.filter(expires_at__lt=timezone.now()).delete()
        return deleted

    @classmethod
    def invalidate_opportunity_config(cls, opportunity_id: int, config_hash: str):
        """Delete cache entries for a specific opportunity and config."""
        deleted, _ = cls.objects.filter(
            opportunity_id=opportunity_id,
            config_hash=config_hash,
        ).delete()
        return deleted
