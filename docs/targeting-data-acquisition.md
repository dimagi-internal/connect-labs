# Targeting data acquisition register

**Status:** live plan of record. Updated as items land.
**Opened:** 2026-09-05.

What this system can target is bounded by what it holds. This register lists
everything worth acquiring next, in the order it is worth acquiring, with the
coverage *measured* rather than assumed — every figure below came from querying
the source, not from its documentation.

## How the list was chosen

The system holds 122,885 values across 69 indicators and 2,350 boundaries.
Population, malaria, travel time and rural share are effectively complete —
99.6–100% of ADM1 and ADM2 units. The binding constraint is **DHS**, which
answers about 58% of ADM1 units at best, and much less for some measures.

Two different problems follow from that, and they need different sources:

* **Not enough *kinds* of question.** We load **25 of DHS's 4,655 indicators**.
  Entire intervention categories — family planning, zero-dose immunisation,
  anaemia — cannot be targeted at all, in countries where the data exists and
  we simply never asked for it. This is the cheap half, because the loader,
  re-levelling, gap derivation, annualisation, costing and UI already exist.
  Adding a measure is a registry entry and an indicator ID.

* **Not enough *places*.** 13 countries have no DHS at all — Egypt, Algeria,
  Somalia, South Sudan, Libya, Botswana, Guinea-Bissau, Equatorial Guinea,
  Mauritius, Djibouti, Comoros, Cabo Verde, Seychelles: **189M people, 13% of
  Africa**, for whom every child-health question currently returns nothing.
  This is the expensive half and needs a different survey programme.

The tiers below follow that split: widen the questions first because it is
nearly free, then widen the map.

---

## Tier 1 — DHS indicators we never asked for  ✅ LANDED 2026-09-05

**Cost:** registry entries. No new pipeline.
**Baseline for comparison:** `ors_coverage` (`CH_DIAT_C_ORS`) reaches 41 African
countries and 769 distinct regions. Anything near that is as good as what we
already rely on.

| # | Measure | DHS id | Countries | ≥2015 | Regions | Why it earns a place |
|---|---|---|---:|---:|---:|---|
| 1 | `zero_dose` | `CH_VACC_C_NON` | 41 | 29 | 789 | Children who received **no** vaccinations. The current global immunisation-equity target; we hold DPT3 and full immunisation but not the one funders actually allocate against. Its gap is a directly fundable count. |
| 2 | `fp_unmet_need` | `FP_NADM_W_UNT` | 41 | 29 | 785 | Family planning is an entire intervention category we cannot target **at all** today. Unmet need is its classic targeting quantity, and the denominator (`pop_f_15_49`) is already at 100% of ADM1. |
| 3 | `fp_modern_method` | `FP_CUSM_W_MOD` | 43 | 29 | 802 | mCPR. Widest African coverage of anything on this list. |
| 4 | `fp_demand_satisfied` | `FP_NADM_W_PDM` | 41 | 29 | 784 | The SDG 3.7.1 formulation; what a family-planning programme is actually judged on. |
| 5 | `itn_household` | `ML_NETP_H_ITN` | 35 | 28 | 699 | **Closes a defect the code already documents.** The ITN intervention's caveat says a household basis is the better fit for net campaigns — and there is no household net measure to switch to. This is it. |
| 6 | `itn_pregnant` | `ML_NETW_W_ITN` | 35 | 28 | 688 | The second ITN priority group after under-fives. |
| 7 | `iptp3` | `ML_IPTP_W_3SP` | 35 | 26 | 696 | Intermittent preventive treatment in pregnancy, 3+ doses. A distinct commodity from ITNs with its own delivery channel (ANC). |
| 8 | `careseeking_diarrhoea` | `CH_DIAT_C_ADV` | 41 | 29 | 763 | Separates *no care sought* from *care sought, no ORS given*. Those need opposite interventions — demand generation against commodity supply — and today they are indistinguishable. |
| 9 | `careseeking_fever` | `ML_FEVT_C_ADV` | 35 | 29 | 700 | Same split for fever, the malaria pathway. |
| 10 | `underweight` | `CN_NUTS_C_WA2` | 41 | 29 | 783 | Weight-for-age. We hold stunting and wasting; underweight is the third of the standard triad. |
| 11 | `severe_wasting` | `CN_NUTS_C_WH3` | 41 | 29 | 783 | The RUTF/SAM denominator. Wasting alone overstates the caseload for a severe-acute programme. |
| 12 | `child_anaemia` | `CN_ANMC_C_ANY` | 36 | 31 | 640 | The target of IFA and micronutrient supplementation, and **the most recent** series here (31 countries since 2015). |
| 13 | `women_anaemia` | `AN_ANEM_W_ANY` | 34 | 27 | 592 | Maternal anaemia; the other half of the supplementation case. |
| 14 | `iron_pregnancy` | `RH_ICSP_W_B99` | 38 | 29 | 699 | Iron supplementation 90+ days — the coverage measure matching the anaemia burden. |
| 15 | `min_meal_frequency` | `CN_IYCF_C_MNA` | 36 | 29 | 679 | Infant and young-child feeding; the behavioural target behind stunting. |
| 16 | `postnatal_2days` | `RH_PCMT_W_DY2` | 38 | 29 | 697 | Postnatal check within two days. The newborn-survival window, and the natural companion to the KMC intervention already registered. |
| 17 | `handwashing` | `WS_HNDW_H_FXD` | 29 | 29 | 582 | Observed handwashing station. Thinnest on this list, and every one of its 29 countries is post-2015. |
| 18 | `water_on_premises` | `WS_SRCE_H_IOP` | 41 | 31 | 793 | "Improved" hides the distance problem; on-premises is what a water-connection programme changes. |
| 19 | `open_defecation` | `WS_TLET_H_NFC` | 41 | 31 | 810 | **Widest region count on the list.** A burden measure, so it selects directly rather than through a coverage gap. |
| 20 | `birth_certificate` | `CP_BREG_C_CRT` | 36 | 28 | 692 | Civil registration — the gateway to every other entitlement, and a plausible Connect delivery use case. |

