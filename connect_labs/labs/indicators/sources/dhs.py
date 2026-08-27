"""DHS Program loader — subnational mortality and fertility.

The DHS API is open, unauthenticated, and the only public source of *subnational*
under-5 mortality. Nigeria's 2024 survey reports 43 units at state level; across
84 countries there are ~4,200 subnational U5MR records.

Two honesty notes that surface in ``method``:

  * A DHS mortality rate is a **period estimate** covering roughly the ten years
    before fieldwork, not a reading of the survey year. We store ``SurveyYear``
    as ``year`` because that is what a user means by "the 2024 DHS", and say so.
  * Survey labels nest — ``North Central`` is a zone containing ``..Benue``. We
    match the leaves (real ADM1 units) and drop the aggregates, which would
    otherwise double-count.
"""

from __future__ import annotations

import logging

from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources.base import BoundaryMatcher, Row, http_json

logger = logging.getLogger(__name__)

API = "https://api.dhsprogram.com/rest/dhs"

#: DHS indicator id → our measure code. Value plus its published CI bounds.
INDICATORS = {
    "u5mr": {"value": "CM_ECMR_C_U5M", "lo": "CM_ECMR_C_U5L", "hi": "CM_ECMR_C_U5U"},
    "imr": {"value": "CM_ECMR_C_IMR", "lo": "CM_ECMR_C_IML", "hi": "CM_ECMR_C_IMU"},
    "tfr": {"value": "FE_FRTR_W_TFR", "lo": None, "hi": None},
    # Child-health indicators. DHS is the only subnational source for these —
    # IHME's 5 km diarrhoea and breastfeeding surfaces are licence-blocked and
    # stopped being updated in 2022 regardless.
    "diarrhoea_prevalence": {"value": "CH_DIAR_C_DIA", "lo": None, "hi": None},
    "ors_coverage": {"value": "CH_DIAT_C_ORS", "lo": None, "hi": None},
    "diarrhoea_untreated": {"value": "CH_DIAT_C_NON", "lo": None, "hi": None},
    "exclusive_breastfeeding": {"value": "CN_BFSS_C_EBF", "lo": None, "hi": None},
    # Nutrition
    "stunting": {"value": "CN_NUTS_C_HA2", "lo": None, "hi": None},
    "wasting": {"value": "CN_NUTS_C_WH2", "lo": None, "hi": None},
    "vitamin_a_coverage": {"value": "CN_MIAC_C_VAS", "lo": None, "hi": None},
    # Immunisation
    "measles_vaccination": {"value": "CH_VACC_C_MSL", "lo": None, "hi": None},
    "dpt3_vaccination": {"value": "CH_VACC_C_DP3", "lo": None, "hi": None},
    "full_immunisation": {"value": "CH_VACC_C_BAS", "lo": None, "hi": None},
    # Malaria. Prevalence carries published CI bounds, which most DHS
    # indicators do not — see the note on ci_low/ci_high in models.py.
    "malaria_prevalence": {
        "value": "ML_PMAL_C_RDT",
        "lo": "ML_PMAL_C_RDL",
        "hi": "ML_PMAL_C_RDU",
    },
    "malaria_treatment": {"value": "ML_FEVT_C_AML", "lo": None, "hi": None},
    # Malaria prevention
    "itn_use_children": {"value": "ML_NETC_C_ITN", "lo": None, "hi": None},
    # Respiratory infection
    "ari_prevalence": {"value": "CH_ARIS_C_ARI", "lo": None, "hi": None},
    "ari_antibiotics": {"value": "CH_ARIS_C_ABI", "lo": None, "hi": None},
    "zinc_coverage": {"value": "CH_DIAT_C_ZNC", "lo": None, "hi": None},
    # Maternal care
    "skilled_birth_attendance": {"value": "RH_DELA_C_SKP", "lo": None, "hi": None},
    "anc4": {"value": "RH_ANCN_W_N4P", "lo": None, "hi": None},
    # Household environment — the background risk for diarrhoeal disease
    "improved_water": {"value": "WS_SRCE_P_IMP", "lo": None, "hi": None},
    "improved_sanitation": {"value": "WS_TLET_P_IMP", "lo": None, "hi": None},
}

