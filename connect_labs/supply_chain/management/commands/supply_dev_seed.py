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
                # From the published standard, not invented. The UNICEF
                # material number is deliberately left unset: there is a real
                # one, and putting a made-up value here would be exactly the
                # kind of authoritative-looking fiction this domain refuses.
                "spec_reference": "WHO/WFP/UNICEF/UN-SCN joint statement, 2007",
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

        # --- downstream: order, stock, distribution -------------------------
        # Everything below the award, so the screens have a chain to show and
        # the fulfilment and stock tiers are exercised end to end. The shapes
        # are the real ones; the numbers are invented.
        downstream = self._seed_downstream(op, round_id, item_150, item_144, suppliers)

        self.stdout.write(self.style.SUCCESS("\nSeeded."))
        self.stdout.write(f"  user            {DEV_USERNAME} / {DEV_PASSWORD}  (view_synthetic_opps on)")
        self.stdout.write(f"  programme       {PROGRAMME_ID}")
        self.stdout.write(f"  round           {round_id}")
        self.stdout.write(
            f"  comparison      {snapshot['comparable_count']} of {snapshot['total_count']} comparable, "
            f"provisional={snapshot['provisional']}, ranked_by={snapshot['ranked_by']}"
        )
        self.stdout.write(
            f"  order           contract {downstream['contract_id']} "
            f"(bought by the LLO) - {downstream['match_status']}"
        )
        self.stdout.write(
            f"  network         {downstream['points']} supply points, "
            f"{downstream['workers']} of them field workers"
        )
        self.stdout.write(f"  checks          {downstream['checks']} open")
        self.stdout.write("")
        self.stdout.write(f"  open  http://localhost:8000/supply/?program_id={PROGRAMME_ID}")
        self.stdout.write(f"  login http://localhost:8000/admin/  ({DEV_USERNAME}/{DEV_PASSWORD}) first")

    def _seed_downstream(self, op, round_id, item_150, item_144, suppliers):
        """Award the round, let the LLO buy it, and run the goods out to workers.

        The arrangement this exists to demonstrate: we source, the local
        partner contracts and pays so the consignment clears under local duty
        relief, and every fact from the purchase order onwards reaches us
        second-hand -- which is why each write below names its `source`.

        Deliberately left imperfect, because the imperfections are the point:
        no purchase-order reference, no exemption certificate behind the
        claimed relief, one shipment still at customs, an invoice for the full
        order when only part of it arrived, and one worker whose reported
        stock does not match the ledger.
        """
        winner = op("quote_list", round_id=round_id)
        winner = next(q for q in winner if q["supplier_id"] == suppliers["Harmattan Foods"])
        award = op(
            "award_create",
            round_id=round_id,
            quote_id=winner["id"],
            rationale="Only quote comparable on a like-for-like basis; three others blocked.",
            decided_by="dev",
        )

        llo = op(
            "party_upsert",
            data={
                "slug": "llo-kano",
                "name": "Connect-RUTF local partner (Kano)",
                "kind": "partner_org",
                "roles": ["buyer", "receiver", "distributor", "payer"],
                "country": "NG",
            },
        )

        contract = op(
            "contract_create",
            data={
                "round_id": round_id,
                "award_id": award["id"],
                "supplier_id": suppliers["Harmattan Foods"],
                "item_id": item_144["id"],
                "commodity_slug": "rutf",
                # The fork: we awarded it, the partner buys it.
                "buyer_of_record": "partner_org",
                "buyer_party_id": llo["id"],
                "quantity": "2000",
                "quantity_unit": "carton",
                "unit_price": "50.00",
                "unit_price_unit": "per_pack",
                "currency": "USD",
                "freight_basis": "included",
                # Claimed, with nothing attached. The landed total therefore
                # comes back Unconfirmed rather than looking like a bargain.
                "duty_relief_claimed": True,
                "duties_basis": "excluded",
                "vat_basis": "excluded",
                "incoterm": "DDP",
                "promised_lead_time_days": 45,
                "status": "placed",
                "signed_on": "2026-06-02",
                "source": "partner_reported",
                "recorded_by_party_id": llo["id"],
            },
        )

        store = op(
            "supply_point_upsert",
            data={
                "slug": "central-store-kano",
                "name": "Central store, Kano",
                "kind": "central_store",
                "managed_by_party_id": llo["id"],
                "admin_area": "Kano Municipal",
                "min_months_of_stock": "2",
                "max_months_of_stock": "6",
                "source": "we_recorded",
            },
        )
        op("contract_update", contract_id=contract["id"], data={"delivery_supply_point_id": store["id"]})

        # Two shipments: one delivered, one held at customs with no
        # certificate -- so in-transit stock stays out of cover.
        op(
            "shipment_record",
            data={
                "contract_id": contract["id"],
                "reference": "SH-1",
                "status": "delivered",
                "dispatched_on": "2026-06-04",
                "carrier": "Overland freight",
                "source": "supplier_reported",
                "lines": [
                    {
                        "item_id": item_144["id"],
                        "batch": "HF-2606-A",
                        "expiry": "2028-06-30",
                        "quantity": "1200",
                        "quantity_unit": "carton",
                    }
                ],
            },
        )
        op(
            "shipment_record",
            data={
                "contract_id": contract["id"],
                "reference": "SH-2",
                "status": "at_customs",
                "dispatched_on": "2026-07-04",
                "source": "supplier_reported",
                "lines": [
                    {
                        "item_id": item_144["id"],
                        "batch": "HF-2607-B",
                        "expiry": "2028-07-31",
                        "quantity": "800",
                        "quantity_unit": "carton",
                    }
                ],
            },
        )

        op(
            "receipt_record",
            data={
                "contract_id": contract["id"],
                "supply_point_id": store["id"],
                "reference": "GRN-001",
                "received_on": "2026-06-18",
                "source": "partner_reported",
                "recorded_by_party_id": llo["id"],
                "lines": [
                    {
                        "item_id": item_144["id"],
                        "batch": "HF-2606-A",
                        "expiry": "2028-06-30",
                        "quantity_accepted": "1185",
                        "quantity_rejected": "15",
                        "rejection_reason": "cartons crushed in transit",
                        "quantity_unit": "carton",
                    }
                ],
            },
        )

        # Billed for the whole order while 800 cartons sit at a border.
        op(
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "reference": "INV-2026-118",
                "issued_on": "2026-06-20",
                "amount": "100000.00",
                "quantity_billed": "2000",
                "quantity_unit": "carton",
                "source": "supplier_reported",
            },
        )

        workers = []
        for slug, name, lga in [
            ("flw-dala-01", "Field worker, Dala", "Dala"),
            ("flw-fagge-01", "Field worker, Fagge", "Fagge"),
            ("flw-gwale-01", "Field worker, Gwale", "Gwale"),
            ("flw-kumbotso-01", "Field worker, Kumbotso", "Kumbotso"),
        ]:
            workers.append(
                op(
                    "supply_point_upsert",
                    data={
                        "slug": slug,
                        "name": name,
                        "kind": "user_held",
                        "opportunity_id": PROGRAMME_ID,
                        "connect_username": slug,
                        "parent_supply_point_id": store["id"],
                        "admin_area": lga,
                        "min_months_of_stock": "1",
                        "max_months_of_stock": "2",
                        "source": "we_recorded",
                    },
                )
            )

        # One run out to three of the four. Kumbotso gets nothing, which is
        # what makes its later dispensing unaccounted for.
        op(
            "distribution_record",
            data={
                "supply_point_id": store["id"],
                "opportunity_id": PROGRAMME_ID,
                "commodity_slug": "rutf",
                "distributed_on": "2026-08-02",
                "reference": "DIST-2026-08",
                "source": "partner_reported",
                "recorded_by_party_id": llo["id"],
                "lines": [
                    {
                        "to_supply_point_id": workers[0]["id"],
                        "item_id": item_144["id"],
                        "batch": "HF-2606-A",
                        "quantity": "18",
                        "quantity_unit": "carton",
                    },
                    {
                        "to_supply_point_id": workers[1]["id"],
                        "item_id": item_144["id"],
                        "batch": "HF-2606-A",
                        "quantity": "12",
                        "quantity_unit": "carton",
                    },
                    {
                        "to_supply_point_id": workers[2]["id"],
                        "item_id": item_144["id"],
                        "batch": "HF-2606-A",
                        "quantity": "15",
                        "quantity_unit": "carton",
                    },
                ],
            },
        )

        # Dispensing, as it arrives from the deliver form. Kumbotso dispenses
        # stock it was never issued.
        for worker, sachets in zip(workers, (2180, 1640, 1420, 460), strict=True):
            for week in range(12):
                op(
                    "movement_record",
                    data={
                        "kind": "consumption",
                        "occurred_on": f"2026-0{6 + week // 5}-{1 + (week % 4) * 7:02d}",
                        "commodity_slug": "rutf",
                        "item_id": item_144["id"],
                        "from_supply_point_id": worker["id"],
                        "quantity": str(round(sachets / 12)),
                        "quantity_unit": "sachet",
                        "source": "connect_visit",
                    },
                )

        # One worker's self-report disagrees with the ledger.
        op(
            "stock_report_ingest",
            rows=[
                {
                    "connect_username": "flw-dala-01",
                    "quantity": "2",
                    "counted_on": "2026-09-10",
                    "form_submission_id": "demo-sub-1",
                },
                {
                    "connect_username": "flw-fagge-01",
                    "quantity": "0",
                    "counted_on": "2026-09-10",
                    "form_submission_id": "demo-sub-2",
                },
            ],
            commodity_slug="rutf",
            quantity_unit="carton",
            opportunity_id=PROGRAMME_ID,
            item_id=item_144["id"],
        )

        match = op("contract_match", contract_id=contract["id"])
        checks = op("checks_list")
        return {
            "contract_id": contract["id"],
            "match_status": match["status"],
            "points": 1 + len(workers),
            "workers": len(workers),
            "checks": checks["count"],
        }

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
