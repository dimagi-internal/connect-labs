# Create a solicitation from a micro-plan

**Date:** 2026-06-17
**Status:** Approved (design) — ready for implementation plan

## Problem

The solicitations app lets program staff publish an EOI/RFP, collect responses
from LLOs, review, and award. It has **no notion of geography**. Separately, the
microplans app produces *plans* (each covering a set of named wards) and *plan
groups* (bundles of plans). When a program runs a solicitation over a planned
area, there is no way to (a) tell respondents which geographic units are on
offer, or (b) capture which units each respondent is willing to cover.

This feature lets a user **create a solicitation from a micro-plan or plan
group**, carrying the plans over as a list of selectable "coverage areas," and
lets **respondents select one or more of those plans** as part of their
response. The selection is captured and shown to reviewers; it does not change
the award mechanism in v1.

## Decisions (locked during brainstorming)

1. **Selectable unit = a whole plan.** A plan group lists its member plans; a
   single plan lists just itself. A plan with intervention/control arms is one
   indivisible unit — if a program needs arms split, it splits the plan first.
   Respondents pick plans, never individual wards.
2. **v1 = capture only.** Store each respondent's selected plans and show them
   to reviewers. The award flow is **unchanged** — a whole response is still
   awarded to one org. No per-plan award, no coverage matrix.
3. **Snapshot + back-reference.** At creation the solicitation copies each
   plan's identity and display fields into its own `data`. It also stores the
   origin (`source_program_id`, `source_group_id`, `source_plan_ids`) for
   traceability. Later edits to the live plan do **not** mutate a published
   solicitation. No live re-read, no re-sync button.
4. **Entry points: group page + single-plan review page.** Both get a "Create
   solicitation" action. Pre-fill the solicitation `title` and `scope_of_work`
   from the plan/group name + region; the author edits everything else.
5. **Selection required on submit, optional on draft.** A *submitted* response
   to a solicitation that has plans must select ≥1 plan. *Draft* saves may
   select none.

All four areas are **additive**. A solicitation with no `plans` and a response
with no `selected_plan_ids` behave exactly as they do today.

## Data model

### Solicitation `data` (new fields)

```jsonc
{
  // ...existing fields (title, description, scope_of_work, questions, ...)
  "plans": [
    {
      "plan_id": 123,                                 // int, microplan plan id
      "name": "Ikorodu",                              // str, required
      "region": "Lagos",                              // str, optional ("" if absent)
      "wards": ["Ikorodu North", "Ikorodu South"],    // list[str], display only
      "arms": ["intervention", "control"],            // list[str], omitted if single-arm
      "work_area_count": 42,                          // int, optional
      "population": 50000                             // int, optional (omitted if absent)
    }
  ],
  "source_program_id": 25,        // int — program the plans came from
  "source_group_id": 88,          // int | null — null when created from a single plan
  "source_plan_ids": [123, 124]   // list[int] — traceability
}
```

`plans` is the canonical list. `wards`/`arms`/`population`/`work_area_count` are
denormalized display fields captured at snapshot time; nothing reads them back
from the live plan.

### Response `data` (new fields)

```jsonc
{
  // ...existing fields (solicitation_id, responses, status, ...)
  "selected_plan_ids": [123, 124],                 // list[int]
  "selected_plan_names": ["Ikorodu", "Ikeja"]      // list[str], denormalized for display
}
```

`selected_plan_names` is stored so the responses list can show coverage without
joining back to the solicitation snapshot (and survives if a plan is later
removed from the solicitation).

## Components and changes

### A. Snapshot builder (microplans owns reading its own data)

A new helper in the microplans app — `build_plan_snapshot(da, *, group_id=None,
plan_id=None) -> dict` (returns `{plans, source_program_id, source_group_id,
source_plan_ids, suggested_title, suggested_scope}`). It uses
`ProgramPlanDataAccess` to load the group (`PlanGroupRecord.plan_ids`) or the
single plan, then for each plan reads `name`, `region`, `input_areas` (→ ward
names + arms), and `work_areas` (→ count) to produce the `plans[]` entries.
Solicitations imports this helper rather than reaching into microplan internals
directly, keeping the cross-app boundary at one well-named function.

### B. Solicitation create flow (solicitations app)

- **`SolicitationCreateView.get`** accepts query params
  `source_program_id` + (`source_group_id` *or* `source_plan_id`). When present,
  it calls `build_plan_snapshot(...)`, seeds the form `initial` (`title`,
  `scope_of_work`) and a hidden `plans_json` + source-ref fields.
- **`SolicitationForm`** gains hidden fields: `plans_json`,
  `source_program_id`, `source_group_id`, `source_plan_ids_json`.
  `to_data_dict()` parses `plans_json` into `data["plans"]` and the source refs
  into their typed fields (mirroring how `questions_json` is handled). Empty/
  absent ⇒ the fields are simply omitted from `data` (no `plans` key).
