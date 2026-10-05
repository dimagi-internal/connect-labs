"""Best-effort coordinates for a partner's head office.

The directory records where a partner is three different ways, none of them
coordinates: a country from a dropdown (clean), regions of operation (free text,
two thirds empty), and an office address (free text, and frequently not an
address at all -- "Nothing", an email, a PO box with no town).

So this resolves to the finest thing each row can actually support and *says
which*, because the alternative is a map that draws a rooftop pin from the word
"Nigeria". Precision travels with the point and the map renders it differently:

  city     a town in the address matched the gazetteer         exact enough to pin
  region   a region of operation matched an ADM1 boundary      a district, not a desk
  country  only the country is known                           a country, drawn as one

Sources are both in this repository. Towns come from ``pulse/data/hq_towns.tsv.gz``
-- GeoNames cities500 for every African country plus the others the directory
names (``tools/build_hq_towns.py``). It is deliberately NOT ``static/pulse/
towns.js``: that file is cut to the countries Connect delivers in so the browser
can afford it, and reading it here left every partner in Zambia, Malawi or
Ethiopia -- and every office abroad -- at "country only" however good its
address was. Regions and countries come from ``labs.admin_boundaries``, the same
PostGIS polygons ``geo.py`` uses.
Nothing here calls an external geocoder: partner addresses are not ours to send
to a third party, and a network dependency inside an import is a bad trade for
data that changes a few times a year.
"""

from __future__ import annotations

import gzip
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from connect_labs.microplans.core import iso as iso_codes

HQ_TOWNS = Path(__file__).parent / "data" / "hq_towns.tsv.gz"

# Address words that are never a town. Without this "Center", "Office" and
# "Road" match real towns somewhere and scatter partners across the map.
_STOP = frozenset("""
    po box bp street road avenue ave rd st close crescent lane drive way plot
    house no number floor suite office building complex centre center estate
    district state province region county ward village town city area zone
    opposite behind near beside along junction roundabout market church mosque
    school hospital clinic secretariat headquarters hq main new old upper lower
    north south east west central federal republic democratic united
    """.split())

# Words that make the word BEFORE them a street, not a place: "Mumias Rd,
# Nairobi" is in Nairobi, and "Luwingu Road, Kasama" is in Kasama. Streets are
# named after towns everywhere, and without this the bigger town wins.
_STREET = frozenset("""
    street st road rd avenue ave close crescent lane drive way highway hwy
    boulevard blvd bypass bye pass link expressway
    """.split())

# Words that make the word before them an administrative area named after its
# seat. "Daru ... Kailahun District" is in Daru, not Kailahun town; but
# "Kaita Road, Katsina State" names no other place, and Katsina is the right
# answer there. So these demote a match rather than discard it.
_ADMIN = frozenset("district state province region county chiefdom division lga municipality".split())

# Below this a single-word match is more likely an English word that happens to
# name a hamlet somewhere ("Science", "Local") than the place an office is in.
# Multi-word names ("Dar es Salaam") are specific enough not to need it.
_MIN_SINGLE_WORD_POPULATION = 1000

# Districts of a city that addresses name instead of the city itself, and which
# cities500 does not carry as places of their own. Keyed by alpha2, folded.
# Add a row when the import reports an address that names one.
_DISTRICT_OF = {
    "CD": {
        name: "kinshasa"
        for name in (
            "gombe",
            "lingwala",
            "kintambo",
            "ngaliema",
            "limete",
            "kalamu",
            "bandalungwa",
            "barumbu",
            "kasa vubu",
            "lemba",
            "matete",
            "masina",
            "ndjili",
            "kimbanseke",
            "mont ngafula",
            "selembao",
            "bumbu",
            "makala",
            "ngiri ngiri",
            "ngaba",
            "kisenso",
            "maluku",
            "nsele",
        )
    },
    "UG": {name: "kampala" for name in ("kawempe", "makindye", "nakawa", "rubaga", "lubaga")},
}


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped.lower()).strip()


@dataclass(frozen=True)
class _Town:
    name: str
    lat: float
    lon: float
    population: int


