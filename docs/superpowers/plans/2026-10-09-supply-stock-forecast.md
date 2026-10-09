# Supply Stock Forecast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only forecast that joins children in treatment (from visits) to stock on hand at every worker and store, and a workflow template that draws it.

**Architecture:** The visit reader keeps each visit's `entity_id`, form identity and the answers a rule's new `cases` block names. A pure-ish service, `stock/services/forecast.py`, reads `WorkerVisit` + standing consumption + `belief.network_tree` at one anchor date and lays need forward week by week. It is exposed as the read operation `stock_forecast` (so the MCP tool `supply_chain_stock_forecast`) and as the workflow supply source `stock_forecast`. The `supply_stock_forecast` template only draws.

**Tech Stack:** Django 5 / Postgres (supply_chain models), pytest, the supply operation registry, workflow supply sources, ES5 + JSX render code (Babel, JSX only).

**Spec:** `docs/superpowers/specs/2026-10-09-supply-stock-forecast-design.md`

## Global Constraints

- Every figure is in the item's single unit (sachets); quantities on the wire as decimal strings or `{amount, unit}` exactly as `belief.wire` gives them.
- Counts sum up the tree; rates and cover are never summed — they are recomputed from summed counts.
- Anchor: the requested `as_of` (or today), moved back to the last case visit when visits stop earlier; the result carries `data_to`.
- Forms are matched the way dispensing lines match them: xmlns or `@name` (stripped). The spec said xmlns; the clone of 2230 carries no `@xmlns`, so name must work too.
- Nothing RUTF-specific in the service. The 150-sachet fallback lives in the template's config and reaches the service as the `course_size` parameter.
- Assumptions on screen are chips, not prose. No explanatory paragraphs.
- Writes nothing. A programme without a `cases` block gets a stock-only forecast and `cases_configured: false`.
- Public repo: every name and figure in tests is invented.

## Review Focus

1. A follow-up for a child whose enrolling Screening predates the rule's `active_from` (carry-over): counted as an open case from its first visit, with what was received so far — test `test_a_child_followed_up_without_an_enrolment_is_an_open_carry_over`.
2. A case whose outcome is an exit, or whose last visit is older than `lost_after_days`: never committed — `test_exited_and_lost_cases_owe_nothing`.
3. A worker with under two full weeks of history: no projected enrolment (`None`, "not enough history"), never zero — `test_too_little_history_projects_no_new_children`.
4. A rejected visit's sachets: not received (standing consumption only) — `test_a_rejected_visits_sachets_are_not_received`.
5. Forecast's on hand and given out disagree with `worker_stock` at the same anchor — `test_numbers_agree_with_worker_stock_and_stock_flow`.

---

## File structure

| File | Responsibility |
|---|---|
| `supply_chain/models.py` + migration `0052_forecast_cases` | `DispensingRule.cases` (JSON), `WorkerVisit.entity_id`, `WorkerVisit.form_xmlns` |
| `supply_chain/stock/services/dispensing.py` | `validate_cases(cases)`, `case_answers(cases, form_json)`, `form_matches(forms, form_json)` |
| `supply_chain/stock/services/visit_reader.py` | stores `entity_id`, `form_xmlns`, case answers on every read |
| `supply_chain/stock/repository.py`, `visit_operations.py`, `serializers.py` | `cases` through upsert, schema, wire |
| `supply_chain/demo/clone_supply.py` | the clone's rule carries a `cases` block |
| `supply_chain/stock/services/forecast.py` (new) | the forecast |
| `supply_chain/stock/visit_operations.py` | `stock_forecast` read operation |
| `workflow/supply_sources.py` | `stock_forecast` source (programme scope, one row) |
| `workflow/templates/supply_stock_forecast.py` + `_render.js` (new), `templates/__init__.py` | the template |
| `CLAUDE.md`, `WORKFLOW_REFERENCE.md` | tool counts, template list, §15 source list |

---

### Task 1: The case rule, and visits that remember their child (PR 1)

**Files:**
- Modify: `connect_labs/supply_chain/models.py` (DispensingRule, WorkerVisit)
- Create: `connect_labs/supply_chain/migrations/0052_forecast_cases.py`
- Modify: `connect_labs/supply_chain/stock/services/dispensing.py`, `visit_reader.py`, `stock/repository.py`, `stock/visit_operations.py`, `serializers.py`, `demo/clone_supply.py`
- Test: `connect_labs/supply_chain/tests/test_forecast_cases.py`

