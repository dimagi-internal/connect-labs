"""The Operation End Starvation demo world, as data.

Separated from the code that writes it so the narrative — which suppliers
exist, which corridors they serve, what is in flight — can be read and edited
without wading through ORM calls.

Geography mirrors the real RUTF producer landscape: plants in Kano, Lagos,
Ouagadougou and Addis Ababa, none in Sudan (which is supplied through Port
Sudan), and corridors through Djibouti and Lome for the two landlocked
countries. Organisation names are fictional.
"""
import os
from datetime import date

from ..models import StaffRole

TODAY = date.today()


SEED = 20260725

# Demo-persona password. The repo default is fine locally, but any deployed
# instance is publicly reachable and this seeds a procurement_admin — set
# SUPPLY_DEMO_PASSWORD in the environment there so the credential is not
# discoverable from the source.
REPO_DEMO_PASSWORD = "oes-demo-2026"


def demo_password():
    """The demo-persona password.

    Read at call time, not import time: a deployed instance sets
    SUPPLY_DEMO_PASSWORD, and resolving it lazily means tests and shells pick
    up the environment they are actually running in without reimporting.
    """
    return os.environ.get("SUPPLY_DEMO_PASSWORD", REPO_DEMO_PASSWORD)


# (legal_name, country, city, categories, cert_profile, gln_suffix)
# cert_profile: "strong" | "expiring" | "thin"
ORGS = [
    ("Savanna Nutrients Ltd", "NG", "Kano", ["rutf"], "strong"),
    ("Kano Therapeutic Foods PLC", "NG", "Kano", ["rutf", "therapeutic_milk"], "expiring"),
    ("Lagos NutriWorks Ltd", "NG", "Lagos", ["rutf"], "strong"),
    ("Faso NutriWorks SA", "BF", "Ouagadougou", ["rutf"], "strong"),
    ("Rift Valley Therapeutics PLC", "ET", "Addis Ababa", ["rutf", "therapeutic_milk"], "strong"),
    ("Abyssinia Nutrition Industries", "ET", "Addis Ababa", ["therapeutic_milk"], "expiring"),
    ("Sahel Milk Products SARL", "BF", "Bobo-Dioulasso", ["therapeutic_milk"], "thin"),
    ("Northern Corridor Logistics Ltd", "NG", "Maiduguri", ["transport"], "strong"),
    ("Blue Nile Freight Co", "SD", "Port Sudan", ["transport", "warehousing"], "expiring"),
    ("Horn Transit Services PLC", "ET", "Dire Dawa", ["transport"], "strong"),
    ("Djibouti Corridor Haulage", "DJ", "Djibouti", ["transport"], "strong"),
    ("Volta Overland SARL", "BF", "Ouagadougou", ["transport"], "thin"),
    ("TransSahel Carriers Ltd", "NG", "Kano", ["transport"], "thin"),
    ("Maiduguri Cold Chain Stores", "NG", "Maiduguri", ["warehousing"], "strong"),
    ("Kassala Warehousing Co", "SD", "Kassala", ["warehousing"], "expiring"),
    ("Addis Central Depot PLC", "ET", "Addis Ababa", ["warehousing"], "strong"),
]

# Tender contacts, indexed positionally against ORGS so the mapping is stable.
CONTACT_NAMES = [
    "Amina Bello",
    "Ibrahim Sanusi",
    "Folake Adeyemi",
    "Salif Ouédraogo",
    "Meseret Tadesse",
    "Yohannes Kebede",
    "Aïcha Traoré",
    "Grace Okonkwo",
    "Osman El-Tayeb",
    "Hanna Girma",
    "Fatouma Ahmed",
    "Boukary Zongo",
    "Nasir Danjuma",
    "Zainab Musa",
    "Awad Hassan",
    "Selam Bekele",
]

CERT_TYPES = {
    "rutf": ["ISO 22000", "GMP", "UNICEF RUTF approval"],
    "therapeutic_milk": ["ISO 22000", "GMP"],
    "transport": ["ISO 9001", "Goods-in-transit insurance"],
    "warehousing": ["ISO 9001", "Cold chain certification"],
}

STAFF = [
    ("oes-lead@oes.example", "Ada Nwosu", StaffRole.Role.PROCUREMENT_ADMIN, ""),
    ("oes-review@oes.example", "Tomas Berhane", StaffRole.Role.REVIEWER, ""),
    ("gov-ng@oes.example", "Hauwa Ibrahim", StaffRole.Role.GOV_OBSERVER, "NG"),
    ("usg@oes.example", "Dale Whitmore", StaffRole.Role.FUNDER, ""),
]

