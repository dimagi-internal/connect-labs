# IPTsc School Delivery — M&E dashboard design

> **Status:** built 2026-10-06 as template `iptsc_dashboard` (`connect_labs/workflow/templates/iptsc_dashboard.py` + `_render.js`, tests in `__tests__/iptsc_render.test.mjs` and `tests/templates/test_iptsc_dashboard.py`). Live preview: workflow 25613 on opp 2307.
>
> **Where the build differs from this plan:**
> - **No per-child SQL pipeline (`children`).** A filtered `first` cannot tell a successful dose 2 from a failed attempt, because `updated_doses` reads 2 either way. Per-child state is built in the render from the `doses` rows, which is cheap at this volume.
> - **Map deferred.** Schools are a table with GPS-grouped names and saturation sparklines.
> - **Saved runs deferred** (phase 3, unchanged).
> - **Audit and task are links** (`links.auditUrl` / `links.taskUrl`), not persisted flags.
> **Opportunity:** 2307 "IPTsc School Malaria – SCDI" (program 315, org `dimagi-iptsc-nigeria`), 45 visits at time of writing.
> **App:** IPTsc School Delivery v4 – SCDI v.38 (deliver app form summary).

## 0. What the intervention is, in data

IPTsc is **intermittent preventive treatment of malaria in school children**: SPAQ given as **3 observed doses on 3 consecutive days**. Day 1 is SP + AQ, and Days 2 and 3 are AQ only. The dose is set by weight band. This is a short, strictly scheduled multi-visit course. The questions an M&E reader cares about are:

1. Did each registered child get Dose 1, 2 **and** 3?
2. Were the doses on consecutive days?
3. Was each one actually swallowed?
4. Was anyone harmed?
5. Is the data real?

### Forms

| Form | Case action | Connect deliver unit(s) | Key facts |
|---|---|---|---|
| **Day 1 – Registration & Dose 1** | creates `child` case | `iptsc_dose_1` | Registration is only "allowed" Mon–Wed (`registration_allowed`), otherwise supervisor override plus audio approval. It covers eligibility, identity (FLW + child name audio, GPS), consent (photo), demographics (DOB/age, sex, school, ward/village, district, state, caregiver phone, **weight**), safety screen, dose band, Dose 1 (prep confirm, dose photo, DOT, swallowed / vomited / re-dose) and 30-min observation. |
| **Follow-up Dose** | updates `child` | `iptsc_dose_1/2/3` (one per expected dose) | Computes `expected_dose`, `prior_doses` and `due_status` (`early` / due / `missed`). The missed path needs supervisor approval plus a PIN. Then confirm identity (audio, GPS), pre-dose AE check, the dose, observation, and close-out (`updated_doses`, `course_status`, revisit within 3 days, case close). A dose beyond 3 is recorded but **not paid**. |
| **Adverse Event Log** | updates `child` | **none** | Fields: which dose, time since dose, reporter, symptoms, severity (mild → danger sign), current status, action (incl. emergency referral), follow-up, `course_action` (hold / stop). **This form will not appear in the Connect visit export.** |

### Child case properties (the state machine)

`consent_3day, dob, age_years, sex, school, ward_village, district, state, caregiver_phone, child_weight_kg, dose_band, registration_date, doses_given_count, last_dose_number, last_dose_date, last_dose_time, last_dose_status, next_dose_due_date, course_status, ae_last_severity`

`course_status` takes the values `on_track, completed, incomplete, stopped, referred` (truncated in the summary; to be confirmed). The case closes on completed, incomplete, stopped or referred.

### Identity and linking keys

- **Child key:** `form.child_id`. It is set on **both** dosing forms: the new child case id on Day 1, the session case id on Follow-up. Fallback: `form.case.@case_id`.
- **Dose number per visit:**
  - Day 1: always 1 (`form.closeout.dose1_completed` says whether it succeeded).
  - Follow-up: `form.expected_dose` is the attempt, and `form.closeout.dose_completed` / `form.closeout.updated_doses` give the result.
- **Connect `entity_name`** is `<child_id> - Dose N`. It is a useful cross-check, but don't key on it.
- **Quirk to flag to the app builder:** the Follow-up form's `visit_identifier` (Connect `entity_id`) is built from `entity_name_1` ("… - Dose 1") for every dose. Connect `entity_id` therefore does not name the dose on follow-ups, so do not key on it.

### What reaches Labs, and from where

