"""Local-only dev seeder for the supply domain.

Stands up a labs-only synthetic programme plus a procurement round whose quotes
reproduce the SHAPES that make a real round hard to compare — one quote that
never stated its pack spec, one priced per sachet for a different quantity than
the round, one excluding duties with no amount, and one complete. The screens
then show a ranked leader marked provisional beside three blocked suppliers,
which is the state the app exists to handle.

THIS REPOSITORY IS PUBLIC. Every supplier name, contact and price here is
invented. Real supplier data goes in through the UI or the API, never into a
committed file.

Writes land in the LOCAL database, which is the supply domain's system of
record. The labs-only programme id (>= 10_000) marks this data as synthetic
(see supply_chain/scopes.py): that is what permits `--reset` to purge the
programme wholesale, and `SupplyDataAccess.purge()` refuses to do it for any
real programme.

Everything here goes in through `call_operation`, with the same schemas the
HTTP API and the MCP server enforce. A seeder with a private write path can
produce a demo of a system that does not exist.

    make manage CMD="supply_dev_seed"
    make manage CMD="supply_dev_seed --reset"
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

PROGRAMME_ID = 10501
DEV_USERNAME = "dev"
DEV_PASSWORD = "dev"


class Command(BaseCommand):
    help = "Seed a local labs-only supply programme with a round in a realistically messy state."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete this programme's records first.")

    def handle(self, *args, **options):
        self._dev_user()
        self._synthetic_programme()

        access = SupplyDataAccess(access_token="local-dev", program_id=PROGRAMME_ID)

        if options["reset"]:
            counts = access.purge()
            summary = ", ".join(f"{n} {label}" for label, n in sorted(counts.items())) or "nothing to delete"
            self.stdout.write(f"reset: {summary}")
        op = lambda name, **payload: call_operation(name, access, payload)  # noqa: E731

        # --- reference data -------------------------------------------------
        # The course definition is PROVISIONAL: 2 sachets/day over 75 days is what
        # the programme's buyer described verbally, not a protocol anyone has
        # confirmed, and `source` says so. Drop this block to see the honest
        # behaviour when a programme has not supplied its ration table --
        # per-course and per-child figures come back Unconfirmed.
        op(
            "commodity_upsert",
            data={
                "slug": "rutf",
                "name": "Ready-to-use therapeutic food",
                "category": "therapeutic_food",
                "base_unit": "sachet",
                "pack_unit": "carton",
                "base_per_pack": 150,
                "base_unit_grams": 92,
                "shelf_life_months_minimum": 18,
                "spec_requirements": [],
                "course_definition": {
                    "base_units_per_day": "2",
                    "days_per_course": 75,
                    "base_units_per_course": 150,
                    "source": "PROVISIONAL - described verbally, not a confirmed protocol",
                },
            },
        )
        op(
            "commodity_upsert",
            data={
                "slug": "infant-scale",
                "name": "Infant scale",
                "category": "equipment",
                "base_unit": "unit",
                "pack_unit": "box",
                "base_per_pack": 1,
                "spec_requirements": [
                    {
                        "field": "minimum_graduation_g",
                        "operator": "<=",
                        "value": 20,
                        "unit": "g",
                        "rationale": "100 g increments cannot record an infant's weight change",
                    }
                ],
            },
        )

        # Two RUTF items that DISAGREE on pack configuration — the condition the
        # item master exists to surface, and invisible at commodity level.
        item_150 = op(
            "item_upsert",
            data={
                "sku": "northwind-rutf-92g",
                "name": "Northwind RUTF 92 g",
                "commodity_slug": "rutf",
                "manufacturer": "Northwind Nutrition",
                "base_unit": "sachet",
                "pack_unit": "carton",
                "base_per_pack": 150,
                "base_unit_grams": 92,
                "shelf_life_months": 24,
                "status": "active",
            },
        )
        item_144 = op(
            "item_upsert",
            data={
                "sku": "harmattan-rutf-92g",
                "name": "Harmattan RUTF 92 g",
                "commodity_slug": "rutf",
                "manufacturer": "Harmattan Foods",
                "base_unit": "sachet",
                "pack_unit": "carton",
                "base_per_pack": 144,
                "base_unit_grams": 92,
                "shelf_life_months": 24,
                "status": "active",
            },
        )
        op(
            "item_upsert",
            data={
                "sku": "bx-10-infant-scale",
                "name": "BX-10 infant scale",
                "commodity_slug": "infant-scale",
                "manufacturer": "Atlas Instruments",
                "base_per_pack": 1,
                "spec_attributes": {"minimum_graduation_g": 100},
                "status": "active",
            },
        )

        suppliers = {}
        for name, kind, country, city in [
            ("Harmattan Foods", "manufacturer", "NG", "Kano"),
            ("Northwind Nutrition", "manufacturer", "KE", "Nairobi"),
            ("Sahel Therapeutics", "distributor", "BF", "Ouagadougou"),
            ("Atlas Nutrition", "manufacturer", "ET", "Addis Ababa"),
            ("Komadugu Supply Co", "trader", "NG", "Lagos"),
        ]:
            rec = op(
                "supplier_create",
                data={
                    "name": name,
                    "type": kind,
                    "country": country,
                    "city": city,
                    "status": "contacted",
                    "contacts": [
                        {"name": "Sales desk", "role": "sales", "email": f"sales@{name.split()[0].lower()}.example"}
                    ],
                },
            )
            suppliers[name] = rec["id"]

        # --- the round ------------------------------------------------------
        round_rec = op(
            "round_create",
            data={
                "label": "Round 2 — February",
                "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
                "delivery_point": {
                    "name": "Central store",
                    "city": "Kano",
                    "country": "NG",
                    "country_name": "Nigeria",
                    "incoterm_requested": "DDP",
                },
                "response_deadline": "2026-09-26",
                "reminder_interval_days": 7,
                "shelf_life_months_minimum": 18,
                "notes_to_supplier": (
                    "We are a small programme buying for a pilot, so our volumes are well "
                    "below a typical institutional contract. We would still value a quotation."
                ),
            },
        )
        round_id = round_rec["id"]
        op("round_open", round_id=round_id)

        for name in suppliers:
            op(
                "outreach_log",
                data={
                    "round_id": round_id,
                    "supplier_id": suppliers[name],
                    "channel": "manual",
                    "sent_on": "2026-09-09",
                    "responded": name != "Komadugu Supply Co",
                    "response_kind": "quote" if name != "Komadugu Supply Co" else "no_reply",
                },
            )

        common = {"round_id": round_id, "commodity_slug": "rutf", "as_quoted_currency": "USD", "fx_rate_to_usd": "1"}

        # COMPARABLE — everything stated.
        op(
            "quote_record",
            data={
                **common,
                "supplier_id": suppliers["Harmattan Foods"],
                "item_id": item_144["id"],
                "as_quoted_amount": "50.00",
                "as_quoted_unit": "per_pack",
                "quantity_basis": "2000",
                "quantity_basis_unit": "carton",
                "pack_spec_source": "trade_item_confirmed",
                "freight_basis": "included",
                "duties_basis": "included",
                "incoterm": "DDP",
                "shelf_life_months_stated": 24,
                "moq": "500",
                "moq_unit": "carton",
                "lead_time_days": 45,
                "validity_until": "2026-12-31",
            },
        )

        # BLOCKED — never said how many sachets are in a carton.
        op(
            "quote_record",
            data={
                **common,
                "supplier_id": suppliers["Northwind Nutrition"],
                "as_quoted_amount": "52.42",
                "as_quoted_unit": "per_pack",
                "quantity_basis": "2000",
                "quantity_basis_unit": "carton",
                "pack_spec_source": "not_stated",
                "freight_basis": "excluded",
                "freight_amount": "2186.58",
                "duties_basis": "included",
                "incoterm": "FOB",
                "shelf_life_months_stated": 18,
                "moq": "500",
                "moq_unit": "carton",
                "lead_time_days": 60,
                "validity_until": "2026-11-30",
            },
        )

        # BLOCKED — priced per sachet, for a different quantity, freight unsaid.
        op(
            "quote_record",
            data={
                **common,
                "supplier_id": suppliers["Sahel Therapeutics"],
                "item_id": item_150["id"],
                "as_quoted_amount": "0.46",
                "as_quoted_unit": "per_base_unit",
                "quantity_basis": "2667",
                "quantity_basis_unit": "carton",
                "pack_spec_source": "trade_item_confirmed",
                "freight_basis": "not_specified",
                "duties_basis": "included",
                "shelf_life_months_stated": 20,
                "moq": "667",
                "moq_unit": "carton",
                "lead_time_days": 30,
                "validity_until": "2026-10-31",
            },
        )

        # BLOCKED — duties excluded, amount unknown (free-zone origin).
        op(
            "quote_record",
            data={
                **common,
                "supplier_id": suppliers["Atlas Nutrition"],
                "item_id": item_150["id"],
                "as_quoted_amount": "50.00",
                "as_quoted_unit": "per_pack",
                "quantity_basis": "2000",
                "quantity_basis_unit": "carton",
                "pack_spec_source": "trade_item_confirmed",
                "freight_basis": "included",
                "duties_basis": "excluded",
                "duties_note": "Manufactured in a free economic zone; import duties payable on arrival.",
                "incoterm": "DAP",
                "shelf_life_months_stated": 24,
                "moq": "250",
                "moq_unit": "carton",
                "lead_time_days": 40,
                "validity_until": "2026-12-15",
            },
        )

        snapshot = op("round_compare", round_id=round_id, commodity_slug="rutf")
        self.stdout.write(self.style.SUCCESS("\nSeeded."))
        self.stdout.write(f"  user            {DEV_USERNAME} / {DEV_PASSWORD}  (view_synthetic_opps on)")
        self.stdout.write(f"  programme       {PROGRAMME_ID}")
        self.stdout.write(f"  round           {round_id}")
        self.stdout.write(
            f"  comparison      {snapshot['comparable_count']} of {snapshot['total_count']} comparable, "
            f"provisional={snapshot['provisional']}, ranked_by={snapshot['ranked_by']}"
        )
        self.stdout.write("")
        self.stdout.write(f"  open  http://localhost:8000/supply/?program_id={PROGRAMME_ID}")
        self.stdout.write(f"  login http://localhost:8000/admin/  ({DEV_USERNAME}/{DEV_PASSWORD}) first")

    def _dev_user(self):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=DEV_USERNAME,
            defaults={"email": "dev@dimagi.com", "is_staff": True, "is_superuser": True},
        )
        user.view_synthetic_opps = True
        user.is_staff = True
        user.is_superuser = True
        user.set_password(DEV_PASSWORD)
        user.save()
        self.stdout.write(f"user {DEV_USERNAME} {'created' if created else 'updated'}")
        return user

    def _synthetic_programme(self):
        opp, created = SyntheticOpportunity.objects.update_or_create(
            opportunity_id=PROGRAMME_ID,
            defaults={
                "label": "RUTF Procurement (local demo)",
                "gdrive_folder_id": "",
                "enabled": True,
                "labs_only": True,
                "org_name": "Connect RUTF (local demo)",
                "program_name": "RUTF Procurement (local demo)",
                "program_id": PROGRAMME_ID,
                "allowed_domains": [],
            },
        )
        self.stdout.write(f"synthetic programme {PROGRAMME_ID} {'created' if created else 'updated'}")
        return opp
