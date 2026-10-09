"""Invented names for a synthetic case's mother and baby, written at the app's own name
questions -- so a picture or a case list can call a case "Baby Amina · mother Hauwa
Musa" rather than "Beneficiary 694".

A clone never copies a real name: identifier paths are refused by the profiler
(``profiler._is_identifier_path``), so a mirror clone carries no names at all. These
are drawn instead, from small pools of names common where the programme works
(``LOCALES``), DETERMINISTICALLY from the case's id: regenerating an opportunity gives
every case the same names, and a case's names are the same on every visit.

It is a replay-time CHOICE, like ``image_config``: the cohort spec says which
locale each source opportunity's names come from and which questions hold them
(``cohorts/kmc.yaml`` ``person_names``), and nothing is baked into a bundle.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Name pools per locale: mothers' given names, family names (in northern Nigeria a
#: woman is usually known by her own name and her father's or husband's), and babies'
#: given names by sex. Common names, invented pairings; no real person.
LOCALES: dict[str, dict[str, list[str]]] = {
    # Northern Nigeria (Kano): Hausa / Hausa-Fulani names.
    "ng_hausa": {
        "mother": "Hauwa Aisha Fatima Zainab Maryam Hadiza Halima Amina Rukayya Safiya Khadija Bilkisu "
        "Hafsat Jamila Asma'u Habiba Sadiya Rahama Ummi Firdausi Nafisa Saratu Binta Ladidi".split(),
        "family": "Musa Abdullahi Ibrahim Sani Bello Usman Garba Yusuf Lawal Aliyu Suleiman Danjuma "
        "Abubakar Haruna Idris Shehu Mahmud Kabiru Auwal Nasiru Bashir Sule Tijjani Umar".split(),
        "boy": "Abubakar Muhammad Aliyu Usman Umar Ibrahim Yusuf Abdullahi Sadiq Ahmad Bashir Nura "
        "Hamza Isa Musa Kabir".split(),
        "girl": "Amina Fatima Aisha Zainab Maryam Hauwa Khadija Ruqayya Safiya Hafsat Habiba Sumayya "
        "Halima Nana Asma'u Hindatu".split(),
    },
    # Nigeria, elsewhere: Yoruba, Igbo and Hausa names together.
    "ng": {
        "mother": "Adaeze Ngozi Chiamaka Funmilayo Bisola Temitope Aisha Zainab Ifeoma Blessing Yetunde "
        "Kemi Amaka Hauwa Folake Chioma".split(),
        "family": "Okafor Adeyemi Eze Okonkwo Balogun Ibrahim Nwosu Adebayo Musa Obi Oladipo Chukwu "
        "Bello Afolabi Nnamdi Ogunleye".split(),
        "boy": "Chinedu Tunde Emeka Ayo Ibrahim Kelechi Femi Obinna Musa Segun Chidi Dayo".split(),
        "girl": "Ada Ngozi Tolu Amaka Zainab Ife Kemi Chioma Aisha Bimpe Nneka Funke".split(),
    },
    # Uganda: Luganda and other Ugandan names.
    "ug": {
        "mother": "Nakato Nansubuga Namukasa Achieng Atim Nabirye Kyomuhendo Nantongo Nalubega Akello "
        "Namatovu Babirye Nakanwagi Auma Kemigisa Nyakato".split(),
        "family": "Ssempala Mugisha Okello Kato Musoke Ochieng Byaruhanga Ssekandi Tumusiime Opio "
        "Wasswa Kiggundu Mukasa Atwine Lubega Ouma".split(),
        "boy": "Kato Wasswa Mugisha Ssenyonga Okello Tumusiime Isaac Brian Joel Ivan Opio Mukisa".split(),
        "girl": "Nakato Babirye Nankya Namata Atim Akello Patience Faith Shamim Mirembe Nabukenya Joy".split(),
    },
    # Kenya: Kikuyu, Luo, Luhya and Kalenjin names.
    "ke": {
        "mother": "Wanjiru Achieng Njeri Atieno Wambui Chebet Nyambura Akinyi Wairimu Jepkosgei Muthoni "
        "Adhiambo Nekesa Kerubo Wanjiku Moraa".split(),
        "family": "Kamau Otieno Mwangi Odhiambo Kiprono Njoroge Wafula Ochieng Kariuki Cheruiyot Onyango "
        "Mutua Wekesa Kibet Omondi Gitau".split(),
        "boy": "Kamau Otieno Kiprotich Baraka Juma Omondi Mwangi Kibet Brian Kevin Ouma Wafula".split(),
        "girl": "Wanjiru Akinyi Chebet Neema Atieno Njeri Imani Wambui Faith Zawadi Achieng Mercy".split(),
    },
}


class PersonNamesConfig(BaseModel):
    """Where a clone's mother and baby names go, and which pool they come from."""

    model_config = ConfigDict(extra="forbid")

    locale: str
    #: The app's name questions (dotted form paths); every one listed is written.
    mother_paths: list[str] = Field(default_factory=list)
    child_paths: list[str] = Field(default_factory=list)
    #: Where the visit records the baby's sex, so a boy gets a boy's name. The first
    #: path that holds "male" / "female" on any of the case's visits decides.
    sex_paths: list[str] = Field(default_factory=list)

    @field_validator("locale")
    @classmethod
    def _known_locale(cls, v: str) -> str:
        if v not in LOCALES:
            raise ValueError(f"person_names.locale: {v!r} is not one of {', '.join(LOCALES)}")
        return v


