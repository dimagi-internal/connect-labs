# KMC Project Metrics Dashboard — Design Document

> **Status: shipped (2026-03-09) — historical record, not current-state.**

**Date:** 2026-03-09
**Status:** Approved
**Template Key:** `kmc_project_metrics`

## Purpose

A program-level M&E dashboard for the KMC (Kangaroo Mother Care) project that aggregates visit data across all FLWs and SVNs (Small Vulnerable Newborns) to show overall project performance. Complements the existing `kmc_longitudinal` template which tracks individual children.

## Data Sources

Two spreadsheets define the metrics:
1. **FLW Performance & Fraud Detection Thresholds** — 13 metrics with red-flag thresholds (avg visits, weight consistency, GPS distance, equipment photos)
2. **M&E Indicator Framework** — ~20 indicators across Impact, Intermediate Outcomes, Outputs, and Process Quality levels

This template focuses on **program-level M&E indicators** (Spreadsheet 2) that can be computed from CommCare form submission data.

## Architecture

- **Template type:** Single Python file (`kmc_project_metrics.py`) in `workflow/templates/`
- **Pipeline:** Single `visits` pipeline using `connect_csv` data source, `visit_level` terminal stage, `beneficiary_case_id` linking
- **Render:** 3-view tabbed React UI (follows `kmc_longitudinal` pattern)
- **Cross-template link:** Navigates to `kmc_longitudinal` workflow for individual child drill-down

## Pipeline Schema

### Data Source
- Type: `connect_csv`
- Grouping key: `username`
- Terminal stage: `visit_level`
- Linking field: `beneficiary_case_id`

### Fields (from Record Visit Details form)

| Field Name | Path(s) | Transform | Purpose |
|------------|---------|-----------|---------|
| `beneficiary_case_id` | `form.case.@case_id`, `form.kmc_beneficiary_case_id` | — | Child linking |
| `child_name` | `form.svn_name`, `form.grp_beneficiary_details.child_name` | — | Display |
| `visit_number` | `form.grp_kmc_visit.visit_number` | int | Visit sequencing |
| `visit_date` | `form.grp_kmc_visit.visit_date` | date | Timeline |
| `visit_timeliness` | `form.grp_kmc_visit.visit_timeliness` | — | Schedule compliance |
| `visit_type` | `form.grp_kmc_visit.visit_type` | — | Visit classification |
| `weight` | `form.anthropometric.child_weight_visit` | float | Weight in grams |
| `birth_weight` | `form.child_weight_birth` | float | Birth weight in grams |
| `kmc_hours` | `form.kmc_24-hour_recall.kmc_hours` | int | Primary caregiver KMC hours |
| `kmc_hours_secondary` | `form.kmc_24-hour_recall.kmc_hours_secondary` | int | Secondary caregiver KMC hours |
| `total_kmc_hours` | `form.kmc_24-hour_recall.total_kmc_hours` | float | Combined KMC hours |
| `feeding_provided` | `form.kmc_24-hour_recall.feeding_provided` | — | Feeding type (multi-select) |
| `danger_sign_positive` | `form.danger_signs_checklist.danger_sign_positive` | — | Any danger sign flag |
| `danger_sign_list` | `form.danger_signs_checklist.danger_sign_list` | — | List of danger signs |
| `child_referred` | `form.danger_signs_checklist.child_referred` | — | Referral made |
| `child_taken_to_hospital` | `form.referral_check.child_taken_to_the_hospital` | — | Referral completed |
| `child_alive` | `form.child_alive` | — | Mortality tracking |
| `kmc_status` | `form.grp_kmc_beneficiary.kmc_status` | — | Current program status |
| `kmc_status_discharged` | `form.kmc_discontinuation.kmc_status_discharged` | — | Discharge flag |
| `reg_date` | `form.grp_kmc_beneficiary.reg_date` | date | Registration date |
| `days_since_reg` | `form.days_since_reg` | int | Days since registration |
| `first_visit_date` | `form.grp_kmc_visit.first_visit_date` | date | Date of first visit |
| `gps` | `form.gps_visit`, `form.visit_gps_manual` | — | GPS coordinates |
| `time_end` | `form.meta.timeEnd` | date | Submission timestamp |
| `form_name` | `form.@name` | — | Form type |
| `successful_feeds` | `form.danger_signs_checklist.successful_feeds_in_last_24_hours` | int | Feeding count |
| `svn_temperature` | `form.danger_signs_checklist.svn_temperature` | float | Temperature |
| `visit_pay_yes_no` | `form.grp_kmc_visit.visit_pay_yes_no` | — | Payment eligibility |

## Computable M&E Indicators

### Impact Level
| Indicator | Calculation | Data Needed |
|-----------|-------------|-------------|
| 28-day post-enrollment mortality rate | Deaths within 28 days of enrollment / Total SVNs enrolled ≥28 days ago | `child_alive`, `reg_date`, `days_since_reg` |

### Intermediate Outcomes
| Indicator | Calculation | Data Needed |
|-----------|-------------|-------------|
| Avg KMC hours (primary caregiver) | Mean of `kmc_hours` across all visits | `kmc_hours` |
| Avg KMC hours (secondary caregiver) | Mean of `kmc_hours_secondary` across all visits | `kmc_hours_secondary` |
| % exclusive breastfeeding at completion | Final visits where `feeding_provided` = exclusive breastfeeding / completers | `feeding_provided`, `visit_number` |
| % referrals completed | Visits where `child_taken_to_hospital` = yes / visits where `child_referred` = yes | `child_referred`, `child_taken_to_hospital` |

