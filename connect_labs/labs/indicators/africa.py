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


def name_for(iso_code: str) -> str:
    return AFRICA.get(iso_code.upper(), iso_code.upper())


def is_african(iso_code: str) -> bool:
    return iso_code.upper() in AFRICA
