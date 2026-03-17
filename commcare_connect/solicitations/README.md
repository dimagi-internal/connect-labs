# Solicitations App

RFP (Request for Proposals) and EOI (Expression of Interest) management. Solicitations are scoped by **program_id** (not opportunity_id), and responses are scoped by **organization_id**.

Includes delivery type descriptions, opportunity enrichment data, and a public-facing browse UI.

## Key Files

| File             | Purpose                                                                           |
| ---------------- | --------------------------------------------------------------------------------- |
| `models.py`      | 5 proxy models: solicitations, responses, reviews, delivery types, enrichments    |
| `data_access.py` | `SolicitationDataAccess` — CRUD for all record types + production API integration |
| `views.py`       | 14 views: public browse, manager CRUD, response submission, reviews               |
| `forms.py`       | Dynamic forms: solicitation editor, response (from questions JSON), review        |
| `tables.py`      | django-tables2 definitions                                                        |
| `urls.py`        | URL routing under `/solicitations/`                                               |

## Data Model

All records use experiment=`"solicitations"`.

| Type                            | Proxy Model                     | Scoping              | Purpose                                          |
| ------------------------------- | ------------------------------- | -------------------- | ------------------------------------------------ |
| `Solicitation`                  | `SolicitationRecord`            | `program_id`         | RFP/EOI with questions, deadlines, scope of work |
| `SolicitationResponse`          | `ResponseRecord`                | `organization_id`    | Answers to solicitation questions                |
| `SolicitationReview`            | `ReviewRecord`                  | (linked to response) | Score, recommendation, notes                     |
| `DeliveryTypeDescriptionRecord` | `DeliveryTypeDescriptionRecord` | `public=True`        | Metadata for delivery types (CHC, KMC, etc.)     |
| `OppOrgEnrichmentRecord`        | `OppOrgEnrichmentRecord`        | `program_id`         | Country/region enrichment for opportunities      |

**Key difference from other apps:** Solicitations do NOT use `opportunity_id` for scoping. They use `program_id` for solicitations and `organization_id` for responses.

## Key Patterns

**Dynamic Questions:** Solicitations store a `questions` JSON array. The response form dynamically generates fields from these questions (text, textarea, number, file, multiple_choice).

**Public Records:** Delivery types use `public=True` on LabsRecord so they're queryable without scope matching.

**Production Data:** `get_opp_org_program_data()` fetches opportunities/programs from production API, enriched with local `OppOrgEnrichmentRecord` data.

## Cross-App Connections

- **Depends on:** `labs.integrations.connect.api_client`
- **Used by:** `ai/` (solicitation agent queries data)

## Testing

```bash
pytest commcare_connect/solicitations/
```

Mock `LabsRecordAPIClient` and production API responses.