- **`solicitation_form.html`** renders a read-mostly "Coverage areas" panel
  listing each snapshot plan (name, region, ward count, work-area count) with a
  per-row remove control that edits the hidden `plans_json` (same client-side
  pattern as the questions/criteria editors). No add-plan UI — plans only enter
  via the snapshot.

### C. Validation (solicitations/validation.py)

- Add `plans`, `source_program_id`, `source_group_id`, `source_plan_ids` to
  `ALLOWED_FIELDS`.
- Add `_validate_plans(plans)` (called from `validate_solicitation_payload`,
  like `_validate_questions`): list of dicts; per item enforce
  `plan_id:int`, `name:non-empty str`, optional `region:str`,
  `wards:list[str]`, `arms:list[str]`, `work_area_count:int`,
  `population:int`; reject unknown keys; `plan_id` unique within the list.
- Type-check the source refs: `source_program_id`/`source_group_id` int-or-None
  (bool rejected, as with `fund_id`); `source_plan_ids` list[int].

### D. Respondent flow (solicitations app)

- **`SolicitationResponseForm`** gains an optional `plans=` constructor arg.
  When non-empty it adds a `MultipleChoiceField` (`CheckboxSelectMultiple`),
  `required=False`, choices = `[(str(plan_id), label), ...]`. A
  `get_selected_plans(solicitation_plans)` method returns
  `(selected_plan_ids:list[int], selected_plan_names:list[str])` by resolving
  the posted ids against the snapshot.
- **`RespondView`** passes `plans=solicitation.plans` into the form, and in
  `post()` adds the two fields to the response `data`. **Required-on-submit**
  is enforced in the view: if `status == "submitted"`, the solicitation has
  plans, and no plan was selected → add a form error and re-render (do not
  persist). Draft saves skip the check.
- **`respond.html`** renders the plan checkbox group above the dynamic question
  fields, with the ward list shown per plan for context. Hidden entirely when
  the solicitation has no plans.

### E. Display (read-only)

- **`public_detail.html`** — a "Coverage areas" section listing the snapshot
  plans (name, region, wards, work-area count) so respondents see what's on
  offer before responding.
- **`responses_list.html`** — a column / chips showing each response's
  `selected_plan_names`.
- **`response_detail.html`** — the selected plans listed alongside the Q&A.

### F. Proxy model accessors (solicitations/models.py)

- `SolicitationRecord.plans` → `self.data.get("plans", [])` (and `source_*`
  accessors as needed).
- `ResponseRecord.selected_plan_ids` / `.selected_plan_names` →
  `self.data.get(..., [])`.

## Out of scope (v1)

- Per-plan award; coverage/gap matrix across respondents.
- Live re-read or "refresh from plan" re-sync.
- Maps in the picker (ward list text only; no geometry rendered).
- Any microplan-side change beyond the two entry-point buttons and the
  read-only `build_plan_snapshot` helper.
- MCP/HTTP-API parity for the new fields beyond what the shared validator
  already enforces (the validator will accept/reject `plans` uniformly; wiring
  dedicated MCP arguments is a later increment if needed).

## Testing

- **Validator unit tests:** `plans[]` happy path; unknown key rejected; missing
  `name`/`plan_id` rejected; duplicate `plan_id` rejected; source-ref type
  checks; a payload with no `plans` still valid (back-compat).
- **Snapshot builder:** given a stubbed group with N plans → N snapshot entries
  with ward names from `input_areas` and counts from `work_areas`; single-plan
  path yields one entry and `source_group_id=None`.
- **Respondent view:** submit with no plan selected on a plans solicitation →
  form error, nothing persisted; submit with ≥1 → `selected_plan_ids`/`names`
  persisted; draft with none → persisted; solicitation without plans → form has
  no plan field and submit works unchanged.
- **Form round-trip:** `plans_json` hidden field → `to_data_dict()` →
  `data["plans"]` matches; remove-a-plan client edit reflected.
- **Back-compat:** an existing solicitation/response (no new fields) renders and
  submits with no change.

## Risks / notes

- **Cross-app import.** Solicitations importing a microplans helper is the one
  new dependency edge. Confine it to `build_plan_snapshot`; do not let
  solicitations reach into microplan record internals elsewhere.
- **Snapshot staleness is intentional.** A published solicitation's plan list is
  frozen by design (decision 3). If staff need updated plans, they create a new
  solicitation. Surface the snapshot's origin in the UI so this is legible.
- **Labs-record envelope.** All writes flow through
  `SolicitationsDataAccess.create_solicitation/update_solicitation` →
  `validate_solicitation_payload`, so the new fields are validated on every
  path (UI, HTTP API, MCP) without per-caller work.