SUPPLIER_LOGIN = ("supplier@savanna.example", "Amina Bello", "Savanna Nutrients Ltd")

CLOSED_ROUND = "OES Supply Base 2026-A"
OPEN_ROUND = "OES Supply Base 2026-B"
LIVE_RFP = "RUTF Northeast Nigeria Q3 2026"
AWARDED_RFP = "RUTF Ethiopia Q2 2026"

# Additional fully-awarded solicitations, one per corridor, so post-award
# execution has a contract per country rather than everything hanging off one.
# (winner org, RFP title, brief, country, lot description, cartons, unit price)
CORRIDOR_AWARDS = [
    (
        "Savanna Nutrients Ltd",
        "RUTF Northeast Nigeria Q2 2026",
        "Supply of RUTF to the north-east Nigeria response for the Q2 caseload.",
        "NG",
        "45,000 cartons RUTF delivered to Maiduguri",
        45000,
        42.10,
    ),
    (
        "Faso NutriWorks SA",
        "RUTF Sahel Q2 2026",
        "Supply of RUTF to the Burkina Faso Sahel region for the Q2 caseload.",
        "BF",
        "20,000 cartons RUTF delivered to Djibo",
        20000,
        43.60,
    ),
    (
        "Blue Nile Freight Co",
        "Sudan Corridor Logistics 2026",
        "Inland haulage and warehousing for imported RUTF through Port Sudan.",
        "SD",
        "Inland haulage of 40,000 cartons from Port Sudan",
        40000,
        3.20,
    ),
]


# name, kind, country, lon, lat, owning org (None = OES network)
NODES = [
    # factories — mirrors the real RUTF producer geography
    ("Kano RUTF Plant", "factory", "NG", 8.5920, 12.0022, "Savanna Nutrients Ltd"),
    ("Lagos Therapeutic Foods Plant", "factory", "NG", 3.3792, 6.5244, "Lagos NutriWorks Ltd"),
    ("Ouagadougou RUTF Plant", "factory", "BF", -1.5197, 12.3714, "Faso NutriWorks SA"),
    ("Addis Ababa RUTF Plant", "factory", "ET", 38.7578, 8.9806, "Rift Valley Therapeutics PLC"),
    # ports and corridor gateways — Ethiopia and Burkina Faso are landlocked,
    # so their corridors run through Djibouti and Lomé respectively
    ("Port of Lagos (Apapa)", "port", "NG", 3.3600, 6.4400, None),
    ("Port Sudan", "port", "SD", 37.2164, 19.6158, None),
    ("Port of Djibouti", "port", "DJ", 43.1450, 11.5890, None),
    ("Port of Lomé", "port", "TG", 1.2833, 6.1319, None),
    # national and regional warehouses
    ("Kano Central Warehouse", "warehouse", "NG", 8.5167, 12.0000, None),
    ("Khartoum Central Warehouse", "warehouse", "SD", 32.5599, 15.5007, None),
    ("Addis Central Depot", "warehouse", "ET", 38.7400, 9.0100, "Addis Central Depot PLC"),
    ("Ouagadougou Central Warehouse", "warehouse", "BF", -1.5330, 12.3600, None),
    ("Kassala Forward Store", "warehouse", "SD", 36.4000, 15.4500, "Kassala Warehousing Co"),
    ("Dire Dawa Transit Store", "warehouse", "ET", 41.8661, 9.5931, None),
    # distribution hubs in the famine-affected zones
    ("Maiduguri Distribution Hub", "distribution_hub", "NG", 13.1510, 11.8311, None),
    ("Damaturu Distribution Hub", "distribution_hub", "NG", 11.9660, 11.7480, None),
    ("El Fasher Distribution Hub", "distribution_hub", "SD", 25.3494, 13.6279, None),
    ("Nyala Distribution Hub", "distribution_hub", "SD", 24.8917, 12.0489, None),
    ("Gode Distribution Hub", "distribution_hub", "ET", 43.5500, 5.9527, None),
    ("Jijiga Distribution Hub", "distribution_hub", "ET", 42.7947, 9.3500, None),
    ("Djibo Distribution Hub", "distribution_hub", "BF", -1.6300, 14.0995, None),
    ("Dori Distribution Hub", "distribution_hub", "BF", -0.0345, 14.0354, None),
    # last-mile delivery points
    ("Bama Health Post", "delivery_point", "NG", 13.6890, 11.5210, None),
    ("Monguno Health Post", "delivery_point", "NG", 13.6100, 12.6750, None),
    ("Tawila Nutrition Site", "delivery_point", "SD", 25.0000, 13.8300, None),
    ("Kebkabiya Nutrition Site", "delivery_point", "SD", 24.0700, 13.6500, None),
    ("Kelafo Nutrition Site", "delivery_point", "ET", 44.3600, 5.6500, None),
    ("Sebba Nutrition Site", "delivery_point", "BF", 0.5150, 13.4370, None),
]

