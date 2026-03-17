# Solicitations New — Design Document

**Date:** 2026-03-02
**Status:** Approved
**App name:** `solicitations`

## Purpose

Rebuild the solicitations system as a public-facing network growth engine. Program managers post public solicitations (RFPs/EOIs) to attract new and existing organizations. Respondents create or select an LLOEntity (real-world organization) and submit responses. Managers review and select entities.

The old solicitations app was a transactional POC scoped internally. The new system prioritizes:
- Fully public browsing (no login required)
- Driving new sign-ups and registrations
- LLOEntity as the respondent (not workspace)
- API + MCP + UI on a shared data_access layer

## Architecture

Approach A: Thin API Layer. One `data_access.py` with three consumers.

```
data_access.py (LabsRecord API)
    ├── Django views (HTML templates for public UI)
    ├── JSON API views (simple Django views returning JSON)
    └── MCP tools (call data_access directly)
```

### File Structure

```
solicitations/
├── data_access.py          # All business logic, talks to LabsRecord API
├── views.py                # Django template views (public + authenticated)
├── api_views.py            # JSON API endpoints (simple Django views)
├── mcp_tools.py            # MCP tool definitions (call data_access directly)
├── models.py               # Proxy models (SolicitationRecord, ResponseRecord, etc.)
├── urls.py                 # URL routing for both HTML and API
├── forms.py                # Django forms for solicitation + response + review
└── templates/
    └── solicitations/  # Server-rendered pages
```

### Data Storage

All data stored via LabsRecord API (production). No local Django ORM models. Proxy models provide typed property access to JSON data.

## Data Model

### SolicitationRecord (scoped by program_id)

| Field | Type | Notes |
|-------|------|-------|
| title | string | |
| description | string | |
| scope_of_work | string | |
| solicitation_type | "eoi" \| "rfp" | |
| status | "draft" \| "active" \| "closed" | |
| is_public | boolean | Visible without login |
| questions | JSON array | `[{id, text, type, required, options}]` |
| application_deadline | date | |
| expected_start_date | date | |
| expected_end_date | date | |
| estimated_scale | string | |
| contact_email | string | |
| created_by | string | Username |
| program_name | string | |

### ResponseRecord (scoped by llo_entity_id)

| Field | Type | Notes |
|-------|------|-------|
| solicitation_id | int | Links to solicitation |
| llo_entity_id | string | The real-world entity responding |
| llo_entity_name | string | |
| responses | dict | `{question_id: answer}` |
| status | "draft" \| "submitted" | |
| submitted_by_name | string | |
| submitted_by_email | string | |
| submission_date | datetime | |

### ReviewRecord (linked to response)

| Field | Type | Notes |
|-------|------|-------|
| response_id | int | Links to response |
| score | int | 1-100 |
| recommendation | string | approved/rejected/needs_revision/under_review |
| notes | string | |
| tags | string | |
| reviewer_username | string | |
| review_date | datetime | |

### Dropped from old system

- DeliveryTypeDescriptionRecord (delivery type catalog)
- OppOrgEnrichmentRecord (opportunity browsing/enrichment)

## URL Structure

### Public (no login required)

```
/solicitations/                          → public listing
/solicitations/<int:pk>/                 → public detail
```

### Authenticated UI

```
/solicitations/manage/                   → manager's solicitation list
/solicitations/create/                   → create solicitation form
/solicitations/<int:pk>/edit/            → edit solicitation form
/solicitations/<int:pk>/responses/       → view responses (manager)
/solicitations/<int:pk>/respond/         → submit response
/solicitations/response/<int:pk>/        → response detail
/solicitations/response/<int:pk>/review/ → review form
```

### JSON API

```
/solicitations/api/solicitations/        → GET list, POST create
/solicitations/api/solicitations/<id>/   → GET detail, PUT update
/solicitations/api/responses/            → GET list, POST create
/solicitations/api/responses/<id>/       → GET detail, PUT update
/solicitations/api/reviews/              → POST create
/solicitations/api/reviews/<id>/         → GET detail, PUT update
```

## Public Pages & Auth Flow

### Public browse (`/solicitations/`)

- No login required
- Lists solicitations where `is_public=True` and `status="active"`
- Card grid: title, type badge (EOI/RFP), deadline, estimated scale, program name
- Filter by solicitation type
- SEO-friendly titles and meta descriptions

### Public detail (`/solicitations/<pk>/`)

- No login required
- Full details: description, scope of work, questions preview, deadlines
- CTA button: "Respond to this Solicitation"
- Click → redirected to `/labs/login/?next=/solicitations/<pk>/respond/`

### Response flow (new user)

1. Browse public solicitation (no login)
2. Click "Respond"
3. Redirected to Connect OAuth login/signup
4. After auth, lands on response form
5. Select existing LLOEntity or create new one (inline name + short_name fields)
6. Fill out question responses
7. Save draft or submit

### Response flow (existing user)

Same but step 3 is instant (already authenticated). Existing LLOs appear in dropdown at step 5.

### LLO selection/creation in response form

- Dropdown of user's existing LLOEntities
- "+ Create new entity" option reveals inline fields: name, short_name
- New LLO created via API when response is submitted

## Manager & Review Flow

### Manager dashboard (`/solicitations/manage/`)

- Lists solicitations for current user's program
- Table: title, type, status, deadline, response count, actions
- Create button → solicitation form

### Solicitation form

- Fields: title, description, scope_of_work, type, status, deadlines, scale, contact_email, is_public
- Dynamic question builder (Alpine.js): add/remove/reorder questions, set type, mark required
- Question types: text, textarea, number, multiple_choice

### Responses list (`/solicitations/<pk>/responses/`)

- Table: LLO entity name, submitted by, status, date, recommendation, score
- Click → response detail with review option

### Review form (`/solicitations/response/<pk>/review/`)

- Shows response: LLO entity info, all Q&A pairs
- Review fields: score (1-100), recommendation, notes, tags
- One review per reviewer per response (create or update)

## Middleware Considerations

Public pages (`/solicitations/` and `/solicitations/<pk>/`) must bypass `LabsURLWhitelistMiddleware` auth requirement. Add `/solicitations/` to `WHITELISTED_PREFIXES` and handle public vs. authenticated routing within the views.

## MCP Tools

Tools exposed for AI agents via MCP:

- `list_solicitations(status, type, is_public)` → list
- `get_solicitation(id)` → detail
- `create_solicitation(data)` → create (full CRUD, unlike old read-only agent)
- `update_solicitation(id, data)` → update
- `list_responses(solicitation_id, status)` → list
- `get_response(id)` → detail
- `create_response(solicitation_id, llo_entity_id, data)` → create
- `create_review(response_id, data)` → create
- `update_review(id, data)` → update
