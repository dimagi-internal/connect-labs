# DDD Context — Campaign Utility Tool

## Project

Standalone Django app at `/campaign/` on labs.connect.dimagi.com (CommCare Connect Labs). A "single pane of glass" for running a national measles–rubella vaccination campaign — replacing a fragmented stack (CommCare HQ + an external KYC provider + payment platforms + GIS/spreadsheets) with one tool.

## base_url

https://labs.connect.dimagi.com/campaign/ (view the national campaign at /campaign/?campaign=MR-NAT-2026)
Auth: CommCare HQ OAuth (separate from the labs session). Recording needs a CommCare-authed browser session (Playwright + ACE_HQ creds, or /ace:labs-login establishes the CCHQ session the campaign OAuth reuses).

## What the tool does (the workflows)

- **Overview** — real-time KPIs (funding, enrolment/attendance/verification/payments progress, workforce distribution, KYC + payment donuts, fraud/verification alerts), filterable.
- **Workers › Payments** — review/validate/approve worker payments with daily-level approval; fraud guard blocks payments for duplicate/failed-KYC/flagged workers.
- **Workers › KYC** — verification status, documents, duplicate/shared-identifier fraud detection, investigations.
- **Activity › Details + Microplanning & Budget** — activities, per-LGA microplans (workforce planned vs actual, budget vs spent, vaccine doses, coverage targets).
- **Reporting & Monitoring** — enrolment/attendance/payment trends, household coverage, CSV export, custom-report builder, geographic coverage MAP (region choropleth + worker GPS).
- **System Administration** — RBAC user management, connection settings, training hub.

## Data fidelity (the differentiator)

Workers are synthetic CommCare **cases** on **real Nigeria geography** (37 states / 774 LGAs / ~9,300 wards from labs AdminBoundary), served via a synthetic CommCare project space through the **Case API** — the exact path real data takes. The tool is the primary store only for things with no CommCare/Connect parallel (payment-approval workflow, activities, microplans, reporting, audit). National scale (up to 50k workers) is usable via a paginated bootstrap.

## Requirements (evidence)

- Spec: "Spec: Campaign Management Utility" (Google Doc, drive folder 1cNpjEn_Smy6mHx5rWzflp20hbx65dlbG)
- Data Model PDF: "Campaign Utility Tool_Data Model.pdf" (12 datasets / 6 domains / 2 owners: CommCare HQ vs the Utility)
- Design doc: docs/superpowers/specs/2026-06-18-campaign-utility-tool-design.md
- Shipped PRs this session: #676,#677,#687,#690,#692,#693,#694,#695,#696

## Narrative direction

The most compelling overall narrative: a campaign administrator running a national vaccination campaign sees and acts on the whole thing in one place — catch a fraudulent payment before it goes out, validate KYC, watch coverage fill in on the map, export a donor report — instead of stitching together CommCare exports, KYC emails, payment spreadsheets, and GIS tools. Want SETS of videos covering the compelling workflows.

## Current phase

First DDD run. Tool is built + deployed. National synthetic data exists as a build path (campaign_build_national MCP tool) but may need standing up in labs before rendering for the map/scale scenes.