| Source | Contains | Use |
|---|---|---|
| Connect visits (`connect_csv`) | Every form submission with a deliver unit: Day 1 and Follow-up. Also Connect status (approved / rejected / pending / over_limit), flagged, GPS, timestamps. | Attempts, doses, timing, GPS, payment status |
| CommCare HQ `child` cases (`cchq_cases`) | Current state of every child, **including children who never got a follow-up form** | The "who is missing a dose" denominator, course status, next-due date |
| CommCare HQ forms "Adverse Event Log" (`cchq_forms`) | AE detail | Safety tab |

The dashboard therefore needs `auth_requires: ["connect", "commcare_hq"]`.

---

## 1. Dashboard shape

There is **one workflow, `iptsc_dashboard`**: a repo template with a sibling `_render.js`, styled with `window.LabsReport`. It is single-opportunity now and built `multi_opp`-ready so a second implementing partner can be added later.

It has a global filter bar: **date range** (registration date or dose date), **FLW**, **school**, **district / ward**, and **sex / age band**. Every tab respects the bar, and filters go in the URL so a view can be shared.

Tabs:

1. **Overview**: is the programme moving?
2. **Dose completion (1 → 2 → 3)**: who got what, and who is missing a dose. *The core tab.*
3. **Reach & coverage**: where and to whom.
4. **Safety & adverse events**
5. **Protocol compliance**: did FLWs follow the rules?
6. **Data authenticity**: is it real?
7. **Field workers**: a scorecard with flags, plus actions.
8. **Child timeline**: a drill-down, opened from any child row anywhere.
9. **Definitions**: every number's formula and source.

### Tab 1 — Overview

- **Headline tiles** (`R.HeadlineTiles`), each with a week-on-week delta:
  - Children registered
  - Received Dose 1 (successful)
  - **Full course completed (3/3)**, with % of children past their Dose 3 due date
  - Currently on track
  - **Missing a dose** (overdue or incomplete)
  - Adverse events (moderate+ highlighted)
  - Schools reached
  - Active FLWs this week
- **Course funnel:** registered → D1 → D2 → D3, as counts and % of the previous step. Children not yet due for a step are left out of that step's denominator, so recent registrations don't read as drop-outs.
- **Daily activity** (`R.WeeklyActivityCard` or a small Chart.js bar): doses given per day, stacked D1 / D2 / D3. A healthy programme shows a Mon–Wed D1 wave followed by D2 and D3 on the next two days. This shape is itself a quick check that the data is real.
- **Cumulative completion line** over time: cumulative D1 vs cumulative 3/3 completions.
- **"What stands out"** (rule-generated, at most 5 bullets), e.g.:
  - "School X: 40% of children missing Dose 3"
  - "FLW Y registered 30 children in 20 minutes"
  - "2 danger-sign AEs this week"

### Tab 2 — Dose completion (Visit 1 vs 2 vs 3)

This tab answers "are children missing visits?".

**2a. Per-child status classification.** Status is computed per child and evaluated as of today (WAT). Each child sits in exactly one bucket:

| Bucket | Rule |
|---|---|
| ✅ **Completed** | 3 successful doses |
| 🟢 **On track** | Next dose due today or later |
| 🟡 **Overdue – within revisit window** | Due date passed ≤ 3 days ago (the form's own revisit allowance), not closed |
| 🔴 **Missed / incomplete** | `course_status = incomplete`, or > 3 days overdue |
| ⛔ **Stopped – safety** | Safety screen failed, AE `stop_ae`, or hold |
| ↗ **Referred** | Referred to a health facility |
| ⚪ **Registered, no Dose 1** | Registered but Dose 1 not successful (refused / spat / vomited, no re-dose) |

**2b. Dose cascade by registration cohort.** One row per registration week (or day), with these columns:
- registered
- D1 ✓
- D2 ✓, D2 on time, D2 late
- D3 ✓, D3 on time, D3 late
- 3/3 %
- still open

Only "mature" cohorts (registered ≥ 3 days ago, plus a grace period) are graded, so the newest week is never shown as failing.

**2c. Child × dose grid** (the "visit 1 vs 2 vs 3" view). One row per child, three columns D1 / D2 / D3. Each cell is a dot:

| Dot | Meaning |
|---|---|
| ● green | Given on the scheduled day |
| ● amber | Given late, i.e. catch-up (show the +days) |
| ◐ | Attempted but not swallowed |
| ○ red | Missed: due date passed, no dose |
| ○ grey | Not yet due |
| ✕ | Stopped / referred |

- Sorted worst first.
- Filterable to "only children missing a dose".
- Grouped by **school**, then FLW, so a supervisor gets a per-school list of who to chase. It exports to CSV (with a formula-injection guard).
- Clicking a row opens the Child timeline.

**2d. Dose intervals.**
- Histogram of days between D1 → D2 and D2 → D3. The expected value is exactly 1.
- Share of courses completed on 3 **consecutive** days, and share with any gap.
- Doses given on a **weekend** or **public holiday**.

**2e. Drop-off reasons.** Why children fall out, from `dose_not_given_reason`, `revisit_possible = no`, `well_enough = no`, `due_status = missed` with no approval, safety stops and AE stops. Shown as a ranked bar chart.

**2f. Attempt vs success.** Per dose number: form submissions, DOT yes, swallowed first time, vomited → re-dosed → swallowed, failed. This separates "didn't come back" from "came back but the dose failed".

### Tab 3 — Reach & coverage

- **Per school:** children registered, completion %, missing-dose count, FLWs, first and last activity, and GPS centroid.
  - School is **free text**, so normalise it (trim, case-fold, collapse whitespace).
  - Also cluster by GPS (schools ~100 m apart), so spelling variants of the same school merge and genuinely different schools with the same name split. Show the raw name variants.
- **Map** (`window.ConnectMap`): one point per school cluster, sized by children and coloured by completion %. Dosing-event points can be toggled on.
- **Geography:** district → ward / village, with completion % at each level.
- **Demographics:**
  - Sex split, with a % female alert outside 40–60%.
  - Age distribution (5–15, flagging >10s, which need confirmation).
  - Weight distribution and **dose band** distribution (10–<20, 20–40, >40 review, <10 hard stop).
  - Completion % by sex and age band, an equity view showing whether older girls drop out more.
- **Time:** registrations per day/week; doses per weekday (expect D1 Mon–Wed, D2 Tue–Thu, D3 Wed–Fri).
- **School saturation (stands in for coverage).** There is no enrolment list, so coverage % cannot be computed. Each school cluster gets instead:
  - a **cumulative-registrations curve**. A curve that flattens means the school is probably covered; one still rising means work remains.
  - days active, registrations per active school-day, and days since last registration.

  The page says plainly that "reach" means children registered, not a share of an eligible population.

### Tab 4 — Safety & adverse events

- **Pre-dose safety screen outcomes:** acute illness, allergy, recent antimalarial, cotrimoxazole, pregnancy (girls ≥ 10). Show the % screened out on each criterion.
- **Dose tolerance:** vomited after dose (by dose number), re-dose given, re-dose swallowed.
- **30-minute observation:**
  - Who observed: school / FLW / supervisor / other.
  - Result: no reaction / reaction / pending / **left early**.
  - Actions taken.
- **Adverse events** (from the AE Log form plus inline observation reports):
  - Count and rate per 1,000 doses, by dose number.
  - Severity mix (mild / moderate / severe / **danger sign**).
  - Symptom frequency, time since dose, reporter.
  - Current status, referral and emergency referral, course action (hold / stop).
- **Line list** of moderate+ AEs and all referrals, with child, school, FLW, dose, days since and status. AEs whose status is "worsening" or "unknown" and have follow-up required are **open items** and stay pinned at the top.
- **Cross-check:** observation said "reaction reported", but no AE Log form exists for that child. The form prompts for one, so a missing log is a compliance gap.

### Tab 5 — Protocol compliance

Each row is an indicator with a % and its count, a band colour, and a per-FLW split on expand.

| Indicator | Why it matters |
|---|---|
| Registrations on Mon–Wed vs Thu–Sun, and Thu–Sun with supervisor override + audio | 3 consecutive school days are required |
| Holiday "yes / not sure" answered, and override used | Broken schedule risk |
| `availability_next2` = no / not sure but registered anyway | Predicts missed doses |
| Consent "yes" with photo present (100% expected) | Ethics; photo count is checkable |
| Dose prepared confirm + dose photo present | Correct dosing evidence |
| **DOT = yes** share | The core of the intervention |
| Weight band edge cases: 10–<20 kg with review, >40 kg with approval, <10 kg hard stop | Dosing safety; 10–<20 kg is unusual for 5+ |
| Over-10 age confirmations | Protocol scope |
| Missed-dose catch-ups with supervisor approval + PIN | Catch-up governance |
| Early dosing attempts (`due_status = early`) | Should be ~0 |
| Doses beyond 3 (`max_reached`, unpaid) | Over-dosing or duplicate-case signal |
| Return dates explained (`return_confirm`) | Adherence driver |
| Observer is FLW / supervisor vs school member | Supervision quality |

### Tab 6 — Data authenticity ("is it real and happening?")

This tab carries most of the "feel confident" value. Every check produces a **per-FLW** value plus a programme value, and contributing checks roll into one 🔴/🟡/🟢 authenticity flag per FLW.

**Location**
- GPS present on every dosing form, and accuracy distribution (flag > 100 m or exactly 0).
- **Child's follow-up GPS vs their Day-1 GPS:** D2/D3 should be at the same school. Flag a median distance > 500 m.
- **FLW footprint:** visits by an FLW cluster at a few schools. Flag schools with only one visit, or wide scatter.
- **"Camping":** many children's GPS identical to the metre.
- **Implied travel speed** between consecutive submissions by the same FLW (> 60 km/h).

**Time**
- **Throughput:** children dosed per FLW per hour, and minimum gap between consecutive Day-1 forms. A real Day-1 includes weighing, consent and audio, so many under ~2 min is a red flag.
- **Form duration** (`timeStart` → `timeEnd`) by form type, flagging very short forms.
- Submission hour histogram: doses at night or outside school hours.
- **Sync lag**, `timeEnd` → server receipt. Long lags cluster with back-filled data.
- Weekend dosing with no override.

**Evidence present** (the app captures a lot, so measure capture rates)
- Consent photo, dose photo, FLW-name audio, child-name audio.
- `child_said_name = no` rate, with reasons.
- Supervisor audio on overrides.
- Very high "child refused to say name" from one FLW → flag.
- Hand photos to the existing **image audit** flow (`/audit/` sessions or a bulk image audit on dose and consent photos) for duplicate and recycled-photo detection. The `LabsAudit` breakdown can be embedded per FLW.

**Plausibility**
- **Weight vs age:** flag weight-for-age outliers (e.g. a 6-year-old at 45 kg, a 14-year-old at 12 kg).
- **Digit preference:** in weights (.0 / .5 heaping) and **age heaping** at 5 / 10 / 15.
- Sex ratio per FLW (40–60%).
- Identical weights across many children for one FLW (mode share).
- **Too-perfect patterns:** 100% swallowed first time, zero vomiting, zero AEs, 100% on-time completion for an FLW with a large caseload. Real data has some friction; a flawless FLW with volume is worth an audit.
- Follow-up weight is **not** re-measured, so dose-band consistency is inherited. Instead, check the follow-up dose shown matches the band on file.

**Duplicates**
- Same normalised child name + school (+ age) across >1 case, **especially across FLWs**.
- One caregiver phone across many children. Siblings are fine; >4 is not.
- Children with >3 dose forms, or two Dose-1 registrations.
- The same child dosed twice in one day.

**Connect vs CommCare reconciliation**
- Doses per child in Connect visits vs `doses_given_count` on the case.
- Connect **rejected / flagged / over_limit** share per FLW, and why.
- Cases with no Connect visit, and visits with no case.

### Tab 7 — Field workers

A scorecard with one row per FLW:
- children, schools, doses (D1 / D2 / D3)
- **3/3 completion %**, missing-dose count, on-time %
- DOT %, swallow %
- AE rate
- protocol-compliance %, evidence-capture %
- authenticity flag, last active

Cells are banded with `R.ScoreCell` and the table is sortable with `R.useTableSort`.

- Flags are persisted through `view.ensureAutoFlags`, so they are auditable (WORKFLOW_REFERENCE §10).
- **Per-row actions:**
  - **Create audit** (`actions.createAudit` on that FLW's recent visits, pre-filtered to the flagged criterion)
  - **Create task** (`actions.createTask` with a coaching prompt built from the flags)
  - **Open their missing-dose list** (deep link into Tab 2 filtered to the FLW)
- The FLW detail panel shows a daily activity strip, schools, and the child × dose grid for their caseload.

### Tab 8 — Child timeline (drill-down)

- **Header:** child id, school, sex / age / weight / band, consent, course status, current bucket.
- **Vertical timeline** of every form: Day 1, each follow-up attempt, AE logs. Each entry shows:
  - date/time, FLW, GPS distance from Day 1
  - due status (on time / late / catch-up approval)
  - DOT / swallowed / vomit / re-dose
  - observation result
  - photo and audio presence, linking to the images where the image proxy allows
- A visual 3-day strip showing D1 → D2 → D3 against the scheduled dates.

### Tab 9 — Definitions

Every indicator gets four things:
- a plain-English definition
- a numerator and denominator
- its source (Connect visit / HQ case / HQ form)
- its threshold

Each indicator in the other tabs has an ⓘ that opens its entry.

---

## 2. Indicator catalogue (initial thresholds — to be agreed with the programme)

| # | Indicator | Numerator / denominator | Green | Amber | Red |
|---|---|---|---|---|---|
| C1 | Dose 1 success | children with successful D1 / registered | ≥ 95% | 90–95 | < 90 |
| C2 | Full course (3/3) | 3 successful doses / children past D3 due + 1 day grace | ≥ 90% | 80–90 | < 80 |
| C3 | Consecutive-day completion | 3/3 on consecutive days / 3/3 completions | ≥ 85% | 70–85 | < 70 |
| C4 | Dropped after D1 | D1 ✓, no D2 by D2 due + 3 days / D1 ✓ (mature) | ≤ 5% | 5–10 | > 10 |
| C5 | Dropped after D2 | D2 ✓, no D3 by D3 due + 3 days / D2 ✓ (mature) | ≤ 5% | 5–10 | > 10 |
| C6 | Currently overdue | overdue children / open courses | — | > 10% | > 20% |
| S1 | DOT rate | DOT yes / dose attempts | ≥ 98% | 95–98 | < 95 |
| S2 | Vomit rate | vomited / doses given (by dose) | info | > 10% | > 20% |
| S3 | AE rate (moderate+) | moderate+ AEs / 1,000 doses | info | — | any danger sign → immediate |
| S4 | Left observation early | left early / observations | ≤ 2% | 2–5 | > 5 |
| P1 | Off-window registration | Thu–Sun registrations / registrations | ≤ 2% | 2–5 | > 5 |
| P2 | Consent photo captured | photo present / consented | 100% | ≥ 98 | < 98 |
| P3 | Over-limit doses | doses > 3 / children | 0 | any | > 1% |
| A1 | GPS drift D2/D3 vs D1 | median metres per FLW | < 200 | 200–500 | > 500 |
| A2 | Fast Day-1 forms | Day-1 forms < 2 min / Day-1 forms | ≤ 5% | 5–15 | > 15 |
| A3 | Child-name audio captured | audio present / child said name = yes | ≥ 95% | 85–95 | < 85 |
| A4 | Possible duplicate children | flagged pairs / children | 0 | any | > 1% |
| A5 | Weight-for-age outliers | outliers / children | ≤ 2% | 2–5 | > 5 |
| A6 | "Too perfect" | FLW with ≥ 30 doses and 0 vomit, 0 refusal, 100% on time | — | flag for audit | — |

Rules from the KMC and CHC work that carry over:
- Every rate shows its **n**.
- A cell with a denominator below a floor (proposed **10 children**, **20 doses**) reads "insufficient", not a number.
- Recent cohorts are not graded until mature.

---

## 3. Technical approach

**Template:** `connect_labs/workflow/templates/iptsc_dashboard.py` + `iptsc_dashboard_render.js`. The `.py` file reads the render file at import time, as `kmc_programme_metrics.py` does. It is registered in `TEMPLATE_GROUP_OF` under **reports**.

### Pipelines (all keyed on the child)

| Alias | Source | Stage | Purpose |
|---|---|---|---|
| `doses` | `connect_csv` | `visit_level` | One row per dosing form. Fields: `child_id`, `form_name`, `expected_dose`, `dose1_completed` / `dose_completed`, `updated_doses`, `due_status`, `dot`, `swallowed`, `redose_given`, `redose_swallowed`, `obs_result`, `obs_responsible`, `obs_action`, `predose_issue_severity`, `well_enough`, `revisit_possible`, `course_status`, weekday, `registration_allowed`, overrides, `holiday_intervening`, `availability_next2`, consent / photo / audio presence (non-empty → 1), `child_said_name`, weight, `dose_band`, age, sex, school, ward, district, GPS lat/lon (`gps_lat` / `gps_lon` transforms), `timeStart` / `timeEnd`, plus Connect `status` / `flagged`. Add a **window field** `distance_from_prev_child_visit_m` (`lag_haversine` partitioned by `child_id`), the MBW revisit-distance pattern. |
| `children` | `connect_csv` | `entity` (`linking_field: child_id`) | One row per child in SQL. Demographics `first`, D1/D2/D3 dates via filtered `first` on `visit_date` with `filter_path` = dose-number path, successful-dose count, `last` course_status. This is the SQL half of the cascade, as in MBW v6 `mothers`. |
| `child_cases` | `cchq_cases` (`case_type: child`) | `visit_level` | Current HQ state: `doses_given_count`, `next_dose_due_date`, `course_status`, `last_dose_status`, `ae_last_severity`, `closed`. This is how children with no follow-up form appear. |
| `ae_log` | `cchq_forms` (`form_name: "Adverse Event Log"`, `app_id_source: opportunity`) | `visit_level` | AE detail |

**Where computation lives:** extraction and per-child rollups are in SQL (pipelines). Status bucketing, cascade, intervals, authenticity checks and flags are in the render, as pure functions in the `v5_*` style with unit tests in `templates/__tests__/iptsc_render.test.mjs`.

At 45 visits all pipelines can stream eagerly. If volume grows past ~50k visits, switch `doses` to `load: on_demand` and fetch the child timeline per child (§4 on-demand sources).

**Semantic registry, later:** once the indicator definitions settle, the C/S/P indicators fit a semantic registry well (entity = child, `cohort_date` = registration_date, maturity gate = registration + 3 days + grace). That would give saved weekly runs, trends across runs and `indicator_programme_report` drill-downs for free. Phase 1 deliberately doesn't do this, because it is quicker to agree the definitions in a live page first.

**Saved runs (phase 2):**
- `supports_saved_runs: true`.
- Freeze computed summaries into state before `view.complete()` (the MBW freeze-before-complete pattern; manifest `pipelines: []`).
- A weekly scheduled run feeds the "trend across weeks" lines.

**UI kit:**
- `window.LabsReport` for tiles, cards, tabs, score cells, notices and legend.
- Chart.js for histograms and daily bars.
- `window.ConnectMap` for the school map.
- No hand-rolled Tailwind where a LabsReport component exists.

**Gotchas carried over from CHC, MBW and KMC:**
- Keep hooks at the top level of `WorkflowUI`, never after early returns.
- Hold expand/collapse state in the parent.
- Read the CSRF token the way the runner does.
- Use WAT (UTC+1) calendar days everywhere for "same day / next day".
- Show a cold-cache / CommCare-auth banner, not zeros.
- Check `metadata.per_opp[...].error`.

---

## 4. Open questions to resolve before / while building

1. **Field paths.** Confirm exact JSON paths with `get_form_json_paths` (especially `child_id`, `expected_dose`, the closeout calculates, and GPS under `identity/gps` vs `confirm/gps`). Then run `pipeline_preview` on opportunity 2307 and check `fields_all_null`.
2. **Deliver-unit relevance on the Follow-up form.** Which of `iptsc_dose_1/2/3` fires per submission, and does a *failed* attempt still produce a Connect visit? This decides whether Connect visits = attempts or = paid doses.
3. **`course_status` / `last_dose_status` / `due_status` full value sets.** The form summary truncates the calculates. Pull them from the app JSON.
4. **Follow-up `entity_id`** reuses "Dose 1" for every dose (see §0). Raise with the app builder; no dashboard dependency.
5. **AE Log has no deliver unit,** so it needs CommCare HQ auth. Confirm the programme is happy with that requirement.
6. **Thresholds** in §2 are proposals and need sign-off from the programme lead.
7. **No school master list or enrolment exists** (confirmed 2026-10-06). The dashboard therefore reports reach as children registered and uses school saturation curves (Tab 3) as the stand-in for coverage. Schools are derived from the data itself: name normalisation plus GPS clustering. If enrolment figures ever become available, coverage = dosed / enrolled slots into the school table with no other change.

## 5. Phasing

| Phase | Scope |
|---|---|
| **1 — MVP** | Pipelines `doses` + `children` + `child_cases`. Tabs Overview, Dose completion (all of 2a–2f), Field workers (no actions), Child timeline, Definitions. Global filters. |
| **2** | Data authenticity tab, Safety tab with `ae_log`, Protocol compliance tab, school map, FLW flags + audit/task actions, image-audit hookup. |
| **3** | Saved weekly runs and trends, semantic-registry indicators, multi-opp. |