**Interfaces:**
- Produces: `dispensing.validate_cases(cases: dict) -> dict` (raises ValueError naming the problem); `dispensing.form_matches(forms: list[str], form_json: dict) -> bool`; `dispensing.case_answers(cases: dict, form_json: dict) -> dict[path, raw]`; `WorkerVisit.entity_id: str`, `WorkerVisit.form_xmlns: str`; `DispensingRule.cases: dict` shaped
  `{"enrol": {"forms": [...], "path": "form.x", "equals": "yes"}, "outcome": {"path": "form.y", "open": [...], "exit": [...], "complete": [...]}, "lost_after_days": 21}`.

- [ ] **Step 1: Write the failing tests**

```python
CASES = {
    "enrol": {"forms": ["Screening"], "path": "form.screening_outcome.rutf_enrollment", "equals": "yes"},
    "outcome": {"path": "form.case_state.outcome_value", "open": ["enrolled"],
                "exit": ["recovered", "lost_for_follow_up"], "complete": ["recovered"]},
    "lost_after_days": 21,
}

def test_validate_cases_keeps_a_good_block_and_names_a_bad_path():
    assert validate_cases(CASES)["lost_after_days"] == 21
    with pytest.raises(ValueError, match="form_json path"):
        validate_cases({**CASES, "enrol": {**CASES["enrol"], "path": "screening.x"}})

def test_complete_must_be_exits():
    with pytest.raises(ValueError, match="complete"):
        validate_cases({**CASES, "outcome": {**CASES["outcome"], "complete": ["enrolled"]}})

def test_the_reader_remembers_the_child_and_the_case_answers(da, rutf, store):
    _rule(rutf, store, RUTF_LINES, cases=validate_cases(CASES))
    read(da, [visit(9001, name="Screening ", entity_id="child-1",
                    answers={"form.screening_outcome.rutf_enrollment": "yes", RUTF_PATH: "5"})])
    seen = WorkerVisit.objects.get(visit_id="9001")
    assert (seen.entity_id, seen.answers["form.screening_outcome.rutf_enrollment"]) == ("child-1", "yes")

def test_a_re_read_backfills_entity_and_case_answers_on_visits_read_before(da, rutf, store):
    rule = _rule(rutf, store, RUTF_LINES)
    v = visit(9001, entity_id="child-1", answers={"form.case_state.outcome_value": "enrolled", RUTF_PATH: "14"})
    read(da, [v])
    rule.cases = validate_cases(CASES); rule.save()
    read(da, [v])
    seen = WorkerVisit.objects.get(visit_id="9001")
    assert (seen.entity_id, seen.answers["form.case_state.outcome_value"]) == ("child-1", "enrolled")
```

- [ ] **Step 2: Run them; expect ImportError / FieldError.** `make test ARGS="connect_labs/supply_chain/tests/test_forecast_cases.py -q"`
- [ ] **Step 3: Implement.** Model fields (`cases = JSONField(default=dict, blank=True)`; `entity_id = CharField(max_length=255, blank=True, default="", db_index=True)`; `form_xmlns = CharField(max_length=255, blank=True, default="")`), `makemigrations supply_chain -n forecast_cases`. In dispensing: `form_matches` reuses `_form_identity`; `validate_cases` checks paths start `form.`, `open`/`exit` lists of non-blank strings, `complete ⊆ exit`, `lost_after_days` int 1..365 (default 21), `enrol.forms` via `_forms`; `case_answers` returns `{path: raw}` for the enrol path (only when `form_matches(enrol.forms)`) and the outcome path. In the reader's `_remember`, add `entity_id`, `form_xmlns` to `fields`, and merge `case_answers` of every dated rule with `cases` into `answers` on every read (so a re-read backfills). Upsert takes `cases` (validated, kept when omitted); schema `_CASES`; serializer emits `cases`; the clone seed's rule carries `CLONE_CASES` (Screening by name and xmlns).
- [ ] **Step 4: Run the new tests plus `connect_labs/supply_chain -q`; expect PASS** (the steady-state query-count test must still hold: no new per-visit query).
- [ ] **Step 5: Commit** `feat(supply): a dispensing rule can say what a case is; visits remember their child`.

### Task 2: The forecast service (PR 2)

**Files:**
- Create: `connect_labs/supply_chain/stock/services/forecast.py`
- Test: `connect_labs/supply_chain/tests/test_forecast.py`