METHOD = (
    "DHS {survey}. Direct estimate from birth histories; the published rate covers "
    "a multi-year period preceding fieldwork, not the survey year alone. Stored "
    "against the survey year."
)


#: The DHS survey-display page is keyed by an internal SurveyNum, not by the
#: SurveyId we carry, so the mapping has to be fetched.
SURVEY_PAGE = "https://dhsprogram.com/methodology/survey/survey-display-{num}.cfm"


def survey_index() -> dict[str, dict]:
    """SurveyId -> survey metadata, for building links and readable labels.

    "NG2024DHS" is meaningless to anyone who does not already know the scheme;
    this is what turns it into "Nigeria DHS 2024" pointing at the real survey
    page.
    """
    out: dict[str, dict] = {}
    page = 1
    while True:
        payload = http_json(
            f"{API}/surveys",
            {
                "f": "json",
                "perpage": "1000",
                "page": str(page),
                "returnFields": (
                    "SurveyId,SurveyNum,SurveyType,SurveyYear,CountryName,"
                    "FieldworkStart,FieldworkEnd,NumberofHouseholds"
                ),
            },
        )
        for r in payload.get("Data") or []:
            if r.get("SurveyId"):
                out[r["SurveyId"]] = r
        if page >= int(payload.get("TotalPages") or 1):
            break
        page += 1
    logger.info("DHS: indexed %d surveys for linking", len(out))
    return out


def african_countries() -> dict[str, str]:
    """DHS 2-letter code → ISO-3, for African countries only."""
    data = http_json(
        f"{API}/countries",
        {
            "returnFields": "DHS_CountryCode,ISO3_CountryCode,CountryName,RegionName",
            "f": "json",
            "perpage": "300",
        },
    )["Data"]
    return {
        c["DHS_CountryCode"]: c["ISO3_CountryCode"]
        for c in data
        if "Africa" in (c.get("RegionName") or "") and c.get("ISO3_CountryCode")
    }


def _fetch(indicator_ids: list[str], breakdown: str) -> list[dict]:
    ids = ",".join(i for i in indicator_ids if i)
    out: list[dict] = []
    page = 1
    while True:
        payload = http_json(
            f"{API}/data",
            {
                "indicatorIds": ids,
                "breakdown": breakdown,
                "f": "json",
                "perpage": "5000",
                "page": str(page),
                "returnFields": (
                    "DHS_CountryCode,CountryName,SurveyYear,SurveyId,"
                    "IndicatorId,CharacteristicLabel,CharacteristicCategory,Value"
                ),
            },
        )
        out.extend(payload.get("Data") or [])
        if page >= int(payload.get("TotalPages") or 1):
            break
        page += 1
    return out


def _latest_survey_per_country(records: list[dict]) -> dict[tuple[str, str], dict]:
    """Keep only the most recent survey for each country, then index by region.

    Not "the latest record per region" — that would mix vintages inside one
    country. Kenya's older surveys report the eight pre-2010 provinces while
    2022 reports 47 counties; keeping the latest of each label would carry both
    taxonomies forward and invite double-counting. Nigeria is worse: labels like
    "Northeast - 1990" survive from a 1990 survey.

    Taking one survey per country means every region in a country shares a
    vintage, which is also the only defensible basis for comparing them.
    """
    latest_year: dict[str, int] = {}
    for r in records:
        code = r["DHS_CountryCode"]
        year = int(r["SurveyYear"])
        if year > latest_year.get(code, 0):
            latest_year[code] = year

    best: dict[tuple[str, str], dict] = {}
    for r in records:
        code = r["DHS_CountryCode"]
        if int(r["SurveyYear"]) != latest_year[code]:
            continue
        best[(code, r["CharacteristicLabel"])] = r
    return best


