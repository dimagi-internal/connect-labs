"""Supplier org profile + certification mutations."""
from datetime import date

from ..models import Certification

EDITABLE_PROFILE_FIELDS = (
    "registration_number",
    "hq_city",
    "description",
    "contact_name",
    "contact_email",
    "gln",
    "gs1_company_prefix",
)


class ActionError(Exception):
    """Raised for rule violations; API layer turns this into a 400."""


def update_profile(org, data):
    """Apply the editable subset of profile fields. legal_name/country are fixed
    at registration — changing a supplier's legal identity is not a self-service
    action."""
    for field in EDITABLE_PROFILE_FIELDS:
        if field in data:
            setattr(org, field, (data[field] or "").strip())
    org.save()
    return org


def add_certification(org, data):
    cert_type = (data.get("cert_type") or "").strip()
    if not cert_type:
        raise ActionError("cert_type is required")
    expiry = data.get("expiry_date") or None
    if expiry:
        try:
            expiry = date.fromisoformat(expiry)
        except ValueError:
            raise ActionError("expiry_date must be ISO format (YYYY-MM-DD)")
    return Certification.objects.create(
        org=org,
        cert_type=cert_type,
        issuer=(data.get("issuer") or "").strip(),
        expiry_date=expiry,
        document_name=(data.get("document_name") or "").strip(),
    )


def delete_certification(org, cert_id):
    """Scoped to the org — a supplier can never delete a rival's certification."""
    deleted, _ = Certification.objects.filter(org=org, id=cert_id).delete()
    return deleted > 0
