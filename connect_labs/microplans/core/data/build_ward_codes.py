"""One-off script to (re)generate ``ward_codes.json`` from a GRID3 Nigeria
operational-wards GeoJSON export. NOT run automatically — re-run by hand if
GRID3 publishes an updated wards file, then commit the regenerated JSON.

    python connect_labs/microplans/core/data/build_ward_codes.py <path-to-grid3-wards.geojson>

Identity is (state, lga, ward) — NOT ward name alone. A ward's name is not
unique nationally: "Doka" alone names 5 different real wards across 4 states
(Katsina, Nasarawa, Kaduna x2, Kano), and 237 ward names nationally repeat
across different LGAs (e.g. "Sabon Gari" x14). Every (state, lga, ward) row
in the source file is treated as its own entity needing its own code, even
when its ward name is identical to another entity's.

Disambiguation, applied to every (state, lga, ward) row in alphabetical order
(so which entity keeps the short code is stable/reproducible across a
regeneration run, not dependent on the source file's row order):

  1. The ward's natural 3-letter prefix (first 3 alnum letters, uppercased),
     if not already claimed by an earlier (alphabetically-sorted) row.
  2. That prefix + the first 2 letters of the ward's LGA, if the natural
     prefix collides — more meaningful than an arbitrary extra letter, since
     it tells you WHY two wards differ, not just THAT they do.
  3. Growing the ward's own name one letter at a time, if the LGA-extended
     candidate is unavailable (no LGA) or ALSO already claimed.
  4. A numeric suffix, once the ward's own full name is exhausted and it
     STILL collides — the final, guaranteed-unique fallback.

This mirrors connect_labs.microplans.core.grouping._disambiguated_ward_prefixes
exactly, just run once, offline, with GLOBAL (nationwide) knowledge instead of
being scoped to whatever wards happen to appear in one grouping call — see
that function's docstring for why a single grouping call's own scope isn't
enough (multiple SEPARATE plans/grouping calls, each covering a different
ward, have no visibility into each other's chosen labels).

Real-world caveat (confirmed by hand): a work area's own ``ward``/``lga``
recorded in Connect data doesn't always match GRID3's canonical spelling
exactly — e.g. "Galinja (non-RCT)" (a local annotation) has no GRID3 match at
all. The grouping pipeline's lookup normalizes lightly (trim, casefold) but
does NOT attempt fuzzy/alt-name matching — a table miss falls back to the
runtime disambiguation, which still guarantees no collision within that one
plan even without table coverage. Deliberately kept simple: multi-ward
collisions are rare in practice (plans rarely span more than ~20 wards) and
the fallback already covers that case well.
"""

import json
import sys


def letters_of(s):
    return "".join(ch for ch in (s or "") if ch.isalnum()).upper()


def build(wards: list[dict]) -> list[dict]:
    claimed: dict[str, tuple] = {}  # code -> (state, lga, ward)
    rows = []
    for w in sorted(wards, key=lambda w: (w["state"], w["lga"], w["ward"])):
        state, lga, ward = w["state"], w["lga"], w["ward"]
        ward_letters = letters_of(ward)
        if not ward_letters:
            continue
        code = ward_letters[:3]
        if code in claimed:
            lga_letters = letters_of(lga)
            lga_candidate = code + lga_letters[:2] if lga_letters else None
            if lga_candidate and lga_candidate not in claimed:
                code = lga_candidate
            else:
                length = 4
                code = ward_letters[:length]
                while code in claimed:
                    length += 1
                    if length > len(ward_letters):
                        suffix = 2
                        candidate = f"{ward_letters}{suffix}"
                        while candidate in claimed:
                            suffix += 1
                            candidate = f"{ward_letters}{suffix}"
                        code = candidate
                        break
                    code = ward_letters[:length]
        claimed[code] = (state, lga, ward)
        rows.append({"state": state, "statecode": w.get("statecode", ""), "lga": lga, "ward": ward, "code": code})
    assert len({r["code"] for r in rows}) == len(rows), "collision in generated table — this should be impossible"
    return rows


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    with open(sys.argv[1]) as f:
        geojson = json.load(f)
    rows = build([feat["properties"] for feat in geojson["features"]])
    out_path = __file__.rsplit("/", 1)[0] + "/ward_codes.json"
    with open(out_path, "w") as f:
        json.dump(rows, f, indent=0, separators=(",", ":"))
    print(f"{len(rows)} wards -> {out_path}")