@lru_cache(maxsize=1)
def _towns() -> dict[str, dict[str, _Town]]:
    """{alpha2: {name key: town}} from the generated gazetteer.

    Keyed by ``_name_key`` so "Mont-Ngafula" and "Mont Ngafula" are one place.
    The file is sorted by population descending, so the first writer is the
    bigger place and keeps an ambiguous name rather than a hamlet stealing it.
    """
    if not HQ_TOWNS.exists():  # pragma: no cover - the file ships with the app
        return {}
    out: dict[str, dict[str, _Town]] = {}
    with gzip.open(HQ_TOWNS, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            name, cc, lat, lon, population = line.rstrip("\n").split("\t")
            key = _name_key(name)
            if len(key) < 4 or key in _STOP:
                continue
            out.setdefault(cc, {}).setdefault(key, _Town(name, float(lat), float(lon), int(population)))
    return out


def _name_key(value: str) -> str:
    """Folded and stripped of punctuation, so "Congo, the Democratic Republic of
    the" and "Congo the Democratic Republic of the" are the same country."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", _fold(value))).strip()


def country_to_iso3(name: str) -> str | None:
    """The directory's country dropdown uses ISO 3166 names, so this mostly is a
    lookup -- but it carries the few spellings the sheet actually contains."""
    folded = _name_key(name)
    if not folded:
        return None
    for row in iso_codes.all_countries():
        if _name_key(row["name"]) == folded:
            return row["alpha3"]
    # The sheet's long-form names, and the shorthands people type instead.
    aliases = {
        "congo the democratic republic of the": "COD",
        "democratic republic of the congo": "COD",
        "dr congo": "COD",
        "drc": "COD",
        "tanzania united republic of": "TZA",
        "tanzania": "TZA",
        "cote d ivoire": "CIV",
        "ivory coast": "CIV",
        "iran": "IRN",
        "syria": "SYR",
        "bolivia": "BOL",
        "venezuela": "VEN",
        "united kingdom": "GBR",
        "south korea": "KOR",
        "laos": "LAO",
        "moldova": "MDA",
        "russia": "RUS",
        "vietnam": "VNM",
        # ISO renamed this one in 2022; the sheet still says the old name.
        "turkey": "TUR",
    }
    return aliases.get(folded)


# Countries the boundary tables do not carry. ``labs.admin_boundaries`` is
# loaded per-country as programs need it, so a partner in a country nobody has
# run a program in yet has no polygon to take a centroid from -- and the whole
# row then fails, even though we know perfectly well which country it is.
#
# A country centroid is stable reference data, not a measurement, so a literal
# is the honest form. Add a row when a partner turns up somewhere new; the
# import reports anything it could not place, so the gap announces itself.
_COUNTRY_FALLBACK = {
    "AFG": (33.94, 67.71),
    "IND": (22.35, 78.67),
    "HTI": (18.97, -72.29),
    "PHL": (12.88, 121.77),
    "IDN": (-2.55, 118.02),
    "BGD": (23.68, 90.36),
    "PAK": (30.38, 69.35),
    "IRN": (32.43, 53.69),
    "MYS": (4.21, 101.98),
    "EGY": (26.82, 30.80),
    "TUR": (38.96, 35.24),
    "UKR": (48.38, 31.17),
    "GBR": (54.00, -2.00),
}


@dataclass(frozen=True)
class HqLocation:
    lat: float
    lon: float
    precision: str  # city | region | country
    label: str
    iso3: str


def _boundary_point(iso3: str, level: int, name_match: str = "") -> tuple[float, float, str] | None:
    """Centroid of an admin unit, or of the country when no name is given."""
    from django.contrib.gis.db.models.functions import Centroid

    from connect_labs.labs.admin_boundaries.models import AdminBoundary

    qs = AdminBoundary.objects.filter(iso_code=iso3, admin_level=level)
    if name_match:
        qs = qs.filter(name__iexact=name_match)
    row = qs.annotate(mid=Centroid("geometry")).values_list("mid", "name").first()
    if not row:
        return None
    point, name = row
    return point.y, point.x, name


def _region_point(iso3: str, regions: str) -> tuple[float, float, str] | None:
    """Match any ADM1 name for this country against the free-text regions cell.

    Matched by containment rather than equality: the cell says things like
    "Borno, Yobe and Adamawa states", so the boundary name has to be looked for
    inside it rather than compared to it.
    """
    from django.contrib.gis.db.models.functions import Centroid

    from connect_labs.labs.admin_boundaries.models import AdminBoundary

    haystack = _fold(regions)
    if not haystack:
        return None
    rows = (
        AdminBoundary.objects.filter(iso_code=iso3, admin_level=1)
        .annotate(mid=Centroid("geometry"))
        .values_list("name", "mid")
    )
    best = None
    for name, point in rows:
        folded = _fold(name)
        if len(folded) >= 4 and re.search(rf"\b{re.escape(folded)}\b", haystack):
            # Longest name wins: "North East" should not beat "North East Region".
            if best is None or len(folded) > len(best[0]):
                best = (folded, point, name)
    if best is None:
        return None
    _, point, name = best
    return point.y, point.x, name


def _city_point(iso3: str, address: str) -> tuple[float, float, str] | None:
    """The town an address is in, or None.

    Every run of one to four words is a candidate, so "Dar es Salaam" is found
    whole -- an earlier version dropped words under three letters before
    matching and so looked for "dar salaam", which is nowhere. A candidate
    followed by a street word is a street and is skipped. When several real
    towns appear, the biggest wins: an address names its neighbourhood and its
    city, and the city is the head office's location ("Kawempe ..., Kampala").
    A name followed by "District" or "State" only wins when nothing else does.
    """
    alpha2 = iso_codes.to_alpha2(iso3) or ""
    table = _towns().get(alpha2, {})
    districts = _DISTRICT_OF.get(alpha2, {})
    if not table or not address:
        return None
    words = _name_key(address).split()
    best: tuple[tuple[bool, int, int], _Town] | None = None
    for size in (4, 3, 2, 1):
        for i in range(len(words) - size + 1):
            following = words[i + size] if i + size < len(words) else ""
            if following in _STREET:
                continue
            candidate = " ".join(words[i : i + size])
            if size == 1 and (len(candidate) < 4 or candidate in _STOP):
                continue
            # The district map wins: some communes (Masina) are places in their
            # own right in cities500, and the head office is still in the city.
            town = table.get(districts[candidate]) if candidate in districts else table.get(candidate)
            if town is None:
                continue
            if size == 1 and town.population < _MIN_SINGLE_WORD_POPULATION:
                continue
            # A named place beats an area named after one; then the biggest
            # town; among equals, the one later in the address, because
            # addresses run from the street out to the city.
            rank = (following not in _ADMIN, town.population, i)
            if best is None or rank > best[0]:
                best = (rank, town)
    if best is None:
        return None
    town = best[1]
    return town.lat, town.lon, town.name


def _countries(raw: str) -> list[str]:
    """Every ISO3 the countries cell names, in the order it names them."""
    raw = (raw or "").replace('"', "").strip()
    # Whole string first. Several ISO names contain a comma -- "Congo, the
    # Democratic Republic of the" -- and splitting on it leaves "Congo", which
    # resolves to the OTHER Congo. Only a name that fails whole gets split, for
    # the cells that really do list several countries.
    whole = country_to_iso3(raw)
    if whole:
        return [whole]
    found: list[str] = []
    for part in raw.split(","):
        iso3 = country_to_iso3(part.strip())
        if iso3 and iso3 not in found:
            found.append(iso3)
    return found


def resolve(countries: str, regions: str, address: str) -> HqLocation | None:
    """Finest location the row supports, or None when even the country is absent.

    The address is tried against every country the row lists, in order: an
    organisation that works in Sierra Leone but writes a London office address
    is located in London, not at the middle of Sierra Leone. The country it
    falls back to is still the first one listed.
    """
    isos = _countries(countries)
    if not isos:
        return None

    for iso3 in isos:
        city = _city_point(iso3, address)
        if city:
            return HqLocation(city[0], city[1], "city", city[2], iso3)

    iso3 = isos[0]
    region = _region_point(iso3, regions)
    if region:
        return HqLocation(region[0], region[1], "region", region[2], iso3)

    country = _boundary_point(iso3, 0)
    if country:
        return HqLocation(country[0], country[1], "country", country[2], iso3)

    fallback = _COUNTRY_FALLBACK.get(iso3)
    if fallback:
        return HqLocation(fallback[0], fallback[1], "country", iso_codes.country_name(iso3) or iso3, iso3)
    return None


def operating_areas(countries: list[str], regions: str) -> list[dict]:
    """Where an organisation says it WORKS, as points: one per named region.

    `resolve` answers a different question -- one point for the head office --
    and stops at the first thing that matches, so an organisation working
    across Borno, Yobe and Adamawa was drawn as a single dot in its office
    town. This walks every listed country and every ADM1 region of that
    country named in the regions cell. A country with no region named still
    gets a point, at the country, marked as such: "we work in Kenya" is a
    claim about all of Kenya, not about its middle.

    The regions cell is not tagged by country, so each country's own regions
    are looked for in all of it. ADM1 names are rarely shared across the
    countries one organisation lists; when they are, both are drawn.
    """
    from django.contrib.gis.db.models.functions import Centroid

    from connect_labs.labs.admin_boundaries.models import AdminBoundary

    haystack = _fold(regions)
    out: list[dict] = []
    seen_iso3: set[str] = set()
    for country in countries or []:
        iso3 = country_to_iso3(country)
        if not iso3 or iso3 in seen_iso3:
            continue
        seen_iso3.add(iso3)

        matched: dict[str, dict] = {}
        if haystack:
            # AdminBoundary holds several sources' copies of the same unit, so
            # one region is kept once, from whichever source is listed first.
            rows = (
                AdminBoundary.objects.filter(iso_code=iso3, admin_level=1)
                .order_by("source", "name")
                .annotate(mid=Centroid("geometry"))
                .values_list("name", "mid")
            )
            for name, point in rows:
                folded = _fold(name)
                if len(folded) < 4 or folded in matched:
                    continue
                if re.search(rf"\b{re.escape(folded)}\b", haystack):
                    matched[folded] = {
                        "lat": point.y,
                        "lon": point.x,
                        "precision": "region",
                        "label": name,
                        "iso3": iso3,
                    }
            # "North East" inside "North East Region" is one place, not two.
            for short in [k for k in matched if any(k != other and k in other for other in matched)]:
                matched.pop(short, None)

        if matched:
            out.extend(matched.values())
            continue
        centre = _boundary_point(iso3, 0)
        if not centre:
            fallback = _COUNTRY_FALLBACK.get(iso3)
            centre = (fallback[0], fallback[1], "") if fallback else None
        if centre:
            out.append(
                {
                    "lat": centre[0],
                    "lon": centre[1],
                    "precision": "country",
                    "label": iso_codes.country_name(iso3) or country,
                    "iso3": iso3,
                }
            )
    return out