def load(measure: str = "u5mr", iso_codes: list[str] | None = None) -> list[Row]:
    """Build rows for one measure at ADM1, latest survey per region."""
    spec = INDICATORS[measure]
    iso_by_dhs = african_countries()
    wanted_iso = {c.upper() for c in iso_codes} if iso_codes else None

    surveys = survey_index()

    logger.info("DHS: fetching %s (subnational)", measure)
    records = _fetch([spec["value"]], "subnational")
    lo_recs = _latest_survey_per_country(_only(_fetch([spec["lo"]], "subnational"), spec["lo"])) if spec["lo"] else {}
    hi_recs = _latest_survey_per_country(_only(_fetch([spec["hi"]], "subnational"), spec["hi"])) if spec["hi"] else {}

    latest = _latest_survey_per_country(_only(records, spec["value"]))

    by_country: dict[str, list[tuple[str, dict]]] = {}
    for (dhs_code, label), rec in latest.items():
        iso = iso_by_dhs.get(dhs_code)
        if not iso or (wanted_iso and iso not in wanted_iso):
            continue
        by_country.setdefault(iso, []).append((label, rec))

    rows: list[Row] = []
    for iso, items in sorted(by_country.items()):
        matcher = BoundaryMatcher(iso, admin_level=1)
        if not len(matcher):
            logger.info("DHS: %s has no ADM1 boundaries loaded, skipping", iso)
            continue

        matched = 0
        for label, rec in items:
            boundary = matcher.match(label)
            if boundary is None:
                continue  # zone aggregates and unmatched labels are dropped, not guessed
            value = rec.get("Value")
            if value in (None, ""):
                continue
            key = (rec["DHS_CountryCode"], label)
            survey_id = rec.get("SurveyId") or f"{iso}{rec['SurveyYear']}DHS"
            meta = surveys.get(survey_id, {})
            rows.append(
                Row(
                    indicator=measure,
                    boundary=boundary,
                    year=int(rec["SurveyYear"]),
                    value=float(value),
                    ci_low=_val(lo_recs.get(key)),
                    ci_high=_val(hi_recs.get(key)),
                    source=Source.DHS,
                    source_ref=_readable(meta, survey_id, rec),
                    source_url=(SURVEY_PAGE.format(num=meta["SurveyNum"]) if meta.get("SurveyNum") else ""),
                    license_code=License.OPEN_API,
                    method=METHOD.format(survey=survey_id),
                    extra={
                        "dhs_label": label,
                        "category": rec.get("CharacteristicCategory"),
                        "survey_id": survey_id,
                        "fieldwork": f"{meta.get('FieldworkStart', '')} to {meta.get('FieldworkEnd', '')}".strip(
                            " to"
                        ),
                        "households": meta.get("NumberofHouseholds"),
                    },
                )
            )
            matched += 1

        logger.info("DHS %s %s: %d/%d region labels matched to boundaries", iso, measure, matched, len(items))
        if matcher.misses:
            logger.debug("DHS %s unmatched labels: %s", iso, matcher.misses[:10])

    return rows


def _readable(meta: dict, survey_id: str, rec: dict) -> str:
    """ "Nigeria DHS 2024" rather than "NG2024DHS"."""
    country = meta.get("CountryName") or rec.get("CountryName")
    year = meta.get("SurveyYear") or rec.get("SurveyYear")
    kind = meta.get("SurveyType") or "DHS"
    if country and year:
        return f"{country} {kind} {year}"
    return survey_id


def _only(records: list[dict], indicator_id: str) -> list[dict]:
    return [r for r in records if r.get("IndicatorId") == indicator_id]


def _val(rec: dict | None) -> float | None:
    if not rec:
        return None
    v = rec.get("Value")
    return float(v) if v not in (None, "") else None
