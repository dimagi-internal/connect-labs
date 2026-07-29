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

# A PRIOR split tender: two lots on two corridors, awarded to two different
# suppliers on purpose. Concentrating a four-country response on the cheapest
# single bidder is the classic humanitarian supply failure — when that plant or
# that corridor goes down, every district goes down at the same moment — and
# seeding a previous split shows the practice is routine rather than staged for
# the demo.
#
# It must be a DIFFERENT corridor pair from the live tender. It used to carry
# the live tender's two lots verbatim — same places, same quantities, same
# split outcome — sitting one row above it in the solicitations list and
# marked 2/2 Awarded. Three scenes build to that award
# being made on camera, and the answer was already on screen behind them the
# whole time. A prior split is worth seeding, because it shows the practice is
# routine rather than staged for the demo; it just must not be THIS split.
SPLIT_AWARD_RFP = "RUTF Horn and Sahel Corridors Q1 2026"

# lot description, category, quantity, delivery country, delivery place, winner
SPLIT_AWARD_LOTS = [
    (
        "30,000 cartons RUTF delivered to Gode",
        "rutf",
        30000,
        "ET",
        "Gode",
        "Rift Valley Therapeutics PLC",
    ),
    (
        "12,000 cartons RUTF delivered to Dori",
        "rutf",
        12000,
        "BF",
        "Dori",
        "Faso NutriWorks SA",
    ),
]

