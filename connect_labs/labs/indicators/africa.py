"""The African country set, and where it comes from.

Kept as an explicit list rather than derived from whatever happens to be loaded,
so "all of Africa" means the same thing every run and a country missing from the
map is visibly a gap rather than silently absent.

54 UN member states plus Western Sahara. Island states are included: they are
small, but excluding them would be a choice nobody made deliberately.
"""

from __future__ import annotations

AFRICA: dict[str, str] = {
    "DZA": "Algeria",
    "AGO": "Angola",
    "BEN": "Benin",
    "BWA": "Botswana",
    "BFA": "Burkina Faso",
    "BDI": "Burundi",
    "CPV": "Cabo Verde",
    "CMR": "Cameroon",
    "CAF": "Central African Republic",
    "TCD": "Chad",
    "COM": "Comoros",
    "COG": "Congo",
    "COD": "Democratic Republic of the Congo",
    "DJI": "Djibouti",
    "EGY": "Egypt",
    "GNQ": "Equatorial Guinea",
    "ERI": "Eritrea",
    "SWZ": "Eswatini",
    "ETH": "Ethiopia",
    "GAB": "Gabon",
    "GMB": "Gambia",
    "GHA": "Ghana",
    "GIN": "Guinea",
    "GNB": "Guinea-Bissau",
    "CIV": "Côte d'Ivoire",
    "KEN": "Kenya",
    "LSO": "Lesotho",
    "LBR": "Liberia",
    "LBY": "Libya",
    "MDG": "Madagascar",
    "MWI": "Malawi",
    "MLI": "Mali",
    "MRT": "Mauritania",
    "MUS": "Mauritius",
    "MAR": "Morocco",
    "MOZ": "Mozambique",
    "NAM": "Namibia",
    "NER": "Niger",
    "NGA": "Nigeria",
    "RWA": "Rwanda",
    "STP": "Sao Tome and Principe",
    "SEN": "Senegal",
    "SYC": "Seychelles",
    "SLE": "Sierra Leone",
    "SOM": "Somalia",
    "ZAF": "South Africa",
    "SSD": "South Sudan",
    "SDN": "Sudan",
    "TZA": "Tanzania",
    "TGO": "Togo",
    "TUN": "Tunisia",
    "UGA": "Uganda",
    "ESH": "Western Sahara",
    "ZMB": "Zambia",
    "ZWE": "Zimbabwe",
}

ISO_CODES = sorted(AFRICA)

#: UN M49 geographic subregion of every country above, as the code UNICEF's
#: SDMX warehouse uses for that subregion's aggregate. Stated rather than
#: fetched for the same reason as the country list: M49 is a published
#: standard that changes rarely, and a lookup that silently lost a country
#: would drop it from every regional fallback without saying so.
#:
#: Used where a country publishes no national estimate of its own and the
#: regional aggregate is the best available figure -- see
#: sources/unicef_lbw.py. M49 subregions rather than UNICEF's reporting
#: regions because they are finer (five African subregions against two
#: UNICEF regions that split the continent roughly in half) and because every
#: country belongs to exactly one.
M49_SUBREGION: dict[str, str] = {
    **dict.fromkeys(("DZA", "EGY", "LBY", "MAR", "SDN", "TUN", "ESH"), "UNSDG_NORTHAFR"),
    **dict.fromkeys(
        (
            "BDI",
            "COM",
            "DJI",
            "ERI",
            "ETH",
            "KEN",
            "MDG",
            "MWI",
            "MUS",
            "MOZ",
            "RWA",
            "SYC",
            "SOM",
            "SSD",
            "TZA",
            "UGA",
            "ZMB",
            "ZWE",
        ),
        "UNSDG_EASTERNAFR",
    ),
    **dict.fromkeys(("AGO", "CMR", "CAF", "TCD", "COG", "COD", "GNQ", "GAB", "STP"), "UNSDG_MIDDLEAFR"),
    **dict.fromkeys(("BWA", "SWZ", "LSO", "NAM", "ZAF"), "UNSDG_SOUTHERNAFR"),
    **dict.fromkeys(
        (
            "BEN",
            "BFA",
            "CPV",
            "CIV",
            "GMB",
            "GHA",
            "GIN",
            "GNB",
            "LBR",
            "MLI",
            "MRT",
            "NER",
            "NGA",
            "SEN",
            "SLE",
            "TGO",
        ),
        "UNSDG_WESTERNAFR",
    ),
}


def name_for(iso_code: str) -> str:
    return AFRICA.get(iso_code.upper(), iso_code.upper())


def is_african(iso_code: str) -> bool:
    return iso_code.upper() in AFRICA
