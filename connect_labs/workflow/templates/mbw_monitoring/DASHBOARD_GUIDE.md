# MBW Monitoring Dashboard - Indicators & Columns Guide

This document explains every column and indicator shown in the MBW Monitoring Dashboard, written for supervisors and program managers who use the dashboard but may not have access to the underlying code.

---

## Table of Contents

1. [Dashboard Overview](#dashboard-overview)
2. [Tab 1: Overview](#tab-1-overview)
3. [Tab 2: GPS Analysis](#tab-2-gps-analysis)
4. [Tab 3: Follow-Up Rate](#tab-3-follow-up-rate)
5. [Tab 4: FLW Performance](#tab-4-flw-performance)
6. [Red Flag Indicators](#red-flag-indicators)
7. [Color Coding Reference](#color-coding-reference)
8. [Key Definitions](#key-definitions)

---

## Dashboard Overview

The MBW Monitoring Dashboard has **4 tabs**, each providing a different lens on Frontline Worker (FLW) performance:

| Tab                 | Purpose                                                   |
| ------------------- | --------------------------------------------------------- |
| **Overview**        | High-level summary combining all key metrics per FLW      |
| **GPS Analysis**    | GPS-based travel and distance metrics to detect anomalies |
| **Follow-Up Rate**  | Visit completion tracking with per-mother drill-down      |
| **FLW Performance** | Aggregated case metrics grouped by assessment status      |

All data is loaded via a single streaming connection when you open the dashboard. After the first load, a snapshot is saved so subsequent visits load instantly (use "Refresh Data" to fetch fresh data).

### Filter Bar

The filter bar sits above all tabs and provides controls that affect the data shown across the dashboard:

| Filter                     | Scope         | Default       | Description                                                                                                                         |
| -------------------------- | ------------- | ------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **Visit Status**           | All tabs      | Approved only | Filters by Connect visit approval status. Options: Approved, Pending, Rejected, Over Limit. Select one or more statuses to include. |
| **App Version** (GPS only) | GPS tab       | > 14          | Filters GPS visits by app build version. Configurable operator (>, >=, =, <=, <) and version number.                                |
| **FLW filter**             | All tabs      | All           | Multi-select list to filter by specific FLWs.                                                                                       |
| **Mother filter**          | Follow-Up tab | All           | Multi-select list to filter by specific mothers.                                                                                    |

- **Apply**: Click to apply any changes to Visit Status or App Version. These filters require a server reload.
- **Reset**: Restores all filters to defaults (Approved only, App Version > 14, no FLW/mother selection).

> **Note**: Changing Visit Status typically does not re-download data from Connect. The dashboard reuses its cached pipeline data and applies the filter server-side, so switching statuses is usually fast (seconds, not minutes). Exceptions include a cold or expired cache, or when a forced refresh (`?bust_cache=1`) triggers a full re-download.

---

## Tab 1: Overview

The Overview tab provides a single table with one row per FLW. Each column summarizes a different dimension of performance.

### Columns

#### FLW Name

The worker's display name (and username underneath if different). Always visible, cannot be hidden.

#### # Mothers

**What it shows:** Total number of unique mothers registered by this FLW, with the count of eligible mothers in parentheses.

**How it's calculated:** Counts unique mother case IDs from the "Register Mother" forms submitted by this FLW. The number in parentheses counts how many of those mothers are marked as eligible for the full intervention bonus (see [Eligibility](#eligibility) below).

**Data source:** Registration forms from CommCare HQ.

**Data paths:**

- Mother count: unique `form.var_visit_1..6.mother_case_id` values per FLW (from "Register Mother" forms)
- Eligible count: `form.eligible_full_intervention_bonus` = `"1"` (top-level field in "Register Mother" form)

---

#### Last Active

**What it shows:** Number of days since the FLW was last active on the Connect platform.

**How it's calculated:** Uses the `last_active` field from Connect user data — the date of the FLW's most recent form submission or module completion. Displayed as "Xd ago" (e.g., "3d ago" means the FLW was last active 3 days ago).

**Data source:** Connect user export API (`/export/opportunity/{id}/user_data/`), field: `last_active` (DateTimeField on `OpportunityAccess` model, updated on each form submission).

**Color coding:**

- Green: ≤ 7 days (active within the past week)
- Yellow: 8-15 days (inactive for 1-2 weeks)
- Red: > 15 days (inactive for more than 2 weeks)

---

#### GS Score

**What it shows:** The FLW's Gold Standard Visit Checklist score, as a percentage.

**How it's calculated:** A supervisor completes a "Gold Standard Visit Checklist" form in a separate supervisor app while observing the FLW. Each form produces a percentage score (0-100%). The dashboard shows the **first (oldest)** GS score on record for that FLW.

**Why the oldest?** The first assessment represents the baseline evaluation. If a supervisor has assessed the FLW multiple times, only the earliest score is displayed.

**Data source:** Gold Standard Visit Checklist forms from the supervisor app in CommCare HQ. The FLW is identified by their Connect ID recorded in the form.

**Data paths:**

- Score: `form.checklist_percentage` (0-100 integer)
- FLW identity: `form.load_flw_connect_id` (maps to the FLW's Connect username)
- Ordering: `form.meta.timeEnd` (oldest form selected per FLW)

**Color coding:**

- Green: 70% or above
- Yellow: 50-69%
- Red: below 50%

---

#### Post-Test

Currently a placeholder (shows "—"). Reserved for future post-test score tracking.

---

#### Follow-up Rate

**What it shows:** The percentage of scheduled visits that have been completed, considering only eligible mothers and applying a 5-day grace period.

**How it's calculated:**

1. Start with all visits scheduled for mothers marked as **eligible for full intervention bonus**
2. Only include visits whose scheduled date was **5 or more days ago** (grace period — gives FLWs time to complete recent visits before they count against them)
3. Calculate: **(completed visits / total visits due past grace period) × 100**

**Example:** An FLW has 50 visits due 5+ days ago for eligible mothers, and 40 are completed → Follow-up Rate = 80%.

**Data source:** Visit schedules from registration forms (CommCare HQ) + completion data from visit form submissions (Connect API).

**Data paths:**

- Scheduled dates: `form.var_visit_1..6.visit_date_scheduled` (from "Register Mother" forms)
- Expiry dates: `form.var_visit_1..6.visit_expiry_date` (from "Register Mother" forms)
- Visit type: `form.var_visit_1..6.visit_type` (from "Register Mother" forms)
- Mother case ID: `form.var_visit_1..6.mother_case_id` (from "Register Mother" forms)
- Eligibility: `form.eligible_full_intervention_bonus` = `"1"` (from "Register Mother" forms)
- Completion detection: `form.@name` from pipeline visit forms, mapped via completion flags (`antenatal_visit_completion`, `postnatal_visit_completion`, `one_two_week_visit_completion`, `one_month_visit_completion`, `three_month_visit_completion`, `six_month_visit_completion`)

**Color coding:**

- Green: 80% or above
- Yellow: 60-79%
- Red: below 60%

---

#### Eligible 5+

**What it shows:** Among mothers eligible for the full intervention bonus, how many are "still on track" — displayed as a count (e.g., "15/20") and a percentage (e.g., "75%").

**How it's calculated:** A mother is considered "still on track" if she meets **either** of these conditions:

- She has **5 or more completed visits**, OR
- She has **1 or fewer missed visits**

The column shows: _(still on track) / (total eligible) = percentage_

**Why this matters:** This tracks whether eligible mothers are likely to complete the full intervention program. Even if a mother hasn't reached 5 visits yet, she's still on track as long as she hasn't missed more than 1 visit.

**Data paths:**

- Eligibility: `form.eligible_full_intervention_bonus` = `"1"` (from "Register Mother" forms)
- Completed visits: count of visits with status starting with "Completed" (derived from completion flags — see Follow-up Rate above)
- Missed visits: count of visits with status "Missed" (visit not completed and past `form.var_visit_N.visit_expiry_date`)

**Color coding:**

- Green: 85% or above
- Yellow: 50-84%
- Red: below 50%

---

#### % EBF (Exclusive Breastfeeding)

**What it shows:** The percentage of the FLW's postnatal visits where the mother reported exclusive breastfeeding.

**How it's calculated:**

1. Look at all of the FLW's form submissions that include a breastfeeding status field (postnatal visits: Postnatal, Week 1, Month 1, Month 3, Month 6)
2. Count how many report "EBF" (exclusive breastfeeding) as the current feeding status
3. Calculate: **(EBF visits / total visits with breastfeeding data) × 100**

**Why this matters:** Exclusive breastfeeding is a key health indicator. Rates that are too low may indicate counseling gaps; rates that are suspiciously high (above 95%) may indicate data fabrication.

**Data source:** Breastfeeding status field from postnatal visit forms (Connect API pipeline).

**Data paths** (multi-choice field — "ebf" token indicates exclusive breastfeeding):

- `form.feeding_history.pnc_current_bf_status` (Postnatal Visit)
- `form.feeding_history.oneweek_current_bf_status` (1 Week Visit)
- `form.feeding_history.onemonth_current_bf_status` (1 Month Visit)
- `form.feeding_history.threemonth_current_bf_status` (3 Month Visit)
- `form.feeding_history.sixmonth_current_bf_status` (6 Month Visit)

**Color coding:**

- Green: 50-85%
- Yellow: 31-49% or 86-95%
- Red: 0-30% or above 95%

---

#### Revisit Dist.

**What it shows:** The average distance (in kilometers) between successive GPS coordinates when the FLW revisits the **same mother**, with a denominator showing how many cases contributed.

**How it's calculated:**

1. Group all visits by mother (using the mother case ID)
2. Sort each mother's visits by date/time
3. For each pair of consecutive visits to the same mother, calculate the straight-line distance between GPS coordinates using the Haversine formula (accounts for Earth's curvature)
4. Report the **average** of all these distances for the FLW
5. Display a denominator "(N)" where N is the number of mother cases that had 2 or more GPS-tagged visits — i.e., the cases that could actually be compared

**Example:** "0.3 km (12)" means 12 mothers had repeat visits and the average revisit distance was 0.3 km.

**Why this matters:** When an FLW visits the same mother multiple times, the GPS coordinates should be close together (same household). Large distances between revisits to the same mother suggest the FLW may not be visiting the actual location.

**Data source:** GPS coordinates from visit form metadata (Connect API pipeline).

**Data paths:**

- GPS coordinates: `form.meta.location` or `form.meta.location.#text` (format: `"latitude longitude altitude accuracy"`)
- Mother case ID (for grouping): `form.parents.parent.case.@case_id`
- Visit ordering: `form.meta.timeEnd`

---

#### Meter/Visit

**What it shows:** The median distance (in meters) the FLW travels between consecutive visits to **different mothers within a single day**.

**How it's calculated:**

1. For each day the FLW worked, list all visits in chronological order
2. Keep only the first visit per mother per day (deduplicate)
3. Only include days with 2+ unique mothers visited
4. Calculate the straight-line distance between each consecutive pair of visits
5. Take the **median** of all these distances across all working days
6. An optional app version filter can exclude visits from older app versions

**Why this matters:** If an FLW is traveling very short distances between different mothers (under 100 meters), it may indicate that visits are being fabricated from the same location rather than traveling to different households.

**Data source:** GPS coordinates and timestamps from visit forms (Connect API pipeline).

**Data paths:**

- GPS coordinates: `form.meta.location` or `form.meta.location.#text`
- Visit ordering: `form.meta.timeEnd`
- Mother case ID (for deduplication): `form.parents.parent.case.@case_id`
- App version filter: `form.meta.app_build_version` (integer)

**Red flag:** Values below 100 meters are highlighted in red.

---

#### Minute/Visit

**What it shows:** The median time gap (in minutes) between consecutive visits to **different mothers within a single day**.

**How it's calculated:**

1. Same grouping as Meter/Visit (consecutive visits to different mothers per day)
2. Calculate the time difference between each consecutive pair of form submissions
3. Take the **median** across all pairs

**Why this matters:** Very short time gaps between visits may indicate that forms are being submitted rapidly without actual patient interaction.

**Data source:** Form submission timestamps from visit forms (Connect API pipeline).

**Data paths:**

- Submission time: `form.meta.timeEnd` (ISO datetime)
- Mother case ID (for deduplication): `form.parents.parent.case.@case_id`

---

#### Dist. Ratio

**What it shows:** A ratio comparing revisit distance to inter-visit travel distance.

**How it's calculated:** (Revisit Dist. in meters) / (Meter/Visit in meters). Equivalently: Revisit Dist. (km) x 1000 / Meter/Visit (m). A high ratio means the FLW's revisits to the same mother are spread far apart relative to how far they travel between different mothers — which may indicate GPS anomalies.

**Why this matters:** Revisit Dist. and Meter/Visit each tell part of the story. The ratio combines them into a single signal: an FLW who travels short distances between different mothers (low Meter/Visit) but has large revisit distances (high Revisit Dist.) will have a high Dist. Ratio, flagging a potential concern.

---

#### Phone Dup %

**What it shows:** The percentage of the FLW's mothers whose phone numbers appear more than once across the FLW's caseload.

**How it's calculated:**

1. Collect the phone number recorded for each of the FLW's mothers
2. Count how many phone numbers appear more than once (i.e., shared by multiple mothers)
3. Calculate: **(mothers with duplicate phone numbers / total mothers with phone numbers) × 100**

**Why this matters:** While some phone sharing is normal (e.g., family members), a high rate of duplicate phone numbers across supposedly different mothers may indicate fabricated registrations.

**Data source:** Phone number from mother registration forms (CommCare HQ).

**Data paths:**

- Phone number: `form.mother_details.phone_number` (from "Register Mother" forms)
- Fallback: `form.mother_details.back_up_phone_number`

---

#### ANC = PNC

**What it shows:** The number of mothers for whom the ANC (Antenatal Care) visit completion date and the PNC (Postnatal Care) visit completion date are on the **same day**.

**How it's calculated:**

1. For each mother, get the ANC completion date (from ANC Visit forms) and the PNC completion date (from Post Delivery Visit forms)
2. Compare the date portions (ignoring time)
3. Count mothers where both dates exist and are identical

**Why this matters:** ANC visits happen during pregnancy and PNC visits happen after delivery. Having both completion dates on the same day is biologically impossible and strongly suggests data fabrication.

**Data source:** ANC completion date from ANC Visit forms + PNC completion date from Post Delivery Visit forms (Connect API pipeline).

**Data paths:**

- ANC completion date: `form.visit_completion.anc_completion_date` (from "ANC Visit" forms)
- PNC completion date: `form.pnc_completion_date` (from "Post delivery visit" forms)

---

#### Parity

**What it shows:** How concentrated (repetitive) the parity values are across the FLW's mothers.

**What is parity?** Parity is the number of times a woman has given birth (live births or stillbirths after 24 weeks). It's a standard maternal health indicator collected during ANC visits.

**How it's calculated:**

1. Collect the parity value recorded for each of the FLW's mothers (from ANC Visit forms)
2. Count how many values appear more than once (duplicates)
3. Calculate: **(mothers with duplicate parity values / total mothers with parity data) × 100**
4. Also identify the **mode** (most common value) and what percentage of mothers have that value

**Display format:** Shows the duplicate percentage, with the mode value and its percentage as a subtitle (e.g., "67%" with "mode: 2 (45%)").

**Why this matters:** In a real population of pregnant women, you'd expect a natural spread of parity values (some first-time mothers, some with 2-3 previous births, etc.). If an FLW records the same parity value for most of their visits, it could indicate:

- Copy-pasting answers across forms
- Fabricating visit data
- Not actually asking the question during visits

**Data source:** Parity field from ANC Visit forms (Connect API pipeline).

**Data path:** `form.confirm_visit_information.parity__of_live_births_or_stillbirths_after_24_weeks`

---

#### Age

**What it shows:** How concentrated (repetitive) the age values are across the FLW's mothers. Same logic as Parity but applied to mother's age.

**How it's calculated:**

1. Collect the age of each of the FLW's mothers (computed from date of birth, or recorded directly)
2. Count how many age values appear more than once
3. Calculate the duplicate percentage and identify the mode

**Why this matters:** Like parity, a natural population of mothers should have diverse ages. High concentration of a single age suggests possible data fabrication.

**Data source:** Mother's date of birth or age from registration forms (CommCare HQ).

**Data paths:**

- Primary: `form.mother_details.mother_dob` (date of birth — age is computed as current date minus DOB)
- Fallback 1: `form.mother_details.age_in_years_rounded`
- Fallback 2: `form.mother_details.mothers_age`

---

#### Age = Reg

**What it shows:** The percentage of mothers whose date of birth has the **same month and day** as their case registration date.

**How it's calculated:**

1. For each mother, compare the month and day of their recorded date of birth with the month and day of when they were registered in the system
2. Count matches (where month and day are identical)
3. Calculate: **(matching / total with both dates available) × 100**

**Why this matters:** It's statistically very unlikely that a mother's birthday falls on the exact date she was registered. A high percentage strongly suggests the FLW entered the registration date as the date of birth instead of asking the mother — indicating the DOB data is fabricated.

**Data source:** Mother's DOB from registration form mother_details + registration date from form metadata (CommCare HQ).

**Data paths:**

- Mother DOB: `form.mother_details.mother_dob` (from "Register Mother" forms)
- Registration date: `received_on` (top-level form metadata), fallback: `metadata.timeEnd`

---

#### Actions

Provides interactive buttons per FLW (always visible, cannot be hidden):

- **Assessment buttons:** Mark the FLW as Eligible for Renewal, Probation, or Suspended
- **Notes:** View/add notes for the assessment
- **Filter:** Add this FLW to the active filter
- **Task:** Create a task for this FLW (with optional AI assistance via OCS)

---

## Tab 2: GPS Analysis

The GPS Analysis tab focuses on geographic patterns to detect suspicious travel behavior.

### Summary Cards

| Card               | Description                                                                        |
| ------------------ | ---------------------------------------------------------------------------------- |
| **Total Visits**   | Number of form submissions within the selected date range                          |
| **Flagged Visits** | Visits where the distance from the previous visit to the same mother exceeded 5 km |
| **Date Range**     | The selected date range for GPS analysis                                           |
| **Flag Threshold** | 5 km — the distance threshold for flagging suspicious visits                       |

### Shared Data Paths (used by all GPS columns)

All GPS Analysis columns derive from these form fields:

- **GPS coordinates:** `form.meta.location` or `form.meta.location.#text` (format: `"latitude longitude altitude accuracy"`)
- **Visit datetime:** `form.meta.timeEnd` (ISO datetime, used for chronological ordering)
- **Mother case ID:** `form.parents.parent.case.@case_id` (used to group visits to the same mother)
- **Direct case ID:** `form.case.@case_id` (used for unique case counting)
- **App build version:** `form.meta.app_build_version` (integer, used for optional version filtering)
- **Form name:** `form.@name` (identifies visit type)

### Aggregate Map

At the top of the GPS tab, a collapsible map displays all FLW visits on a single view. Each FLW's visits are shown as color-coded pins (a unique color per FLW), so you can visually compare coverage areas and spot overlapping or isolated clusters. The map uses marker clustering — when zoomed out, nearby pins are grouped into numbered clusters that expand as you zoom in. This keeps the map responsive even with thousands of visits. The map is **collapsed by default**; click the toggle to expand it.

### FLW Table Columns

All columns in the GPS table are **sortable** — click any column header to sort ascending or descending. This makes it easy to find outliers (e.g., sort by Dist. Ratio descending to surface the most suspicious FLWs first).

#### Total Visits

The number of form submissions by this FLW within the date range.

#### With GPS

**What it shows:** Count and percentage of visits that have GPS coordinates attached.

**How it's calculated:** Count visits where the form metadata contains parseable GPS coordinates (latitude and longitude). Shown as "X (Y%)" where X is the count and Y is the percentage of total visits.

---

#### Flagged

**What it shows:** Number of visits flagged for suspicious GPS distance.

**How it's calculated:** A visit is flagged when the straight-line distance between it and the FLW's **previous visit to the same mother** exceeds **5 km**. If a mother lives in one place, visits to her should always be in roughly the same location.

**Color coding:** Red text when any visits are flagged.

---

#### Unique Cases

The number of distinct mother cases visited by this FLW within the date range.

**Data path:** Count of unique `form.case.@case_id` values.

---

#### Revisit Dist.

**What it shows:** Average distance (km) between consecutive visits to the same mother, with a denominator showing how many cases contributed to the calculation.

**How it's calculated:** Same as Revisit Dist. in the Overview tab — average Haversine distance between consecutive GPS coordinates for visits to the same mother case, across all mothers for this FLW. Displayed as a value followed by "(N)" where N is the number of mother cases that had 2 or more GPS-tagged visits (i.e., the number of cases that could be compared). For example, "0.3 km (12)" means 12 mothers had repeat visits and the average revisit distance was 0.3 km.

---

#### Max Revisit Dist.

**What it shows:** The single largest distance (km) observed between consecutive visits to the same mother.

**Color coding:** Red and bold when exceeding 5 km.

---

#### Meter/Visit

**What it shows:** The median distance (in meters) the FLW travels between consecutive visits to **different mothers within a single day**. Same calculation as the Meter/Visit column in the Overview tab.

**Red flag:** Values below 100 meters are highlighted in red.

---

#### Dist. Ratio

**What it shows:** A ratio comparing revisit distance to inter-visit travel distance.

**How it's calculated:** (Revisit Dist. in meters) / (Meter/Visit in meters). In other words: Revisit Dist. (km) x 1000 / Meter/Visit (m). A high ratio means the FLW's revisits to the same mother are spread far apart relative to how far they travel between different mothers — which may indicate GPS anomalies.

---

#### Trailing 7 Days

**What it shows:** A sparkline bar chart showing the FLW's daily travel pattern over the last 7 days.

**How it's calculated:**

1. For each day in the last 7 days, group all visits by this FLW
2. Sort visits chronologically and extract GPS coordinates
3. Calculate the total path distance traveled that day (sum of distances between consecutive visit locations)
4. Display as bars with an average daily travel (km/day) label

---

### GPS Drill-Down

Clicking "Details" on an FLW row expands to show individual visit records with:

- **Date:** Visit date (from `form.meta.timeEnd`)
- **Form:** Type of visit (from `form.@name`)
- **Entity:** Mother case name (from `form.mbw_visit.deliver.entity_name`)
- **GPS:** Latitude and longitude coordinates (from `form.meta.location`)
- **Revisit Dist.:** Distance from the previous visit to the same mother (computed via Haversine)
- **Status:** Whether the visit is flagged (distance > 5 km)

---

## Tab 3: Follow-Up Rate

The Follow-Up Rate tab tracks whether each FLW is completing their scheduled visits on time.

### Summary Cards

| Card                       | Description                                       |
| -------------------------- | ------------------------------------------------- |
| **Total Visit Cases**      | Total number of expected visits across all FLWs   |
| **Total FLWs**             | Number of FLWs shown                              |
| **Average Follow-up Rate** | Mean follow-up rate across all FLWs (color-coded) |

### Key Data Paths (Follow-Up)

The follow-up system merges two data sources:

**From "Register Mother" forms (CommCare HQ) — expected visits:**

- Visit schedules: `form.var_visit_1` through `form.var_visit_6`, each containing:
  - `visit_type` — e.g., "ANC Visit", "Postnatal Delivery Visit", "1 Week Visit", etc.
  - `visit_date_scheduled` — when the visit should happen
  - `visit_expiry_date` — deadline after which the visit is considered missed
  - `mother_case_id` — links to the mother
- Visit create flags (determines if visit was scheduled): `form.var_visit_N.create_antenatal_visit`, `create_postnatal_visit`, `create_one_two_visit`, `create_one_month_visit`, `create_three_month_visit`, `create_six_month_visit` = `"1"`
- Mother eligibility: `form.eligible_full_intervention_bonus` = `"1"` (top-level)

**From visit form submissions (Connect API) — completed visits:**

- Form name: `form.@name` (mapped to visit type via normalization: "ANC Visit" → ANC, "Post delivery visit" → Postnatal, etc.)
- Mother case ID: `form.parents.parent.case.@case_id`
- Completion flags: `antenatal_visit_completion`, `postnatal_visit_completion`, `one_two_week_visit_completion`, `one_month_visit_completion`, `three_month_visit_completion`, `six_month_visit_completion`

### FLW Table Columns

#### Follow-up Rate

Same calculation as in the Overview tab — percentage of eligible visits due 5+ days ago that are completed. Shown with a colored progress bar.

#### Completed

Total completed visits (both on-time and late), shown with the percentage of total visits in parentheses.

#### Due

Visits that are not yet completed but haven't passed their expiry date. Includes both "Due - On Time" and "Due - Late" visits.

#### Missed

Visits that were never completed and have passed their expiry date.

#### Per-Visit-Type Breakdown (ANC through Month 6)

Six mini-columns showing completed, due, and missed counts for each visit type individually:

- **ANC** — Antenatal Care Visit
- **Postnatal** — Post Delivery Visit
- **Week 1** — 1 Week Visit
- **Month 1** — 1 Month Visit
- **Month 3** — 3 Month Visit
- **Month 6** — 6 Month Visit

### Visit Status Definitions

Each scheduled visit is assigned one of these statuses based on when it was completed relative to its scheduled date:

| Status                  | Meaning                                                                            |
| ----------------------- | ---------------------------------------------------------------------------------- |
| **Completed - On Time** | Completed within the on-time window (7 days for most visits, 4 days for Postnatal) |
| **Completed - Late**    | Completed after the on-time window but before the visit expired                    |
| **Due - On Time**       | Not yet completed, but currently within the on-time window                         |
| **Due - Late**          | Not yet completed, past the on-time window but before expiry                       |
| **Missed**              | Not completed and past the expiry date — this visit will never be completed        |
| **Not Due Yet**         | The scheduled date hasn't arrived yet                                              |

### On-Time Windows

| Visit Type                | On-Time Window             | Notes                                  |
| ------------------------- | -------------------------- | -------------------------------------- |
| ANC Visit                 | 7 days from scheduled date | Scheduled at 28 weeks gestation        |
| Postnatal / Post Delivery | **4 days** from delivery   | Shorter window due to clinical urgency |
| 1 Week Visit              | 7 days                     | Scheduled 7 days after delivery        |
| 1 Month Visit             | 7 days                     | Scheduled 30 days after delivery       |
| 3 Month Visit             | 7 days                     | Scheduled 90 days after delivery       |
| 6 Month Visit             | 7 days                     | Scheduled 180 days after delivery      |

### Eligibility Filter

A "Full intervention bonus only" checkbox (enabled by default) filters the follow-up rate to only count mothers marked as eligible for the full intervention bonus at registration. Mothers not eligible show a "Not eligible" badge in the drill-down view.

**Data path:** `form.eligible_full_intervention_bonus` = `"1"` (top-level field in "Register Mother" form)

### Mother Drill-Down

Clicking an FLW row expands to show per-mother details:

| Field                  | Data Path                                                                                                    | Source Form         |
| ---------------------- | ------------------------------------------------------------------------------------------------------------ | ------------------- |
| Mother name            | `form.mother_details.format_mother_name` (fallback: `mother_full_name`, or `mother_name` + `mother_surname`) | Register Mother     |
| Age                    | Computed from `form.mother_details.mother_dob` (fallback: `age_in_years_rounded`, `mothers_age`)             | Register Mother     |
| Phone number           | `form.mother_details.phone_number` (fallback: `back_up_phone_number`)                                        | Register Mother     |
| Registration date      | `received_on` (top-level form metadata)                                                                      | Register Mother     |
| Household size         | `form.number_of_other_household_members` (top-level)                                                         | Register Mother     |
| Preferred visit time   | `form.var_visit_1.preferred_visit_time`                                                                      | Register Mother     |
| ANC completion date    | `form.visit_completion.anc_completion_date`                                                                  | ANC Visit           |
| PNC completion date    | `form.pnc_completion_date`                                                                                   | Post Delivery Visit |
| Expected delivery date | `form.mother_birth_outcome.expected_delivery_date`                                                           | Register Mother     |
| Baby DOB               | `form.capture_the_following_birth_details.baby_dob`                                                          | Post Delivery Visit |
| Eligibility            | `form.eligible_full_intervention_bonus` (top-level, `"1"` = eligible)                                        | Register Mother     |

---

## Tab 4: FLW Performance

The FLW Performance tab aggregates case-level metrics grouped by each FLW's latest assessment status.

### Performance by Status Table

Each row represents one assessment status category. FLWs are grouped into the status assigned during their most recent assessment (from monitoring sessions or audit sessions).

| Status                   | Color  | Meaning                                             |
| ------------------------ | ------ | --------------------------------------------------- |
| **Eligible for Renewal** | Green  | Good performance, eligible for contract renewal     |
| **Probation**            | Yellow | Underperforming, not eligible for renewal           |
| **Suspended**            | Red    | Strong evidence of fraud or severe deficiencies     |
| **No Category**          | Gray   | Not yet assessed in any monitoring or audit session |

**Data path for FLW status:** `run.data.state.worker_results.{username}.result` (from workflow monitoring runs) or audit session `overall_result` (from audit sessions). The most recent assessment by date is used.

### Columns

All columns in the Performance tab are **computed from the same visit data** used in the Follow-Up Rate tab (see [Key Data Paths (Follow-Up)](#key-data-paths-follow-up) above). They aggregate the visit statuses across mothers, grouped by the FLW's assessment status.

#### # FLWs

Number of FLWs in this assessment status category.

#### Total Cases

Total registered mothers across all FLWs in this status group (regardless of eligibility).

#### Eligible at Reg

Mothers marked as eligible for the full intervention bonus at the time of registration.

**Data path:** `form.eligible_full_intervention_bonus` = `"1"` (from "Register Mother" forms)

#### Still Eligible

**What it shows:** How many eligible mothers are "still on track" for the full program.

**How it's calculated:** Among eligible mothers, count those where:

- **5 or more visits are completed**, OR
- **1 or fewer visits are missed**

#### % Still Eligible

**(Still Eligible / Eligible at Reg) × 100**

**Color coding:**

- Green: 85% or above
- Yellow: 50-84%
- Red: below 50%

#### % ≤1 Missed

**What it shows:** Percentage of **eligible** mothers (`eligible_full_intervention_bonus = "1"`) with 0 or 1 missed visits.

**How it's calculated:** Count eligible mothers where total missed visits ≤ 1, divide by total eligible mothers in this status group.

#### % 4 Visits On Track

**What it shows:** Among **eligible** mothers whose Month 1 visit is due (5-day grace), what percentage have 3 or more completed visits?

**How it's calculated:**

1. Filter to eligible mothers whose Month 1 visit scheduled date is 5+ days ago (i.e., all 4 visits up to Month 1 should be completable by now)
2. Count those with 3+ total completed visits (the "3-of-4" threshold — on track if at most 1 visit is incomplete)
3. **(eligible with ≥3 completed / total eligible with Month 1 due) × 100**

**Data path (denominator):** `form.var_visit_N.visit_date_scheduled` where `visit_type` = "1 Month Visit", filtered to eligible mothers with dates ≤ (today - 5 days)

#### % 5 Visits Complete

**What it shows:** Among **eligible** mothers whose Month 3 visit is due (5-day grace), what percentage have 4 or more completed visits?

**How it's calculated:** Same logic as above, but:

- Denominator: eligible mothers whose Month 3 scheduled date is 5+ days ago
- Numerator: those with 4+ completed visits

**Data path (denominator):** `form.var_visit_N.visit_date_scheduled` where `visit_type` = "3 Month Visit", filtered to eligible mothers with dates ≤ (today - 5 days)

#### % 6 Visits Complete

**What it shows:** Among **eligible** mothers whose Month 6 visit is due (5-day grace), what percentage have 5 or more completed visits?

**How it's calculated:** Same logic:

- Denominator: eligible mothers whose Month 6 scheduled date is 5+ days ago
- Numerator: those with 5+ completed visits

**Data path (denominator):** `form.var_visit_N.visit_date_scheduled` where `visit_type` = "6 Month Visit", filtered to eligible mothers with dates ≤ (today - 5 days)

### Totals Row

The bottom row aggregates all status groups together — total FLWs, total cases, and weighted percentages across all categories.

---

### Monthly Visit Schedule Sub-Table

Below the performance-by-status table, a second table shows visit completion rates broken down by **visit type** and **month**.

| Aspect           | Description                                                                              |
| ---------------- | ---------------------------------------------------------------------------------------- |
| **Rows**         | One per visit type: ANC, Postnatal, Week 1, Month 1, Month 3, Month 6, plus a Totals row |
| **Columns**      | One per month (Sep 2025 through Jul 2026), plus a Total column                           |
| **Cell content** | Depends on display mode (toggle buttons at top):                                         |
|                  | - **X / Y**: Completed count vs total scheduled                                          |
|                  | - **Completed**: Just the completed count                                                |
|                  | - **Scheduled**: Just the total scheduled count                                          |
|                  | - **% Percent**: Completion percentage                                                   |

**How it's calculated:** For each visit type and month, count the number of visits whose scheduled date falls in that month, and how many of those are completed. Each cell shows the selected view of these two numbers.

**Data paths:**

- Visit type: `form.var_visit_N.visit_type` (from "Register Mother" forms)
- Scheduled date (determines month bucket): `form.var_visit_N.visit_date_scheduled`
- Completion: determined by matching pipeline form submissions to expected visits via completion flags

---

## Red Flag Indicators

When creating a task for an FLW (via the OCS AI integration), the system automatically detects red flag indicators based on the FLW's metrics. These are included in the AI prompt to guide the conversation.

| Red Flag                          | Threshold            | What It Means                                             |
| --------------------------------- | -------------------- | --------------------------------------------------------- |
| **Low Gold Standard Score**       | GS Score < 50%       | FLW performed poorly on supervised assessment             |
| **Low Follow-Up Visit Rate**      | Follow-up Rate < 50% | More than half of due visits are incomplete               |
| **Low Case Eligibility Rate**     | Eligible 5+ < 50%    | More than half of eligible mothers are off track          |
| **Low Travel Distance Per Visit** | Meter/Visit < 100m   | FLW appears to be submitting forms from the same location |
| **High Phone Duplicate Rate**     | Phone Dup > 30%      | Too many mothers sharing phone numbers                    |
| **ANC/PNC Same-Date Anomaly**     | ANC=PNC ≥ 5 cases    | Multiple biologically impossible same-day completions     |
| **Abnormal EBF Rate**             | EBF ≤ 30% or > 95%   | Breastfeeding rate outside expected range                 |

---

## Color Coding Reference

### Follow-Up Rate / GS Score Colors

| Color  | Follow-up Rate | GS Score |
| ------ | -------------- | -------- |
| Green  | ≥ 80%          | ≥ 70%    |
| Yellow | 60-79%         | 50-69%   |
| Red    | < 60%          | < 50%    |

### EBF Colors

| Color  | Range            |
| ------ | ---------------- |
| Green  | 50-85%           |
| Yellow | 31-49% or 86-95% |
| Red    | ≤ 30% or > 95%   |

### Eligible 5+ / % Still Eligible Colors

| Color  | Range  |
| ------ | ------ |
| Green  | ≥ 85%  |
| Yellow | 50-84% |
| Red    | < 50%  |

### Last Active Colors

| Color  | Range         |
| ------ | ------------- |
| Green  | ≤ 7 days ago  |
| Yellow | 8-15 days ago |
| Red    | > 15 days ago |

### GPS Flags

| Indicator                     | Threshold                                          |
| ----------------------------- | -------------------------------------------------- |
| Visit flagged                 | Distance from previous visit to same mother > 5 km |
| Max Revisit Dist. highlighted | > 5 km                                             |
| Meter/Visit red flag          | < 100 meters                                       |

---

## Key Definitions

### Eligibility

A mother is "eligible for the full intervention bonus" if she was marked as `eligible_full_intervention_bonus = "1"` in her registration form. This flag is set at registration time and does not change. The follow-up rate calculation and several Performance tab metrics only count eligible mothers.

**Data path:** `form.eligible_full_intervention_bonus` (top-level field in "Register Mother" form)

### Grace Period (5 days)

The follow-up rate only counts visits whose scheduled date was **5 or more days ago**. This gives FLWs a reasonable window to complete recent visits before they affect their performance score. A visit scheduled yesterday wouldn't count against them yet.

### Haversine Distance

The straight-line distance between two GPS points on Earth's surface, accounting for the planet's curvature. Used for all distance calculations in the dashboard. While real travel distances are longer (roads aren't straight lines), Haversine provides a consistent, comparable baseline.

### Assessment Status

The result assigned to an FLW during a monitoring session or audit:

- **Eligible for Renewal** — Good performance
- **Probation** — Underperforming
- **Suspended** — Evidence of fraud or severe issues (label only — does not trigger any action on the Connect platform)

The dashboard uses the **most recent** assessment from any monitoring or audit session.

**Data path:** `run.data.state.worker_results.{username}.result` (values: `"eligible_for_renewal"`, `"probation"`, `"suspended"`, or `null`)

### Visit Types

The MBW program includes 6 visit types in a mother's care journey:

| Visit                         | When                    | Purpose                   |
| ----------------------------- | ----------------------- | ------------------------- |
| **ANC Visit**                 | ~28 weeks of pregnancy  | Antenatal care assessment |
| **Postnatal / Post Delivery** | At delivery (EDD)       | Immediate postnatal care  |
| **1 Week Visit**              | 7 days after delivery   | Early newborn care        |
| **1 Month Visit**             | 30 days after delivery  | Growth monitoring         |
| **3 Month Visit**             | 90 days after delivery  | Continued follow-up       |
| **6 Month Visit**             | 180 days after delivery | Final program visit       |