# Additional fully-awarded solicitations, one per corridor, so post-award
# execution has a contract per country rather than everything hanging off one.
# Each corridor ran its own tender on its own clock, so the closing and
# delivery dates are per-corridor rather than one shared offset — three rows
# that differ only by the place name read as a copied fixture, not a history.
# (winner org, RFP title, brief, country, bid closed N days ago,
#  delivery due in N days, lot description, cartons, unit price)
CORRIDOR_AWARDS = [
    (
        "Savanna Nutrients Ltd",
        "RUTF Northeast Nigeria Q2 2026",
        "Supply of RUTF to the north-east Nigeria response for the Q2 caseload.",
        "NG",
        71,
        104,
        "45,000 cartons RUTF delivered to Maiduguri",
        45000,
        42.10,
    ),
    (
        "Faso NutriWorks SA",
        "RUTF Sahel Q2 2026",
        "Supply of RUTF to the Burkina Faso Sahel region for the Q2 caseload.",
        "BF",
        48,
        67,
        "20,000 cartons RUTF delivered to Djibo",
        20000,
        43.60,
    ),
    (
        "Blue Nile Freight Co",
        "Sudan Corridor Logistics 2026",
        "Inland haulage and warehousing for imported RUTF through Port Sudan.",
        "SD",
        93,
        135,
        # Names its destination, not just its origin. A haulage contract whose
        # only stated place is where the cartons are collected has no delivery
        # point to measure against, and the contract measure counts arrivals at
        # the place the contract names (see Contract._quantity_in_contract_unit).
        "Inland haulage of 40,000 cartons from Port Sudan to Khartoum",
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
    ("Gombe Distribution Hub", "distribution_hub", "NG", 11.1673, 10.2897, None),
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

# Komadugu Health Initiative's own feeding sites — the implementing partner's
# eleven sites across Borno. They are ordinary delivery_point nodes owned by a
# partner org, not a parallel structure: a site is a site whoever runs it.
# name, country, lon, lat, catchment weight.
#
# The weight is each site's share of Borno's caseload, and it is deliberately
# uneven: Monguno and Ngala host large displaced populations and admit several
# times what a rural post like Askira does. An even split renders every row of
# the distribution calendar with identical figures — 214 children, 214 cartons,
# eleven times down the page — which is the surest sign a demo world was
# generated rather than observed.
PARTNER_SITES = [
    ("Monguno Nutrition Centre", "NG", 13.6100, 12.6750, 3.4),
    ("Ngala Nutrition Centre", "NG", 14.1890, 12.3540, 2.6),
    ("Dikwa Nutrition Centre", "NG", 13.9170, 12.0400, 1.9),
    ("Gwoza Nutrition Centre", "NG", 13.6940, 11.0850, 1.6),
    ("Kukawa Nutrition Centre", "NG", 13.5500, 12.9200, 1.5),
    ("Damboa Nutrition Centre", "NG", 12.7550, 11.1550, 1.2),
    ("Konduga Nutrition Centre", "NG", 13.4180, 11.6540, 1.0),
    ("Mafa Nutrition Centre", "NG", 13.6000, 11.9230, 0.9),
    ("Magumeri Nutrition Centre", "NG", 12.8320, 12.0910, 0.7),
    ("Biu Nutrition Centre", "NG", 12.1950, 10.6120, 0.6),
    ("Askira Nutrition Centre", "NG", 13.3300, 10.4500, 0.4),
]

# The implementing partner. Not a supplier — they never bid; they receive at
# their own sites, report what arrived, and treat the children.
PARTNER_ORG = (
    "Komadugu Health Initiative",
    "NG",
    "Maiduguri",
    "Zara Bukar",
    "zara@komadugu.example",
)

# adm1_code -> (name, country, IPC phase, under-5 population)
#
# Under-5 populations are SYNTHETIC, sized to be plausible for each district
# against its real population. The SAM caseload is derived from them rather
# than typed, so the method is one line of code instead of a spreadsheet
# nobody can find:
#
#     monthly SAM caseload
#       = under-5 population
#       x SAM prevalence for the district's IPC phase
#       x 2.6   (the standard incidence correction factor, converting a
#                point prevalence into an annual burden)
#       / 12
#
# Every seeded CaseloadEstimate carries that sentence in its own source_note,
# because a caseload figure with no stated method is the easiest number in a
# funding meeting to wave away.
DISTRICTS = {
    "NGA-2839": ("Borno", "NG", 5, 1_113_000),
    "NGA-2873": ("Yobe", "NG", 4, 625_000),
    # The third north-east district, and the one the coverage scene turns on.
    # Hauwa's view is scoped to Nigeria on the server, so the well-covered
    # district she is compared against has to BE in Nigeria — Kassala is in
    # Sudan and never appears on her page. Gombe is the smallest of the
    # north-east states and sits a phase below Borno, which is what lets it
    # take fewer cartons and still cover nearly all of its need. Yobe cannot
    # play the part: 91% of its caseload is more courses than Borno received.
    "NGA-2859": ("Gombe", "NG", 3, 600_000),
    "SDN-881": ("North Darfur", "SD", 5, 338_000),
    "SDN-5856": ("Southern Darfur", "SD", 2, 654_000),
    "SDN-884": ("Kassala", "SD", 3, 405_000),
    "ETH-3134": ("Somali", "ET", 4, 975_000),
    "BFA-2806": ("Soum", "BF", 5, 95_000),
    "BFA-2876": ("Séno", "BF", 2, 76_000),
    "BFA-2877": ("Yagha", "BF", 4, 29_000),
}

# Which district each node is answerable for. A node absent from this map
# serves no caseload — a port or a national warehouse sits on the route without
# being answerable for children, and a cover figure there would be meaningless.
NODE_DISTRICTS = {
    "Maiduguri Distribution Hub": "NGA-2839",
    "Bama Health Post": "NGA-2839",
    "Monguno Health Post": "NGA-2839",
    "Damaturu Distribution Hub": "NGA-2873",
    "Gombe Distribution Hub": "NGA-2859",
    "El Fasher Distribution Hub": "SDN-881",
    "Tawila Nutrition Site": "SDN-881",
    "Kebkabiya Nutrition Site": "SDN-881",
    "Nyala Distribution Hub": "SDN-5856",
    "Kassala Forward Store": "SDN-884",
    "Gode Distribution Hub": "ETH-3134",
    "Jijiga Distribution Hub": "ETH-3134",
    "Kelafo Nutrition Site": "ETH-3134",
    "Djibo Distribution Hub": "BFA-2806",
    "Dori Distribution Hub": "BFA-2876",
    "Sebba Nutrition Site": "BFA-2877",
    **{name: "NGA-2839" for name, _c, _lon, _lat, _w in PARTNER_SITES},
}

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
    (
        "OES-C-2026-SD1",
        "Blue Nile Freight Co",
        "Inland haulage of 40,000 cartons from Port Sudan to Khartoum",
        40000,
        3.20,
    ),
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
    # Kassala is the well-covered district the funder narrative contrasts
    # against Borno: a small caseload served to ~91% of need. It crosses a
    # district boundary, which is what makes it count as supply reaching a
    # district rather than redistribution inside one (see services/coverage.py).
    #
    # Borno's matching leg lands at Bama, the one Borno site that had never
    # received anything. Together with the 15,000 already across the boundary it
    # takes Borno to 34% of a caseload seven times Kassala's, on more than twice
    # the cartons — which is the contrast the funder narrative is built on.
    #
    # It only fits because the short-receipt consignment stopped banking its
    # cartons twice: the Nigeria contract had 325 cartons of headroom before
    # delivered_quantity would have exceeded what it contracted for, and now has
    # 2,617. The contract measure still counts a carton once per leg it travels
    # (review K1) — this leg fits under the ceiling rather than resolving it.
    ("SHP-2026-0204", "Khartoum Central Warehouse", "Kassala Forward Store", [], 6388, "delivered", "checkin", 16),
    ("SHP-2026-0305", "Kano Central Warehouse", "Bama Health Post", [], 1399, "delivered", "asn", 6),
    # Gombe: the small, well-covered district Hauwa's coverage table is read
    # against. 9,464 courses against a 10,400 caseload is 91%, on well under
    # the cartons Borno received — so tonnage ranks Borno first and coverage
    # ranks it second, which is the whole point of the scene. It lands in
    # Gombe rather than Maiduguri, so it does not count toward OES-C-2026-NG1's
    # contracted delivery: a carton counts once, where its contract says.
    ("SHP-2026-0306", "Kano Central Warehouse", "Gombe Distribution Hub", [], 9464, "delivered", "asn", 10),
]

# Consignments carrying a short-dated batch, by reference -> shelf life in days
# from the seed date.
#
# Every lot was seeded at 540 days, which put every expiry in January 2028 and
# meant the expiry-risk exception could not fire at all: the service, its cover
# calculation and its queue row were written, tested and unreachable, and the
# command centre narrated "all four exception kinds" over three.
#
# Djibo is the right home for it. It is the most over-supplied node in the
# network at 25 weeks of cover, which is exactly the situation this exception
# exists to catch — stock sitting where the demand behind it is too small to
# work through the batch before it expires. A short-dated lot at a node that
# turns its stock over quickly would never be at risk, and seeding one there
# would be the kind of detail that makes a demo world look authored.
SHORT_DATED_LOTS = {"SHP-2026-0402": 150}

# Days each leg is running behind its planned arrival. Authored rather than
# drawn, because the exception queue ranks on these: two consignments drawn
# independently landed on "2 days behind plan" together, and a queue whose top
# two rows are identical after the place name reads as a fixture rather than a
# morning's work. A leg absent from this table is on time.
SHIPMENT_SLIP_DAYS = {
    "SHP-2026-0302": 2,  # Kano → Maiduguri, the short well-instrumented leg
    "SHP-2026-0402": 6,  # Ouagadougou → Djibo, a border crossing and a bad road
    # Khartoum → El Fasher, the corridor that arrives as phone check-ins and
    # serves the worst famine phase in the response. Nine days is the figure the
    # command-centre narration speaks, and it is the leg the reallocation from
    # Kassala answers — one causal chain instead of three unrelated corridors.
    "SHP-2026-0202": 9,
    "SHP-2026-0203": 1,
}

STATUS_STEPS = {
    "planned": [],
    "in_transit": ["departing"],
    "delivered": ["departing", "arriving", "receiving"],
    "confirmed": ["departing", "arriving", "receiving"],
}