def _pick(pool: list[str], key: str) -> str:
    digest = hashlib.sha256(key.encode()).digest()
    return pool[int.from_bytes(digest[:8], "big") % len(pool)]


def names_for(entity_id: str, locale: str, sex: str | None = None) -> tuple[str, str]:
    """``(baby's given name, mother's full name)`` for a case, the same every time."""
    pools = LOCALES[locale]
    s = str(sex or "").strip().lower()
    babies = (
        pools["girl"] if s.startswith("f") else pools["boy"] if s.startswith("m") else pools["boy"] + pools["girl"]
    )
    child = _pick(babies, f"child:{locale}:{entity_id}")
    given = _pick(pools["mother"], f"mother:{locale}:{entity_id}")
    family = _pick(pools["family"], f"family:{locale}:{entity_id}")
    mother = f"{given} {family}"
    return child, mother


def _get(form_json: dict, path: str) -> Any:
    node: Any = form_json
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _set(form_json: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    node = form_json
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def apply_person_names(visits: list[dict[str, Any]], config: PersonNamesConfig) -> int:
    """Write each case's names into every one of its visits' name questions. Returns
    the number of cases named."""
    sex: dict[str, str] = {}
    for v in visits:
        eid = str(v.get("entity_id") or "")
        if not eid or eid in sex:
            continue
        for path in config.sex_paths:
            found = _get(v.get("form_json") or {}, path)
            if isinstance(found, str) and found.strip().lower() in ("male", "female"):
                sex[eid] = found
                break
    named: set[str] = set()
    for v in visits:
        eid = str(v.get("entity_id") or "")
        form_json = v.get("form_json")
        if not eid or not isinstance(form_json, dict):
            continue
        child, mother = names_for(eid, config.locale, sex.get(eid))
        for path in config.child_paths:
            _set(form_json, path, child)
        for path in config.mother_paths:
            _set(form_json, path, mother)
        named.add(eid)
    return len(named)


def config_for_source(spec: dict | None, source_opportunity_id: int) -> PersonNamesConfig | None:
    """The cohort spec's ``person_names`` block for one source opportunity, or None
    (no block, or no locale for that source and no default)."""
    if not spec:
        return None
    by_source = {int(k): v for k, v in (spec.get("locale_by_source") or {}).items()}
    locale = by_source.get(int(source_opportunity_id)) or spec.get("default_locale")
    if not locale:
        return None
    paths = spec.get("paths") or {}
    return PersonNamesConfig(
        locale=locale,
        mother_paths=list(paths.get("mother") or []),
        child_paths=list(paths.get("child") or []),
        sex_paths=list(paths.get("sex") or []),
    )