Every one of these carries subnational data to at least 2024, and most to 2025.

### Outcome

**9,039 values loaded in 53 seconds**, 29–41 countries per measure — within a
country or two of what the API said to expect, which is the check that regions
were matched rather than silently dropped. The 13 coverage measures produced
**62,661 gap rows**; the two care-seeking measures also produced annual
siblings, being the only ones conditional on a recall-window prevalence.

Validated against published national figures by population-weighted rollup:

| | ours | published | |
|---|---:|---|---|
| Liberia, care sought for diarrhoea | 66.6% | 65–70% (LDHS 2019-20) | ✔ |
| Nigeria, severe wasting | 1.7% | 2–3% | ✔ |
| Kenya, zero-dose | 3.9% | low single digits | ✔ |
| Tanzania, household ITN | 72.9% | ~78% | ✔ |
| Nigeria, modern contraception | 17.6% | 12% (NDHS **2018**) | ↑ newer round |
| Nigeria, child anaemia | 55.6% | 68% (NDHS **2018**) | ↓ newer round |

The last two are not errors: these load from **DHS 2024** rounds, and the
comparison figures were 2018. Both move the direction the trend between rounds
actually went — contraceptive use up, anaemia down.

**Known limit.** `itn_household` and `handwashing` are denominated in
*households*, which we hold for 56% of ADM1 and 84% of ADM2 units. Their
unreached counts are floors across part of the continent. The `coverage` field
says so on every answer, but "how many households need a net" is answerable in
fewer places than "what share own one".

---

## Tier 2 — UNICEF subnational SDMX  ✅ LANDED 2026-09-05

**Cost:** one new loader against a public SDMX API. No authentication.
**Endpoint:** `https://sdmx.data.unicef.org/ws/public/sdmxapi/rest/`

Of UNICEF's 42 dataflows, exactly four are subnational. Two of those matter
here, and one of them is **the reachable form of MICS**.

| # | Dataflow | Countries (African) | Levels | Why |
|---|---|---:|---|---|
| 21 | `CME_SUBNATIONAL` | 25 | **ADM1 and ADM2** | Under-five *and* **neonatal** mortality. Our `nmr` reaches only 30.7% of ADM1 units and is the thinnest headline measure we carry; this is the direct fix, from the IGME family we already trust for `u5mr`. |
| 22 | `WASH_HOUSEHOLD_SUBNAT` | **44** | ADM1 | JMP-harmonised water, sanitation and hygiene, 22 indicators. `DATA_SOURCE_MAIN` includes **MICS**, EDSMICS and MIS alongside DHS — so it reaches countries DHS never surveyed. 44 African countries against the 38 our own WASH measures currently cover. |

