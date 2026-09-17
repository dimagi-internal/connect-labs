"""What labs knows about an organisation, beyond who it is.

``labs.LabsOrg`` holds identity — name, country, and the keys that join it to
Connect — and deliberately nothing else, so that when Connect grows an
organisation data model the migration is a repointed foreign key rather than a
redesign. Everything in this module is the "else": the facts the LLO Directory
carries that Connect has no column for.

The directory is the master organisation list, not a lookup table for Connect.
An organisation that answered an EOI and was not selected never gets a Connect
row at all, so this registry is strictly larger than Connect's, and always will
be. That is the correction this app exists to make.
"""

from django.db import models

from connect_labs.labs.models import LabsOrg


class OrgProfile(models.Model):
    """The LLO Directory's Organizations row, minus the identity columns.

    One row per organisation. Every field here is something a person maintains
    in the sheet; nothing is derived from Connect. When Connect eventually
    carries these, this table is what gets read from and dropped — which is only
    possible because none of it leaked into ``LabsOrg``.
    """

    org = models.OneToOneField(LabsOrg, on_delete=models.CASCADE, related_name="marketplace_profile")

    has_used_connect = models.BooleanField(null=True, blank=True)
    year_established = models.IntegerField(null=True, blank=True)
    team_size = models.IntegerField(null=True, blank=True)
    flws_managed = models.IntegerField(null=True, blank=True)

    countries = models.JSONField(default=list, blank=True)
    regions = models.JSONField(default=list, blank=True)

    website = models.CharField(max_length=500, blank=True, default="")
    office_address = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    msa_link = models.CharField(max_length=500, blank=True, default="")
    work_order_link = models.CharField(max_length=500, blank=True, default="")

    # When the organisation entered the network: the date they answered an EOI,
    # which Connect never sees. Some are an exact submission date and some are a
    # cohort's publication date shared by everyone in it, so the basis travels
    # with the date and no display may imply precision it lacks.
    joined_at = models.DateField(null=True, blank=True, db_index=True)
    joined_basis = models.CharField(max_length=200, blank=True, default="")

    # Resolved from the address at import time. Precision travels with the point
    # because the sources behind it are not equivalent: a town matched in an
    # address is a pin, a country is a whole country. A map that hides the
    # difference draws a rooftop from the word "Nigeria".
    country_iso3 = models.CharField(max_length=3, blank=True, default="", db_index=True)
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    location_precision = models.CharField(
        max_length=8,
        blank=True,
        default="",
        choices=[("city", "city"), ("region", "region"), ("country", "country")],
    )
    location_label = models.CharField(max_length=160, blank=True, default="")

    source_row = models.IntegerField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "organisation profile"

    def __str__(self) -> str:
        return f"profile of {self.org.name}"


class OrgContact(models.Model):
    """A named person at an organisation.

    Rows rather than a JSON blob, because outreach sends mail to these: a
    recipient list has to be queryable, deduplicable, and individually
    suppressible. ``supply_chain.Supplier.contacts`` is a JSONField and is the
    shape being moved away from.

    These are real people's names, addresses and phone numbers. They live in the
    database and nowhere else — never in fixtures, tests or committed files.
    This repository is public.
    """

    org = models.ForeignKey(LabsOrg, on_delete=models.CASCADE, related_name="contacts")

    full_name = models.CharField(max_length=200, blank=True, default="")
    role_title = models.CharField(max_length=200, blank=True, default="")
    is_main_poc = models.BooleanField(default=False)
    email = models.EmailField(max_length=320)
    phone = models.CharField(max_length=64, blank=True, default="")
    notes = models.TextField(blank=True, default="")

    source_row = models.IntegerField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_main_poc", "full_name"]
        constraints = [
            models.UniqueConstraint(fields=["org", "email"], name="uniq_contact_email_per_org"),
        ]

    def __str__(self) -> str:
        return self.full_name or self.email


class OrgConnectSlug(models.Model):
    """A Connect org slug attributed to an organisation by a person.

    This is ``pulse.PulsePartnerAlias``, moved and generalised. The matcher is
    deliberately strict, so a handful of real organisations fall through it: a
    second workspace sharing no stem with the first, an abbreviation the slug
    never spells out, a typo in the directory itself. Loosening the rules would
    buy those few at the cost of guessing everywhere else.

    ``why`` is required, and enforced in ``save()`` rather than left to
    convention. An attribution without a stated reason is a guess someone will
    later trust, and the cost of a wrong one is one organisation's history filed
    under another organisation's name.

    The slug matches a Connect org slug exactly or as a ``slug-`` prefix, so an
    organisation's next workspace resolves without another edit.
    """

    org = models.ForeignKey(LabsOrg, on_delete=models.CASCADE, related_name="connect_slugs")
    slug = models.CharField(max_length=120, unique=True)
    why = models.TextField()

    source_row = models.IntegerField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]
        verbose_name = "Connect slug attribution"

    def save(self, *args, **kwargs):
        if not (self.why or "").strip():
            raise ValueError(
                f"OrgConnectSlug({self.slug!r}) needs a stated reason: an attribution "
                "without one is a guess someone will later trust."
            )
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.slug} → {self.org.name}"
