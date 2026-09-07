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

Sources are both already in this repository. Towns come from ``static/pulse/
towns.js`` -- GeoNames cities500, filtered to the countries Connect delivers in,
which pulse already ships for its "nearest town" labels. Regions and countries
come from ``labs.admin_boundaries``, the same PostGIS polygons ``geo.py`` uses.
Nothing here calls an external geocoder: partner addresses are not ours to send
to a third party, and a network dependency inside an import is a bad trade for
data that changes a few times a year.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from connect_labs.microplans.core import iso as iso_codes

TOWNS_JS = Path(__file__).parent.parent / "static" / "pulse" / "towns.js"

# Address words that are never a town. Without this "Center", "Office" and
# "Road" match real towns somewhere and scatter partners across the map.
_STOP = frozenset(
    """
    po box bp street road avenue ave rd st close crescent lane drive way plot
    house no number floor suite office building complex centre center estate
    district state province region county ward village town city area zone
    opposite behind near beside along junction roundabout market church mosque
    school hospital clinic secretariat headquarters hq main new old upper lower
    north south east west central federal republic democratic united
    """.split()
)

_WORD = re.compile(r"[A-Za-zÀ-ÿ']{3,}")


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped.lower()).strip()


@lru_cache(maxsize=1)
def _towns() -> dict[str, dict[str, tuple[float, float]]]:
    """{alpha2: {folded town name: (lat, lon)}} parsed from the shipped JS.

    Parsed rather than duplicated into Python: two copies of a gazetteer drift,
    and the JS file is the one pulse's own map labels already use.
    """
    if not TOWNS_JS.exists():  # pragma: no cover - the file ships with the app
        return {}
    out: dict[str, dict[str, tuple[float, float]]] = {}
    pattern = re.compile(r"\['([^']+)',\s*'([A-Z]{2})',\s*(-?[\d.]+),\s*(-?[\d.]+)\]")
    for name, cc, lat, lon in pattern.findall(TOWNS_JS.read_text()):
        folded = _fold(name)
        if len(folded) < 4 or folded in _STOP:
            continue
        # First writer wins: cities500 is ordered by population, so the bigger
        # place keeps an ambiguous name rather than a hamlet stealing it.
        out.setdefault(cc, {}).setdefault(folded, (float(lat), float(lon)))
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
    }
    return aliases.get(folded)


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
    alpha2 = iso_codes.to_alpha2(iso3)
    table = _towns().get(alpha2 or "", {})
    if not table or not address:
        return None
    words = [w for w in (_fold(w) for w in _WORD.findall(address)) if w not in _STOP]
    # Two-word names first ("Port Harcourt"), then single words, so the more
    # specific place wins before a component of it does.
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            candidate = " ".join(words[i : i + size])
            hit = table.get(candidate)
            if hit:
                return hit[0], hit[1], candidate.title()
    return None


def resolve(countries: str, regions: str, address: str) -> HqLocation | None:
    """Finest location the row supports, or None when even the country is absent."""
    raw = (countries or "").replace('"', "").strip()
    # Whole string first. Several ISO names contain a comma -- "Congo, the
    # Democratic Republic of the" -- and splitting on it leaves "Congo", which
    # resolves to the OTHER Congo. Only a name that fails whole gets split, for
    # the cells that really do list several countries.
    iso3 = country_to_iso3(raw)
    if not iso3:
        for part in raw.split(","):
            iso3 = country_to_iso3(part.strip())
            if iso3:
                break
    if not iso3:
        return None

    city = _city_point(iso3, address)
    if city:
        return HqLocation(city[0], city[1], "city", city[2], iso3)

    region = _region_point(iso3, regions)
    if region:
        return HqLocation(region[0], region[1], "region", region[2], iso3)

    country = _boundary_point(iso3, 0)
    if country:
        return HqLocation(country[0], country[1], "country", country[2], iso3)
    return None
