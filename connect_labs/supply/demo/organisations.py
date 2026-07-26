"""Seeds who exists: supplier organisations, their certifications, and the
demo personas who log in.

Every supplier is written with ``update_or_create`` keyed on legal name, so
re-running refreshes rather than duplicates.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model

from ..models import Certification, StaffRole, SupplierMember, SupplierOrg
from .data import CERT_TYPES, CONTACT_NAMES, ORGS, STAFF, SUPPLIER_LOGIN, TODAY, demo_password

User = get_user_model()


def _seed_orgs(rng):
    orgs = {}
    for index, (name, country, city, categories, cert_profile) in enumerate(ORGS):
        org, _ = SupplierOrg.objects.update_or_create(
            legal_name=name,
            defaults={
                "country": country,
                "hq_city": city,
                "registration_number": f"{country}-{2015 + (index % 9)}-{4100 + index * 7}",
                "description": _describe(name, categories, city, country),
                "contact_name": CONTACT_NAMES[index % len(CONTACT_NAMES)],
                "contact_email": f"tenders@{_domain(name)}",
                "gln": f"62912345{index:04d}"[:13].ljust(13, "0"),
                "gs1_company_prefix": f"629123{index:02d}",
            },
        )
        org.categories_hint = categories  # transient, used by later stages
        _seed_certs(org, categories, cert_profile, rng)
        orgs[name] = org
    return orgs


def _describe(name, categories, city, country):
    if "rutf" in categories:
        return (
            f"{name} manufactures ready-to-use therapeutic food at its {city} plant, "
            "packed 150 sachets per carton to UNICEF specification with GS1 logistics "
            "labelling applied at palletisation."
        )
    if "therapeutic_milk" in categories:
        return f"{name} produces F-75 and F-100 therapeutic milk powders in {city}."
    if "transport" in categories:
        return (
            f"{name} operates road freight along the {city} corridor, including "
            "temperature-monitored trailers for nutrition commodities."
        )
    return f"{name} operates bonded and ambient warehousing in {city}, {country}."


def _domain(name):
    slug = "".join(ch.lower() for ch in name if ch.isalnum())[:18]
    return f"{slug}.example"


def _seed_certs(org, categories, profile, rng):
    wanted = []
    for cat in categories:
        wanted.extend(CERT_TYPES.get(cat, []))
    wanted = list(dict.fromkeys(wanted))

    if profile == "thin":
        wanted = wanted[:1]

    for i, cert_type in enumerate(wanted):
        if profile == "expiring" and i == 0:
            expiry = TODAY + timedelta(days=rng.randint(12, 55))
        else:
            expiry = TODAY + timedelta(days=rng.randint(200, 900))
        Certification.objects.update_or_create(
            org=org,
            cert_type=cert_type,
            defaults={
                "issuer": {"UNICEF RUTF approval": "UNICEF Supply Division"}.get(cert_type, "SGS"),
                "expiry_date": expiry,
                "document_name": f"{cert_type.lower().replace(' ', '-')}-certificate.pdf",
            },
        )

# ---------- users ----------


def _user(email, name):
    user, created = User.objects.update_or_create(username=email, defaults={"email": email, "name": name})
    if created or not user.has_usable_password():
        user.set_password(demo_password())
        user.save(update_fields=["password"])
    return user


def _seed_staff():
    staff = {}
    for email, name, role, country in STAFF:
        user = _user(email, name)
        StaffRole.objects.update_or_create(user=user, defaults={"role": role, "country": country})
        staff[role] = user
    return staff


def _seed_supplier_login(orgs):
    email, name, org_name = SUPPLIER_LOGIN
    user = _user(email, name)
    SupplierMember.objects.update_or_create(user=user, defaults={"org": orgs[org_name]})

# ---------- EOI rounds ----------