### Outcome — and a correction to the estimate above

**The "8 new countries" figure was wrong, and the way it was wrong is worth
recording.** It counted country-name overlap between the dataflow and our
holdings. It did not check whether the *geography joins*, and for two of the
biggest it does not:

* **Egypt** publishes to survey analysis regions — "Lower Egypt", "Upper
  Egypt", "Frontier Governorates". We hold the 27 **governorates**. "Lower
  Egypt" spans many of them, so attaching it to each would be inheritance
  dressed as measurement. The boundary matcher refused, correctly.
* **Algeria** publishes to 7 programme zones ("Ept 1 : Nord-Centre") against
  our 48 wilayas. Same refusal.

**Actual gain: 5 countries** — Somalia, Sudan, Comoros, Guinea-Bissau, Tunisia
— of which **3 are DHS-less**. Plus Zambia for neonatal mortality.

| measure | before | after |
|---|---|---|
| WASH family | 38 countries, 56% of ADM1 | **43 countries, 65.8%** |
| `nmr` | 23 countries, 30.7% ADM1, 40% ADM2 | **24 countries, 43.3% ADM1, 71.3% ADM2** |

### The bug this found

The first load of Tunisia reported **14.5% open defecation**. The true national
figure is near zero.

Every stored value was correct. The fault was that UNICEF **mixes tessellations
within one country**: Tunisia carries seven economic regions from MICS 2018
*and* a handful of individual governorates from MICS 2012. Taking the latest
observation per *area* kept both, and our boundary names matched only the
governorates — Kairouan, Kasserine, Sidi Bouzid, Tunis. Three of those four are
the poorest interior governorates. The country then rolled up to the mean of
its worst regions, wearing a national label.

This is the failure mode partial matching always has: **it does not leave a
gap, it produces a biased number that looks complete.** The fix holds one
series per country, so an area either belongs to the survey being read or is
not read at all — the same rule the DHS loader already applied, whose docstring
warns about exactly this and which I reasoned past on the theory that the JMP
pools vintages deliberately. It does; but it pools them across *different
geographies*.

The rollup guard would have caught the national claim independently — a country
whose units are not all evaluated is never emitted as a whole-country row — but
it would not have caught a hand-computed weighted mean, which is what surfaced
it.

### On MICS proper

MICS was the obvious answer to the 13 DHS-less countries, and it is **not
directly reachable**. UNICEF's SDMX warehouse carries MICS results only where
they have been harmonised into a thematic product — which is exactly what
`WASH_HOUSEHOLD_SUBNAT` is. The child-health MICS tables (ORS, immunisation,
nutrition) are published as per-survey reports and SPSS/Stata microdata behind
per-survey registration at `mics.unicef.org`.

Reaching those means **re-tabulating survey microdata**, which is a different
class of work from calling an API: sample weights, cluster design, and matching
each survey's own region names to our boundaries. It is not ruled out. It is
ruled out *of this pass*, and item 22 buys a real part of the benefit for a
fraction of the cost.

---

## Tier 3 — recorded, not yet acted on

| # | Item | Source | Why it is here |
|---|---|---|---|
| 23 ✅ | Motorized travel time | MAP / Weiss et al., CC BY 3.0 | Already a recorded `candidate`. We loaded the walking-only surface, which answers community reach; this answers **referral** access, a different question we currently cannot ask. Same loader, different raster. |
| 24 ✅ | Monthly rainfall climatology | CHIRPS (UCSB/USGS), public domain | **The system has diagnosed its own gap here.** The ORS seasonality work concluded: *"nothing in this dataset is monthly. It can size a campaign and cannot time one."* West Africa has two diarrhoea seasons — bacterial in the rains, rotavirus in the dry — so timing is not a detail. This is what would let a distribution schedule be defended rather than asserted. |
| 25 ❌ | Western Sahara boundaries | geoBoundaries | 55 ISO codes, 54 ADM0 polygons. The one country in scope with no geometry at all, so it can never appear in any answer. Small, and currently a silent absence. |
| 26 | GRID3 Nigeria operational wards | GRID3, CC BY 4.0 | Recorded `candidate`. The only national ward layer and openly licensed, but GRID3 calls it "operational rather than authoritative" — 4% Authorized, 37% Placeholder. Worth loading only against a concrete ADM3 question, and worth **not** loading speculatively. |
| 27 | Nigeria poverty source rescan | — | The `nigeria-household-poverty-targeting` note has **never had a full alternative-source scan**. Its checks confirm what we found; only a scan says whether something better has since been published. |