APPROPRIATIONS = [
    (
        "US Government",
        "FY2026 Emergency Food Security — Horn of Africa & Sahel",
        "FY2026",
        48_000_000,
        "US-GOV-1-OES-FY2026-001",
    ),
    (
        "US Government",
        "FY2026 Famine Prevention Reserve",
        "FY2026",
        22_500_000,
        "US-GOV-1-OES-FY2026-002",
    ),
]

# contract_ref, org, lot description to match, quantity, unit price
# One per corridor, so a shipment always belongs to a contract for its own
# country and supplier.
CONTRACTS = [
    ("OES-C-2026-ET1", "Rift Valley Therapeutics PLC", "48,000 cartons RUTF delivered to Gode", 48000, 41.80),
    ("OES-C-2026-NG1", "Savanna Nutrients Ltd", "45,000 cartons RUTF delivered to Maiduguri", 45000, 42.10),
    ("OES-C-2026-BF1", "Faso NutriWorks SA", "20,000 cartons RUTF delivered to Djibo", 20000, 43.60),
    ("OES-C-2026-SD1", "Blue Nile Freight Co", "Inland haulage of 40,000 cartons from Port Sudan", 40000, 3.20),
]

# Which contract each shipment belongs to, by reference prefix.
CONTRACT_BY_PREFIX = {
    "SHP-2026-01": "OES-C-2026-ET1",
    "SHP-2026-02": "OES-C-2026-SD1",
    "SHP-2026-03": "OES-C-2026-NG1",
    "SHP-2026-04": "OES-C-2026-BF1",
}

# reference, contract, origin, destination, waypoints, cartons, state, tier, days ago departed
SHIPMENTS = [
    # Ethiopia corridor: clean EPCIS from the plant, delivered and confirmed
    ("SHP-2026-0101", "Addis Ababa RUTF Plant", "Addis Central Depot", [], 16000, "confirmed", "epcis", 34),
    (
        "SHP-2026-0102",
        "Addis Central Depot",
        "Gode Distribution Hub",
        ["Dire Dawa Transit Store"],
        12000,
        "confirmed",
        "epcis",
        26,
    ),
    ("SHP-2026-0103", "Addis Central Depot", "Jijiga Distribution Hub", [], 8000, "delivered", "epcis", 12),
    ("SHP-2026-0104", "Addis Ababa RUTF Plant", "Addis Central Depot", [], 12000, "in_transit", "asn", 3),
    # Sudan corridor: imported through Port Sudan, tracked by check-ins only
    ("SHP-2026-0201", "Port Sudan", "Khartoum Central Warehouse", [], 14000, "confirmed", "checkin", 30),
    (
        "SHP-2026-0202",
        "Khartoum Central Warehouse",
        "El Fasher Distribution Hub",
        ["Kassala Forward Store"],
        9000,
        "in_transit",
        "checkin",
        9,
    ),
    (
        "SHP-2026-0203",
        "Port Sudan",
        "Nyala Distribution Hub",
        ["Khartoum Central Warehouse"],
        11000,
        "in_transit",
        "checkin",
        5,
    ),
    # Nigeria: a short, well-instrumented corridor
    ("SHP-2026-0301", "Kano RUTF Plant", "Kano Central Warehouse", [], 20000, "confirmed", "epcis", 21),
    ("SHP-2026-0302", "Kano Central Warehouse", "Maiduguri Distribution Hub", [], 15000, "delivered", "asn", 8),
    ("SHP-2026-0303", "Kano Central Warehouse", "Damaturu Distribution Hub", [], 10000, "in_transit", "asn", 2),
    ("SHP-2026-0304", "Maiduguri Distribution Hub", "Bama Health Post", [], 3000, "planned", "portal", None),
    # Burkina Faso: plant to the Sahel, one hand-keyed leg
    ("SHP-2026-0401", "Ouagadougou RUTF Plant", "Ouagadougou Central Warehouse", [], 9000, "confirmed", "asn", 24),
    ("SHP-2026-0402", "Ouagadougou Central Warehouse", "Djibo Distribution Hub", [], 6000, "delivered", "portal", 11),
    ("SHP-2026-0403", "Ouagadougou Central Warehouse", "Dori Distribution Hub", [], 5000, "in_transit", "portal", 4),
]

STATUS_STEPS = {
    "planned": [],
    "in_transit": ["departing"],
    "delivered": ["departing", "arriving", "receiving"],
    "confirmed": ["departing", "arriving", "receiving"],
}