**Interfaces:**
- Consumes: Task 1 fields; `belief.network_tree(program_id, item, on_date=, window_days=None) -> list[Belief]`; `belief.wire`; `network._expected_inbound(program_id, points, item=, as_of=)`.
- Produces: `forecast.forecast(program_id: int, item: Item, *, opportunity_ids: list[int] | None = None, on_date: date | None = None, horizon_weeks: int = 8, scenario: Decimal = 1, course_size: Decimal | None = None) -> dict` with keys `unit, as_of, anchor, data_to, horizon_weeks, scenario, cases_configured, weeks[{start,end}], basis{course{size,basis,completed_cases}, profile[{week,sachets,basis,cases}], enrolment_weeks, lost_after_days}, history[{start,end,given_out}], programme{...}, cohorts[...], workers[...], stores[...]`.

Algorithm (all Decimal, rounded to 0.1 on the wire):
1. `today = on_date or date.today()`; tree = `belief.network_tree(program_id, item, on_date=today)`; flatten; workers = user_held points in `opportunity_ids` (or all).
2. Rules = active `DispensingRule`s for the item and those opportunities with a non-empty `cases`. None → `cases_configured=False`: each worker's need per week is its belief pace (`amc/30*7`), anchor = today.
3. Visits = `WorkerVisit` of those opportunities with `visit_date <= today`, status not rejected/duplicate, non-blank `entity_id`; `data_to` = their latest date; `anchor = min(today, data_to)`; if anchor < today the tree is re-read at the anchor so on hand and need share one day.
4. Sachets per visit = standing consumption of the item by `visit_id` as of the anchor.
5. Per entity: enrolled_on = first visit whose answers enrol it (else its first visit, `carry_over=True`); last visit; last outcome; received = Σ sachets. Open = outcome in `open` or blank, and `(anchor - last).days <= lost_after_days`. Worker = point of its latest visit.
6. Course: median received among `complete` cases when ≥ `K_MIN`(5) → `measured`; else `commodity.course_definition.base_units_per_course` → `protocol`; else the `course_size` parameter → `default`; else `unknown` (committed and projected are `None`).
7. Profile week k: Σ sachets in treatment week k ÷ cases observable for the whole week and still in treatment at its start, when ≥ K_MIN → `measured`; else `course_definition.base_units_per_day × 7` → `protocol`; else the last measured week → `steady`; else `course / 10` → `default`.
8. Committed per open case: walk future week i, treatment week k = `(week_start - enrolled_on).days // 7`, take `min(profile[k], left)`.
9. Enrolment per worker: enrolments in each of the last 3 seven-day windows ending at the anchor that fall on/after the rule's `active_from`; < 2 such windows → `None`. Projected: a cohort of `rate × scenario` children enrols in each future week i and follows the profile until the course is used.
10. Need per worker per week = committed + projected. Runs dry = first day cumulative need exceeds on hand (linear within the week; on hand ≤ 0 → the anchor). Shortfall cumulative = `max(0, cum_need - on_hand)`.
11. Stores bottom-up: demand = Σ children's cumulative shortfall; own on hand from the store's own Belief; runs dry when demand exceeds it; subtree need = Σ worker need (counts). Programme: Σ root shortfall against inbound by `expected_on` inside the horizon (overdue or undated listed, not counted).
12. History: given out per seven-day window, the last 8 before the anchor, programme total and per worker.

- [ ] **Step 1: Write the failing tests** (world: one store, two workers, a Screening enrolling `child-a` on day −20 with 10 sachets and weekly Visit Forms of 14; `child-b` exited `recovered`; `child-c` last seen 30 days ago; `child-d` followed up with no Screening):