---

## Excluded, and staying excluded

Licence, not quality. Recorded here so the question is not reopened by accident.

| Source | Reason |
|---|---|
| IHME Global Burden of Disease | Non-commercial agreement excluding for-profit entities **and their employees**, and forbidding re-hosting. Nobody should register a healthdata.org account on a dimagi.com address. |
| Meta / Relative Wealth Index | CC BY-NC 4.0. The World Bank validated it and it is genuinely the best ward-level wealth product for Nigeria — excluded purely on the non-commercial term. |
| WHO GHO / World Malaria Report | CC BY-NC-SA 3.0 IGO. Retained as the **external cross-check** MAP is validated against, which is the right role for it. |
| FAO/World Bank Nigeria poverty map | Quality, for once: 2010–13 survey base, a different poverty line, and in 585 of 775 rows the count and rate × population disagree by more than 2%. |

---

## Tier 3 outcomes

### 23 — motorized travel time ✅

54 of 54 countries in 118 seconds, zero failures. Liberia: **71.2 minutes and
17.8% beyond two hours on foot, against 29.1 minutes and 5.8% by vehicle.**
Motorized is faster everywhere, which is the check that the two surfaces are
what they claim.

### 24 — rainfall seasonality ✅

10-year climatology (2016–2025), 120 CHIRPS rasters, all 54 countries, zero
failures. Every seasonal *shape* validates against the known climate:

| | mm/yr (over people) | peak | wettest quarter | profile |
|---|---:|---|---:|---|
| Niger | 524 | Aug | **81.6%** | one sharp Sahel peak |
| Liberia | 2,873 | Jun | 44.2% | long wet season, May–Oct |
| DR Congo | 1,451 | Dec | 36.2% | bimodal, equatorial |
| South Africa | 708 | Jan | 43.4% | summer rain, dry mid-year |
| Morocco | 345 | Dec | 41.5% | winter rain, dry summer |
| Egypt | 52 | Dec | 61.0% | desert |

**The totals will not match the national average anyone looks up, and that is
correct.** They are weighted by people, not by land. Checked directly:

| | areal | over people | published (areal) |
|---|---:|---:|---|
| Niger | **184** | 524 | ~150–250 ✔ |
| South Africa | **460** | 694 | ~450 ✔ |
| Liberia | **2,302** | 2,669 | ~2,400–2,600 ✔ |

The areal means land on the published figures; population-weighting raises them
because people live where it rains — Niger's population is in the southern
Sahel, not the Sahara. Nearly 3× for Niger, and it is the right number for
"when does it rain on the people we are shipping to". The measure description
carries the Niger comparison so nobody rediscovers it as a bug.

### 25 — Western Sahara ❌ closed, not actionable

geoBoundaries publishes 230 ADM0 countries and **ESH is not among them** — a
disputed territory gbOpen does not issue separately. Sourcing it elsewhere
would mean mixing tessellations, which this system never does. **54 of 55 ISO
codes is the ceiling**, and that is now a documented fact rather than an open
task.

## Order of work

1. **Tier 1** (items 1–20) — one pass, grouped by family, because they share a
   loader and the marginal cost of the twentieth is near zero.
2. **Tier 2** (items 21–22) — new loader; `CME_SUBNATIONAL` first, since `nmr`
   is the thinnest headline measure we carry.
3. **Tier 3** (items 23–27) — individually, each against a real question.

After each tier: re-derive, sweep, export a snapshot, and import to production
**with `--prune`** — a restore without it is additive, and would leave superseded
arithmetic behind. See `derive.sweep_derived` and `targeting_import --prune`.