### Outputs
| Indicator | Calculation | Data Needed |
|-----------|-------------|-------------|
| # eligible infants enrolled | Count unique `beneficiary_case_id` | `beneficiary_case_id` |
| Enrollment over time | Cumulative unique children by `reg_date` | `reg_date`, `beneficiary_case_id` |
| Avg age at enrollment | Mean child age at registration (from DOB) | `reg_date`, DOB (from registration) |
| Mean days discharge→first visit | Mean of (first_visit_date - hospital discharge) | `first_visit_date`, discharge date |
| % families retained ≥28 days | SVNs with visits spanning ≥28 days / total enrolled | `reg_date`, `visit_date`, `beneficiary_case_id` |
| # referrals made | Count visits where `child_referred` = yes | `child_referred` |
| % visits on schedule | Visits where `visit_timeliness` = on-time / total visits | `visit_timeliness` |
| % visits with danger signs assessed | Visits with `danger_sign_positive` non-null / total visits | `danger_sign_positive` |
| Total visits completed | Count all visit rows | row count |
| Avg visits per child | Total visits / unique children | `beneficiary_case_id` |
| Weight gain trajectory | Avg weight by visit number | `weight`, `visit_number` |

## UI Design

### View 1: Overview
```
┌─────────────────────────────────────────────────────────┐
│  [Overview]  [Outcomes & Outputs]  [Indicators Table]   │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ SVNs     │ │ Active   │ │ 28-Day   │ │ Mortality│  │
│  │ Enrolled │ │ SVNs     │ │ Retention│ │ Rate     │  │
│  │   142    │ │    87    │ │  78.5%   │ │  2.1%    │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ Avg KMC  │ │ Referrals│ │ Total    │ │ Avg Days │  │
│  │ Hours/Day│ │ Made     │ │ Visits   │ │ to 1st   │  │
│  │   5.2    │ │    23    │ │   534    │ │  2.3     │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
│                                                         │
│  ┌──────────────────────┐ ┌────────────────────────┐   │
│  │ Enrollment Trend     │ │ Visits Per Week        │   │
│  │ (cumulative line)    │ │ (bar chart)            │   │
│  │                      │ │                        │   │
│  └──────────────────────┘ └────────────────────────┘   │
│                                                         │
│  [📊 View Individual Children →]                        │
└─────────────────────────────────────────────────────────┘
```

### View 2: Outcomes & Outputs
```
┌─────────────────────────────────────────────────────────┐
│  KMC Practice                                           │
│  ┌──────────────────────────────────────────────┐       │
│  │ Avg KMC Hours Over Time (line chart)         │       │
│  │ - Primary caregiver (solid)                  │       │
│  │ - Secondary caregiver (dashed)               │       │
│  │ - 8-hour target line (dotted)                │       │
│  └──────────────────────────────────────────────┘       │
│                                                         │
│  Nutrition & Feeding                                    │
│  ┌──────────────────┐ ┌────────────────────────┐       │
│  │ EBF Rate: 72%    │ │ Feeding Type Breakdown │       │
│  │ (of completers)  │ │ (donut chart)          │       │
│  └──────────────────┘ └────────────────────────┘       │
│                                                         │
│  Health Outcomes                                        │
│  ┌──────────────────────────────────────────────┐       │
│  │ Avg Weight by Visit Number (line chart)      │       │
│  │ with 2500g threshold line                    │       │
│  └──────────────────────────────────────────────┘       │
│  ┌──────────────────┐ ┌────────────────────────┐       │
│  │ Danger Sign      │ │ Referral Completion    │       │
│  │ Incidence: 12%   │ │ Rate: 85%             │       │
│  └──────────────────┘ └────────────────────────┘       │
│                                                         │
│  Visit Quality                                          │
│  ┌──────────────────┐ ┌────────────────────────┐       │
│  │ On Schedule:     │ │ Danger Signs           │       │
│  │ 81%              │ │ Assessed: 94%          │       │
│  └──────────────────┘ └────────────────────────┘       │
└─────────────────────────────────────────────────────────┘
```

### View 3: Indicators Table
```
┌─────────────────────────────────────────────────────────┐
│ Level    │ Indicator            │ Value │ Status │ Trend │
├──────────┼──────────────────────┼───────┼────────┼───────┤
│ Impact   │ 28-day mortality     │ 2.1%  │ 🟢     │ ↓     │
│ Outcome  │ Avg KMC hours (pri)  │ 5.2h  │ 🟡     │ ↑     │
│ Outcome  │ EBF rate             │ 72%   │ 🟢     │ →     │
│ Outcome  │ Referral completion  │ 85%   │ 🟢     │ ↑     │
│ Output   │ SVNs enrolled        │ 142   │ 🟢     │ ↑     │
│ Output   │ Days to 1st visit    │ 2.3   │ 🟢     │ ↓     │
│ Output   │ Retention ≥28d       │ 78%   │ 🟡     │ →     │
│ Output   │ Visits on schedule   │ 81%   │ 🟡     │ ↑     │
│ Output   │ Danger signs checked │ 94%   │ 🟢     │ →     │
│ ...      │ ...                  │ ...   │ ...    │ ...   │
└─────────────────────────────────────────────────────────┘
```

## Status Colors
- **Green:** Meeting target / trending well
- **Amber:** Approaching threshold / needs attention
- **Red:** Below target / requires action

Thresholds will be configurable in DEFINITION config, defaulting to reasonable M&E targets.

## Cross-Template Linking

A "View Individual Children" button in the Overview links to the kmc_longitudinal workflow for the same opportunity. Uses the `links` prop pattern or constructs the URL directly.

## Non-Goals
- FLW-level scorecards (deferred to a separate template)
- Indicators requiring external data (satisfaction surveys, hospital studies, NNT)
- Indicators requiring registration form data that isn't carried forward into visit form case properties (e.g., exact DOB, hospital discharge date — these may be approximated from case properties available in visit forms)