```python
def test_open_cases_and_what_they_are_owed(world):
    out = forecast.forecast(PROGRAM, world.item, on_date=TODAY, course_size=Decimal(150))
    acacia = _worker(out, "worker-acacia")
    assert acacia["open_cases"] == 2  # child-a and the carry-over child-d
    assert Decimal(acacia["owed"]) == Decimal(150) - Decimal(38) + Decimal(150) - Decimal(14)

def test_exited_and_lost_cases_owe_nothing(world): ...   # child-b, child-c not counted
def test_a_child_followed_up_without_an_enrolment_is_an_open_carry_over(world): ...
def test_course_size_falls_back_measured_protocol_default(world): ...
def test_too_little_history_projects_no_new_children(world): ...
def test_scenario_zero_leaves_committed_only(world): ...
def test_runs_dry_on_is_the_first_day_need_passes_on_hand(world): ...
def test_a_store_runs_dry_when_its_workers_shortfall_passes_its_own_stock(world): ...
def test_inbound_counts_at_the_programme_only(world): ...
def test_visits_that_stop_early_move_the_anchor_back_and_say_so(world): ...
def test_without_a_case_rule_the_forecast_is_pace_only(world): ...
def test_a_rejected_visits_sachets_are_not_received(world): ...
def test_numbers_agree_with_worker_stock_and_stock_flow(world): ...
```
(Each test's body asserts the exact figure the world above produces; written out in `test_forecast.py`.)
- [ ] **Step 2: Run; expect ImportError.**
- [ ] **Step 3: Implement `forecast.py` per the algorithm.**
- [ ] **Step 4: Run `connect_labs/supply_chain -q`; PASS.**
- [ ] **Step 5: Commit** `feat(supply): forecast stock against children in treatment and new enrolments`.

### Task 3: `stock_forecast` operation and supply source (PR 2)

**Files:**
- Modify: `connect_labs/supply_chain/stock/visit_operations.py`, `connect_labs/workflow/supply_sources.py`, `CLAUDE.md` (registry 116 → 117, generated 112 → 113, tools 259 → 260)
- Test: `connect_labs/supply_chain/tests/test_forecast.py` (op), `connect_labs/workflow/tests/test_supply_sources.py` (source)

**Interfaces:**
- Produces: operation `stock_forecast(access, item_id, opportunity_ids=None, as_of=None, horizon_weeks=8, scenario="1", course_size=None)`; source `"stock_forecast": Source("stock_forecast", PROGRAM, "*", item=True, as_of=True, args=("scenario", "horizon_weeks"), params=("course_size", "horizon_weeks"))`, with the workflow's opportunities of that programme passed as `opportunity_ids`.

- [ ] **Step 1: Failing tests**

```python
def test_the_operation_is_a_read_on_the_mcp_catalogue():
    op = get_operation("stock_forecast")
    assert (op.is_write, op.internal) == (False, False)

def test_a_forecast_source_runs_once_per_programme_for_the_workflows_opportunities(world, request_for):
    out = supply_sources.run(request_for(), SimpleNamespace(data={}),
                             {"alias": "f", "source": "stock_forecast", "item": "rutf"},
                             opportunity_ids=[OPP_A, OPP_B])
    assert len(out["rows"]) == 1
    assert {w["opportunity_id"] for w in out["rows"][0]["workers"]} == {OPP_A, OPP_B}
```
- [ ] **Step 2: Run; FAIL.** **Step 3:** register the op (schema: item_id, opportunity_ids array of ID, as_of, horizon_weeks 1..26, scenario number 0..3, course_size number > 0); add the source and pass `opportunity_ids` for it in `run`. **Step 4:** PASS. **Step 5:** commit.

### Task 4: The `supply_stock_forecast` template (PR 3)

**Files:**
- Create: `connect_labs/workflow/templates/supply_stock_forecast.py`, `supply_stock_forecast_render.js`
- Modify: `connect_labs/workflow/templates/__init__.py` (category `reports`), `CLAUDE.md` (39 → 40 templates, list), `WORKFLOW_REFERENCE.md` §15
- Test: `connect_labs/workflow/tests/test_supply_sources.py::test_the_forecast_template_declares_valid_sources`; the parser test already covers every shipped template.

The render draws, in order: the programme line (weekly given out solid, then committed + new stacked, the network on-hand line, inbound steps, "runs dry week of X"); children in treatment by enrolment week; workers soonest dry first (rows expand to the worker's own weekly bars; phone rows stack below `sm`, as #2350); stores; assumption chips and a scenario control (50–150 %) that calls `actions.querySupply('forecast', {args: {scenario}})`. Classes only from those `supply_stock_review_render.js` already uses; colours inline in SVG.

- [ ] **Step 1:** failing template test. **Step 2:** FAIL. **Step 3:** write the template + render; transpile the render with the repo's Babel (`npx babel --presets @babel/preset-react`) to prove it parses. **Step 4:** PASS. **Step 5:** commit.

### Task 5: Review, merge, post-deploy steps

- [ ] One fresh reviewer subagent over the whole diff against the spec; fix what is real.
- [ ] Merge PRs 1–3 in order (`gh pr merge --squash --admin` after local tests + pre-commit), then #2368.
- [ ] Post-deploy (not run here): upsert the clone rule's `cases` via `supply_chain_dispensing_rule_upsert`; the hourly reader backfills `entity_id`; create the workflow on 10113; pin it with `supply_chain_view_pin`.
