# Supply: Stock From Visits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn each submitted Connect visit into `consumption` leaving the field worker's own supply point, so that every level of the network, down to the individual worker, has an honest answer to "how much does this worker hold, and how long will it last?", with the unapproved and estimated parts shown on every figure.

**Architecture:** Visit consumption is posted to the existing append-only ledger (`Movement`), and reversals net it back out. A per-opportunity `DispensingRule` maps form paths to quantities. It is editable over MCP and on a web form without a deploy. An `internal=True` operation (`visit_consumption_ingest`) reads visits through `fetch_raw_visits`/`get_export_client`, so synthetic fixtures are read exactly as a real export would be. A beat task and a management command both call it. A mutable, revisioned `WorkerVisit` row holds each visit's current status, which the append-only ledger cannot carry. `stock/services/belief.py` computes every worker's and every store's figures in grouped SQL whose query count does not grow with the number of workers. The Network, Workers and Worker screens and the portfolio map read those figures through operations and honour `?as_of=`.

**Tech Stack:** Django 5 / Postgres, jsonschema operation registry (`supply_chain/operations.py`), pytest + pytest-django, Celery beat (`django_celery_beat` DatabaseScheduler), server-rendered templates (Tailwind), inline SVG (no chart library is loaded on supply pages), vitest for one small JS helper.

**Spec:** `docs/superpowers/specs/2026-09-28-supply-stock-from-visits-design.md`. Read it alongside this plan. The plan argues from the spec, and the "Deviations" section at the end lists every place it departs from the spec, with the reason.

## Global Constraints

- **Synthetic only.** Nothing reads or writes a real programme's supply data "until the product owner says otherwise" (spec §1). `visit_consumption_ingest` refuses any programme for which `scopes.is_synthetic(program_id)` is false, and the beat task skips them.
- **Every write goes through `call_operation`.** Each run is one attributed `OperationCall`, and its rows carry `Revision`s (spec §4, #2075). No `bulk_create` on any tracked model: a bulk insert sends no `post_save`, so history would miss it.
- **The ledger is append-only.** `quantity > 0` except `kind="adjustment"` (the DB `CheckConstraint`). A reversal is a `consumption` row **into** the worker's point, with `reverses` set (spec §3.2). No `Movement` is ever updated.
- **`source="connect_visit"`** on every movement and every auto-created point that the reader posts (spec §3.2).
- **Worker points:** `kind="user_held"`, parent = the rule's `resupply_point`, slug `user-{opp}-{username}` (spec §3.3). The worker is matched on `(opportunity_id, connect_username)` first and `connect_user_uuid` second.
- **A missing answer is unknown, never zero** (spec §2). A protocol-derived quantity is **estimated**, and every figure it contributes to says so (spec §2, §6).
- **No ranking of workers** (spec §2, §22 of the September design). The Workers page has no default "worst first" order. It sorts by name until the reader picks a column.
- **Counts sum up the hierarchy. Cover and rates are recomputed at each level and never summed** (spec §5).
- **`?as_of=` works on every new page**, and write controls are hidden under it (spec §6, §8).
- **This repo is public:** use invented names only in fixtures, seeders and tests (spec §7). No real usernames, no programme 263 data, no PII.
- **Migrations:** new columns on existing tables get `db_default` as well as `default`, because an old task in a rolling deploy inserts without naming the column (see the `SupplyPoint.location_source` comment in `models.py`).
- **ORS and zinc rules stay off** until the programme settles 4 sachets versus 2 co-packs (spec §9).
- **MCP:** new agent operations appear as `supply_chain_<name>` tools automatically. `visit_consumption_ingest` is `internal=True` and must **not** appear.
- Run tests with `make test ARGS="<path> -q"` (worktree-safe). Commit with `make commit` or `PATH=/Users/haldimagi/emdash/repositories/connect-labs/.venv/bin:$PATH git commit`.

## Review Focus

These are the five inputs the spec implies but never tests, and which are most likely to bite someone using this. Each has a pinning test in the task that owns the code.

1. **A stated answer that is not a usable quantity** (`"two"`, `"-3"`, `"NaN"`, `""`, `true`). Expected: the item is `no_answer` for that visit, with a reason naming the path and the raw answer. The reader never posts a zero, a negative or a guess. *Test: Task 3 `test_an_unreadable_answer_is_unknown_not_zero`.*
2. **A visit rejected, reversed, then reinstated** (status goes back to `approved`/`pending`). A person would expect the consumption to come back. The one-consumption-plus-one-reversal constraints make a re-post impossible, so the reader reports the visit as `reinstated_after_reversal` and writes nothing. A human has to see it. *Test: Task 5 `test_a_visit_reinstated_after_reversal_is_reported_not_reposted`.*
3. **A worker whose username changes while their Connect user id stays the same.** Expected: they resolve to the same supply point through the UUID, and no phantom second worker is created. *Test: Task 4 `test_a_renamed_worker_is_found_by_uuid`.*
4. **A rule edited after visits were already posted** (a path fixed, or a protocol quantity changed). Expected: visits already posted are not re-evaluated. The new lines apply to visits read from then on, and the report counts the skips. *Test: Task 5 `test_editing_a_rule_does_not_repost_old_visits`.*
5. **A visit dated in the future, or with no parseable date or id.** Expected: it is skipped and counted (`future`, `undated`), and it never lands in the ledger on a date that has not happened. *Test: Task 5 `test_future_and_undated_visits_are_skipped`.*

---

## File Structure

| Path | Responsibility | Task |
|---|---|---|
| `connect_labs/supply_chain/models.py` (modify) | `Movement.visit_id/reverses/estimated` + constraints; `MovementQuerySet.standing_consumption`; `DispensingRule`; `SupplyPoint.connect_user_uuid`; `WorkerVisit` | 1, 2, 4, 5 |
| `connect_labs/supply_chain/records.py` (modify) | `reported_receipt` count kind, `ON_HAND_COUNT_KINDS` | 6 |
| `connect_labs/supply_chain/serializers.py` (modify) | wire for new fields, `dispensing_rule` | 1, 2, 4 |
| `connect_labs/supply_chain/operations.py` (modify) | register serializers for new models | 2 |
| `connect_labs/supply_chain/stock/services/posting.py` (modify) | `post_visit_consumption`, `post_visit_reversal` | 1 |
| `connect_labs/supply_chain/stock/services/resupply.py` (modify) | reversal netting; extract `rate_from` and `cover` so belief shares one rule | 1, 7 |
| `connect_labs/supply_chain/stock/services/dispensing.py` (create) | rule validation (T2), visit evaluation (T3), report reading (T6), all pure | 2, 3, 6 |
| `connect_labs/supply_chain/stock/services/workers.py` (create) | `WorkerIndex`: resolve or create worker points in memory | 4 |
| `connect_labs/supply_chain/stock/services/visit_source.py` (create) | the one place visits are read (`AnalysisPipeline.fetch_raw_visits`) | 5 |
| `connect_labs/supply_chain/stock/services/visit_reader.py` (create) | the reader: post, reverse, remember, report; beat entry point | 5, 6 |
| `connect_labs/supply_chain/stock/services/ingest.py` (modify) | `extract_rows` reads `form_json`; kind-aware idempotency | 6 |
| `connect_labs/supply_chain/stock/services/soh.py`, `network.py` (modify) | ignore `reported_receipt` as an on-hand count; `_latest_counts(on_date)` | 6, 7 |
| `connect_labs/supply_chain/stock/services/belief.py` (create) | per-point figures and subtree roll-up in grouped SQL | 7 |
| `connect_labs/supply_chain/stock/services/timeline.py` (create) | a worker's day-by-day timeline, and its inline SVG | 8 |
| `connect_labs/supply_chain/stock/repository.py` (modify) | rule CRUD; strip visit-only fields from `record_movement` | 1, 2 |
| `connect_labs/supply_chain/stock/visit_operations.py` (create) | `dispensing_rule_*`, `visit_consumption_ingest`, `worker_stock`, `network_tree`, `worker_stock_get` | 2, 5, 7, 8 |
| `connect_labs/supply_chain/stock/operations.py` (modify) | `stock_count_record` kinds restricted to on-hand kinds | 6 |
| `connect_labs/supply_chain/apps.py`, `mcp_tools.py` (modify) | import `visit_operations` so it registers | 2 |
| `connect_labs/supply_chain/data_access.py` (modify) | purge severs `reverses`, drops rules and worker visits | 2, 5 |
| `connect_labs/supply_chain/history/program.py` (modify) | program paths for `DispensingRule`, `WorkerVisit` | 2, 5 |
| `connect_labs/supply_chain/stock/dispensing_forms.py` (create) | web form for a rule | 2 |
| `connect_labs/supply_chain/stock/visit_views.py` (create) | rule list/create/edit; Workers; Worker detail | 2, 8 |
| `connect_labs/supply_chain/network/views.py` (modify) | network tree | 8 |
| `connect_labs/supply_chain/urls.py`, `navigation.py` (modify) | routes, tabs | 2, 8 |
| `connect_labs/templates/supply_chain/{dispensing_rules,workers,worker_detail}.html` (create), `network.html` (modify) | screens | 2, 8 |
| `connect_labs/static/supply_chain/marker_size.js` (+ `.test.js`) (create), `portfolio_map.js`, `portfolio_map.html` (modify) | worker marker sized by on-hand | 8 |
| `connect_labs/supply_chain/management/commands/supply_ingest_visit_consumption.py` (create) | operator entry point | 5 |
| `connect_labs/supply_chain/management/commands/supply_ingest_stock_reports.py` (modify) | read through `visit_source` | 6 |
| `connect_labs/supply_chain/tasks.py` (modify) | beat task | 5 |
| `connect_labs/supply_chain/migrations/0035…0040` (create) | schema + periodic task | 1, 2, 4, 5, 6 |
| `connect_labs/supply_chain/demo/{__init__,stock_from_visits}.py`, `demo/README.md` (create); `management/commands/supply_seed_stock_from_visits.py` (create) | synthetic world | 9 |
| `CLAUDE.md` (modify) | app map line, MCP tool counts | 10 |

Migration numbers: `0035_movement_from_visits` (T1), `0036_dispensing_rule` (T2), `0037_supply_point_connect_user_uuid` (T4), `0038_worker_visit` (T5), `0039_seed_visit_consumption_beat_task` (T5), `0040_stock_count_reported_receipt` (T6). Task 3 has no migration. If `main` has taken 0035 by the time you start, renumber and fix each `dependencies` line.

---

### Task 1: Visit fields on `Movement`, reversal posting, ledger netting

**Files:**
- Modify: `connect_labs/supply_chain/models.py` (`MovementQuerySet` ~1167–1238, `Movement` ~1241–1311)
- Modify: `connect_labs/supply_chain/stock/services/posting.py` (append)
- Modify: `connect_labs/supply_chain/stock/services/resupply.py` (`_demand` ~110–119)
- Modify: `connect_labs/supply_chain/stock/repository.py` (`record_movement` ~113–147)
- Modify: `connect_labs/supply_chain/serializers.py` (`movement` ~455)
- Create: `connect_labs/supply_chain/migrations/0035_movement_from_visits.py` (generated)
- Test: `connect_labs/supply_chain/tests/test_visit_movements.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `Movement.visit_id: str` (""), `Movement.reverses: Movement | None` (OneToOne, reverse accessor `movement.reversal`), `Movement.estimated: bool`
  - `MovementQuerySet.standing_consumption() -> MovementQuerySet`: consumption that is not a reversal and has not been reversed
  - `MovementQuerySet.consumption_by_unit()` now nets reversals out
  - `posting.post_visit_consumption(*, program_id, opportunity_id, point, item, quantity: Decimal, unit: str, occurred_on: date, visit_id: str, estimated: bool) -> Movement`
  - `posting.post_visit_reversal(original: Movement, *, reason: str) -> Movement` raises `ValueError` if `original` is not a visit's consumption
  - `posting.VISIT_ONLY_FIELDS = frozenset({"visit_id", "reverses", "reverses_id", "estimated"})`
  - wire `movement` gains `visit_id`, `estimated`, `reverses_movement_id`

- [ ] **Step 1: Write the failing tests**

Create `connect_labs/supply_chain/tests/test_visit_movements.py`:

```python
"""Consumption posted from a visit, and the reversal that cancels it.

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented.

A reversal is a `consumption` INTO the worker's point naming the row it
cancels (design §3.2). These tests pin that the ledger, the consumption
total and the monthly rate all read the pair as nothing having happened.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, Item, Movement, SupplyPoint
from connect_labs.supply_chain.operations import call_operation, record
from connect_labs.supply_chain.stock.services import ledger, posting, resupply
from connect_labs.supply_chain.values import Quantity

pytestmark = pytest.mark.django_db

PROGRAM = 10511
OPP = 10511
TODAY = date(2026, 9, 28)


@pytest.fixture
def item():
    rutf = Commodity.objects.create(
        scope_key=f"prog:{PROGRAM}",
        slug="rutf",
        name="RUTF",
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
    )
    return Item.objects.create(
        scope_key=f"prog:{PROGRAM}",
        sku="rutf-150",
        name="RUTF 150",
        commodity=rutf,
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
    )


@pytest.fixture
def store():
    return SupplyPoint.objects.create(
        program_id=PROGRAM, slug="partner-store", name="Partner store", kind="regional_store", source="we_recorded"
    )


@pytest.fixture
def worker(store):
    return SupplyPoint.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        slug="user-10511-worker-acacia",
        name="worker-acacia",
        kind="user_held",
        connect_username="worker-acacia",
        parent=store,
        source="connect_visit",
    )


def _issue(item, store, worker, sachets, on):
    return Movement.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        kind="distribution",
        occurred_on=on,
        from_supply_point=store,
        to_supply_point=worker,
        item=item,
        commodity=item.commodity,
        quantity=Decimal(sachets),
        quantity_unit="sachet",
        source="we_recorded",
    )


def _dispense(item, worker, sachets, on, visit_id, estimated=False):
    return posting.post_visit_consumption(
        program_id=PROGRAM,
        opportunity_id=OPP,
        point=worker,
        item=item,
        quantity=Decimal(str(sachets)),
        unit="sachet",
        occurred_on=on,
        visit_id=visit_id,
        estimated=estimated,
    )


def test_a_visit_posts_consumption_out_of_the_worker(item, store, worker):
    _issue(item, store, worker, 100, TODAY - timedelta(days=10))
    movement = _dispense(item, worker, 14, TODAY - timedelta(days=2), "9001")

    assert (movement.kind, movement.source, movement.visit_id) == ("consumption", "connect_visit", "9001")
    assert (movement.from_supply_point_id, movement.to_supply_point_id) == (worker.pk, None)
    assert ledger.balance(PROGRAM, worker, item=item) == Quantity(Decimal("86"), "sachet")


def test_a_reversal_puts_the_stock_back_and_names_what_it_cancels(item, store, worker):
    _issue(item, store, worker, 100, TODAY - timedelta(days=10))
    original = _dispense(item, worker, 14, TODAY - timedelta(days=2), "9001")

    reversal = posting.post_visit_reversal(original, reason="visit rejected")

    assert reversal.reverses_id == original.pk
    assert (reversal.from_supply_point_id, reversal.to_supply_point_id) == (None, worker.pk)
    assert reversal.kind == "consumption" and reversal.visit_id == "9001"
    # Dated with the visit it cancels, so a monthly rate over any window nets exactly.
    assert reversal.occurred_on == original.occurred_on
    assert ledger.balance(PROGRAM, worker, item=item) == Quantity(Decimal("100"), "sachet")


def test_consumption_by_unit_nets_reversals_out(item, store, worker):
    rejected = _dispense(item, worker, 14, TODAY, "9001")
    _dispense(item, worker, 10, TODAY, "9002")
    posting.post_visit_reversal(rejected, reason="visit rejected")

    assert Movement.objects.for_program(PROGRAM).consumption_by_unit() == {"sachet": Decimal("10")}


def test_average_monthly_consumption_nets_reversals_out(item, store, worker):
    _issue(item, store, worker, 500, TODAY - timedelta(days=40))
    _dispense(item, worker, 30, TODAY - timedelta(days=35), "9001")
    rejected = _dispense(item, worker, 60, TODAY - timedelta(days=5), "9002")
    posting.post_visit_reversal(rejected, reason="visit rejected")

    amc = resupply.average_monthly_consumption(PROGRAM, worker, item=item, as_of=TODAY)

    # 30 sachets over the 36 days since the first dispensing that stands, per 30 days.
    assert amc == Quantity(Decimal("25.0000"), "sachet")


def test_one_consumption_per_visit_and_item(item, store, worker):
    _dispense(item, worker, 14, TODAY, "9001")
    with pytest.raises(IntegrityError), transaction.atomic():
        _dispense(item, worker, 5, TODAY, "9001")


def test_one_reversal_per_consumption(item, store, worker):
    original = _dispense(item, worker, 14, TODAY, "9001")
    posting.post_visit_reversal(original, reason="visit rejected")
    with pytest.raises(IntegrityError), transaction.atomic():
        posting.post_visit_reversal(original, reason="again")


def test_a_reversal_must_come_back_into_a_point(item, store, worker):
    original = _dispense(item, worker, 14, TODAY, "9001")
    with pytest.raises(IntegrityError), transaction.atomic():
        Movement.objects.create(
            program_id=PROGRAM,
            kind="consumption",
            occurred_on=TODAY,
            from_supply_point=worker,
            item=item,
            commodity=item.commodity,
            quantity=Decimal("14"),
            quantity_unit="sachet",
            visit_id="9001",
            reverses=original,
            source="connect_visit",
        )


def test_negative_consumption_is_still_refused(item, worker):
    with pytest.raises(IntegrityError), transaction.atomic():
        Movement.objects.create(
            program_id=PROGRAM,
            kind="consumption",
            occurred_on=TODAY,
            from_supply_point=worker,
            item=item,
            commodity=item.commodity,
            quantity=Decimal("-3"),
            quantity_unit="sachet",
            source="connect_visit",
        )


def test_only_a_visits_consumption_can_be_reversed(item, store, worker):
    issue = _issue(item, store, worker, 100, TODAY)
    with pytest.raises(ValueError, match="only a visit's consumption"):
        posting.post_visit_reversal(issue, reason="nope")


def test_movement_record_cannot_forge_a_visit_or_a_reversal(item, store, worker):
    original = _dispense(item, worker, 14, TODAY, "9001")
    da = SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)

    result = call_operation(
        "movement_record",
        da,
        {
            "data": {
                "kind": "loss",
                "occurred_on": TODAY.isoformat(),
                "from_supply_point_id": worker.pk,
                "commodity_slug": "rutf",
                "item_id": item.pk,
                "quantity": "1",
                "quantity_unit": "sachet",
                "source": "we_recorded",
                "visit_id": "9001",
                "reverses_id": original.pk,
                "estimated": True,
            }
        },
    )

    saved = Movement.objects.get(pk=result["id"])
    assert (saved.visit_id, saved.reverses_id, saved.estimated) == ("", None, False)


def test_the_wire_says_where_a_movement_came_from(item, store, worker):
    original = _dispense(item, worker, 14, TODAY, "9001", estimated=True)
    reversal = posting.post_visit_reversal(original, reason="visit rejected")

    wire = record(reversal)

    assert wire["visit_id"] == "9001"
    assert wire["estimated"] is True
    assert wire["reverses_movement_id"] == original.pk
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_visit_movements.py -q"`
Expected: FAIL with `AttributeError: module ... posting has no attribute 'post_visit_consumption'`.

- [ ] **Step 3: Add the fields and constraints to `Movement`**

In `connect_labs/supply_chain/models.py`, after `stock_count = models.ForeignKey(...)` in `Movement`, add:

```python
    # Set on a movement the stock reader posted from a Connect visit. The
    # consumption a visit caused and the reversal that cancels it both carry
    # it, which is what makes re-reading the same visits write nothing.
    visit_id = models.CharField(max_length=64, blank=True, default="", db_default="", db_index=True)
    # A reversal: a `consumption` INTO the worker's point cancelling the row
    # named here (design 2026-09-28 §3.2). Never an edit -- the ledger stays
    # append-only. One-to-one, so a movement can be reversed at most once.
    reverses = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal"
    )
    # True when any part of the quantity came from a protocol -- the form said
    # only THAT something was given -- rather than a number somebody entered.
    estimated = models.BooleanField(default=False, db_default=False)
```

Append to `Movement.Meta.constraints`:

```python
            # Re-reading a visit writes nothing: one consumption and at most
            # one reversal per visit and item. Scoped by program because a
            # synthetic programme's invented visit ids may repeat another's.
            models.UniqueConstraint(
                fields=["program_id", "visit_id", "item"],
                condition=~Q(visit_id="") & Q(reverses__isnull=True),
                name="movement_one_consumption_per_visit_item",
            ),
            models.UniqueConstraint(
                fields=["program_id", "visit_id", "item"],
                condition=~Q(visit_id="") & Q(reverses__isnull=False),
                name="movement_one_reversal_per_visit_item",
            ),
            # A reversal puts stock back: a consumption into a point, from nowhere.
            models.CheckConstraint(
                condition=Q(reverses__isnull=True)
                | Q(kind="consumption", from_supply_point__isnull=True, to_supply_point__isnull=False),
                name="movement_reversal_is_consumption_back_in",
            ),
```

Replace `MovementQuerySet.consumption_by_unit` with:

```python
    def standing_consumption(self):
        """Consumption that still stands: not a reversal, and not reversed.

        A reversal carries the visit's date, so dropping the pair nets exactly
        over any window -- the same as subtracting one from the other.
        """
        return self.filter(kind="consumption", reverses__isnull=True, reversal__isnull=True)

    def consumption_by_unit(self):
        """{quantity_unit: Decimal} dispensed, net of reversals. The input to average monthly consumption."""
        return {unit[0]: total for unit, total in self.standing_consumption()._totals(["quantity_unit"]).items()}
```

- [ ] **Step 4: Net reversals out of the monthly rate**

In `connect_labs/supply_chain/stock/services/resupply.py`, change `_demand`:

```python
def _demand(program_id, supply_point, basis, item=None):
    qs = Movement.objects.for_program(program_id).filter(from_supply_point=supply_point)
    if basis == CONSUMPTION:
        # A reversed visit never happened as far as the rate is concerned.
        qs = qs.filter(kind="consumption", reversal__isnull=True)
    else:
        qs = qs.filter(kind__in=RELEASE_KINDS, to_supply_point__isnull=False).exclude(to_supply_point=supply_point)
    if item is not None:
        qs = qs.filter(item=item)
    return qs
```

- [ ] **Step 5: Add the posting helpers**

Append to `connect_labs/supply_chain/stock/services/posting.py`:

```python
# Fields only the stock reader sets. A movement typed by a person or an agent
# through movement_record must never claim to be a visit or to reverse one.
VISIT_ONLY_FIELDS = frozenset({"visit_id", "reverses", "reverses_id", "estimated"})


def post_visit_consumption(
    *, program_id, opportunity_id, point, item, quantity, unit, occurred_on, visit_id, estimated
) -> Movement:
    """What one visit gave out of one item, leaving the worker's own stock."""
    movement = Movement(
        program_id=program_id,
        opportunity_id=opportunity_id,
        kind="consumption",
        occurred_on=occurred_on,
        from_supply_point=point,
        item=item,
        commodity=item.commodity,
        quantity=quantity,
        quantity_unit=unit,
        visit_id=str(visit_id),
        estimated=estimated,
        reference=f"visit {visit_id}"[:64],
        source="connect_visit",
    )
    movement.save()
    return movement


def post_visit_reversal(original: Movement, *, reason: str) -> Movement:
    """Cancel a visit's consumption: the same quantity back into the same point.

    Dated with the visit, not with the day the rejection was read, so a
    monthly rate over any window loses the pair exactly. What the page showed
    on a past day is reproduced by the as-of rewind (history/rewind.py), which
    removes this row for any date before it was recorded.
    """
    if original.kind != "consumption" or original.from_supply_point_id is None or not original.visit_id:
        raise ValueError("only a visit's consumption can be reversed; correct anything else with an adjustment")
    movement = Movement(
        program_id=original.program_id,
        opportunity_id=original.opportunity_id,
        kind="consumption",
        occurred_on=original.occurred_on,
        to_supply_point_id=original.from_supply_point_id,
        item_id=original.item_id,
        commodity_id=original.commodity_id,
        quantity=original.quantity,
        quantity_unit=original.quantity_unit,
        visit_id=original.visit_id,
        estimated=original.estimated,
        reverses=original,
        reference=f"reverses visit {original.visit_id}"[:64],
        note=reason,
        source="connect_visit",
    )
    movement.save()
    return movement
```

- [ ] **Step 6: Stop `movement_record` from setting visit-only fields**

In `connect_labs/supply_chain/stock/repository.py`, at the top of `record_movement`, after the local imports:

```python
        from connect_labs.supply_chain.stock.services.posting import VISIT_ONLY_FIELDS

        data = {key: value for key, value in data.items() if key not in VISIT_ONLY_FIELDS}
```

- [ ] **Step 7: Put the new fields on the wire**

In `connect_labs/supply_chain/serializers.py` `movement(obj)`, after `"reference": obj.reference,` add:

```python
        "visit_id": obj.visit_id,
        "estimated": obj.estimated,
        "reverses_movement_id": obj.reverses_id,
```

- [ ] **Step 8: Generate the migration**

Run: `make manage CMD="makemigrations supply_chain --name movement_from_visits"`
Expected: creates `connect_labs/supply_chain/migrations/0035_movement_from_visits.py` with three `AddField` (`visit_id` carrying `db_default=""`, `reverses`, `estimated` carrying `db_default=False`) and three `AddConstraint`. Then run `make manage CMD="makemigrations supply_chain --check --dry-run"`. Expected: `No changes detected`.

- [ ] **Step 9: Run the tests to verify they pass, plus the stock suites that share the ledger**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_visit_movements.py connect_labs/supply_chain/tests/test_stock.py connect_labs/supply_chain/tests/test_stock_operations.py connect_labs/supply_chain/tests/test_serialisers_produce_json.py -q"`
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add connect_labs/supply_chain/models.py connect_labs/supply_chain/stock/services/posting.py \
  connect_labs/supply_chain/stock/services/resupply.py connect_labs/supply_chain/stock/repository.py \
  connect_labs/supply_chain/serializers.py connect_labs/supply_chain/migrations/0035_movement_from_visits.py \
  connect_labs/supply_chain/tests/test_visit_movements.py
make commit  # message: "feat(supply): a visit's consumption and its reversal on the ledger"
```

---
### Task 2: `DispensingRule`, its operations, validation and web form

**Files:**
- Modify: `connect_labs/supply_chain/models.py` (new model after `DistributionLine`)
- Create: `connect_labs/supply_chain/stock/services/dispensing.py`
- Create: `connect_labs/supply_chain/stock/visit_operations.py`
- Modify: `connect_labs/supply_chain/stock/repository.py` (rule CRUD)
- Modify: `connect_labs/supply_chain/serializers.py`, `connect_labs/supply_chain/operations.py` (`_SERIALIZERS`)
- Modify: `connect_labs/supply_chain/apps.py`, `connect_labs/supply_chain/mcp_tools.py` (import `visit_operations`)
- Modify: `connect_labs/supply_chain/history/program.py` (`PATHS`)
- Modify: `connect_labs/supply_chain/data_access.py` (`purge`)
- Create: `connect_labs/supply_chain/stock/dispensing_forms.py`, `connect_labs/supply_chain/stock/visit_views.py`, `connect_labs/templates/supply_chain/dispensing_rules.html`
- Modify: `connect_labs/supply_chain/urls.py`, `connect_labs/supply_chain/navigation.py`
- Create: `connect_labs/supply_chain/migrations/0036_dispensing_rule.py` (generated)
- Test: `connect_labs/supply_chain/tests/test_dispensing_rules.py`

**Interfaces:**
- Consumes: `ledger._pack_spec`, `ledger.convert` (existing).
- Produces:
  - model `DispensingRule(program_id, opportunity_id, item, lines, forms, reports, resupply_point, active_from, status)`, unique `(program_id, opportunity_id, item)`
  - `dispensing.base_unit(item) -> str` (raises `ValueError`)
  - `dispensing.validate_lines(lines: list[dict], item) -> list[dict]` (normalised; quantities as decimal strings)
  - `dispensing.validate_reports(reports: dict | None) -> dict` (Task 6 reads `balance_paths`, `receipt.quantity_paths`, `receipt.date_paths`)
  - `StockRepositoryMixin.list_dispensing_rules(opportunity_id=None, include_inactive=False)`, `.get_dispensing_rule(rule_id)`, `.upsert_dispensing_rule(data)`
  - operations `dispensing_rule_list`, `dispensing_rule_get`, `dispensing_rule_upsert` (write)
  - wire `dispensing_rule`: `{id, opportunity_id, item_id, item_name, commodity_slug, unit, lines, forms, reports, resupply_supply_point_id, active_from, status, estimated}`
  - URL names `supply_chain:dispensing_rules`, `supply_chain:dispensing_rule_create`, `supply_chain:dispensing_rule_edit`

A line is one of these two shapes (the operation schema enforces the types, and `validate_lines` enforces what the types cannot):

```json
{"kind": "stated", "paths": ["form.rutf_dispensing.rutf_sachets_dispensed"], "unit": "sachet"}
{"kind": "protocol", "given_paths": ["form.vita_group.va_delivered"], "given_values": ["child_fine"],
 "age_paths": ["form.child_age_months"],
 "by_age": [{"from_months": 6, "to_months": 11, "quantity": "1"}, {"from_months": 12, "to_months": 59, "quantity": "2"}],
 "unit": "capsule"}
```

`given_values: null` means "any non-empty answer counts as given" (an mRDT used per result recorded). `forms` lists the form names (`form_json.form["@name"]`) that the rule reads. An empty list means every form. A Stock Management form never answers a dispensing question, and without this list every one of them would be counted as `no_answer` (see Deviations).

- [ ] **Step 1: Write the failing tests**

Create `connect_labs/supply_chain/tests/test_dispensing_rules.py`:

```python
"""Dispensing rules: what a visit gives out, editable without a deploy.

THIS REPOSITORY IS PUBLIC. Every name, path and figure here is invented or
copied from an app definition (form paths only; no submission was read).
"""

import json

import jsonschema
import pytest
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.models import Revision
from connect_labs.supply_chain.models import DispensingRule
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10512
OPP = 10512

RUTF_LINES = [
    {
        "kind": "stated",
        "paths": ["form.rutf_dispensing.rutf_sachets_dispensed", "form.visit_1.rutf_dispensing.rutf_sachets_dispensed"],
        "unit": "sachet",
    },
    {
        "kind": "stated",
        "paths": ["form.screening_outcome.rutf_stock_deduction", "form.var.appetite_test_stock_deduction"],
        "unit": "sachet",
    },
]


@pytest.fixture
def da():
    return SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def world(da):
    op(da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton", "base_per_pack": 150})
    item = op(
        da,
        "item_upsert",
        data={"sku": "rutf-150", "name": "RUTF 150", "commodity_slug": "rutf", "base_unit": "sachet", "pack_unit": "carton", "base_per_pack": 150},
    )
    store = op(da, "supply_point_upsert", data={"slug": "partner-store", "name": "Partner store", "kind": "regional_store", "source": "we_recorded"})
    worker = op(
        da,
        "supply_point_upsert",
        data={"slug": "user-10512-worker-acacia", "name": "worker-acacia", "kind": "user_held", "opportunity_id": OPP, "connect_username": "worker-acacia", "source": "we_recorded"},
    )
    return {"item": item, "store": store, "worker": worker}


def upsert(da, world, **overrides):
    data = {
        "opportunity_id": OPP,
        "item_id": world["item"]["id"],
        "resupply_point_id": world["store"]["id"],
        "active_from": "2026-08-01",
        "lines": RUTF_LINES,
        **overrides,
    }
    return op(da, "dispensing_rule_upsert", data=data)


def test_a_stated_rule_is_kept_and_read_back(da, world):
    rule = upsert(da, world)

    got = op(da, "dispensing_rule_get", rule_id=rule["id"])

    assert got["lines"] == RUTF_LINES
    assert got["unit"] == "sachet"
    assert got["estimated"] is False
    assert got["active_from"] == "2026-08-01"
    assert [r["id"] for r in op(da, "dispensing_rule_list")] == [rule["id"]]


def test_upserting_again_edits_the_same_rule(da, world):
    first = upsert(da, world)
    second = upsert(da, world, active_from="2026-09-01", status="inactive")

    assert second["id"] == first["id"]
    assert DispensingRule.objects.count() == 1
    assert second["status"] == "inactive"
    assert op(da, "dispensing_rule_list") == []
    assert len(op(da, "dispensing_rule_list", include_inactive=True)) == 1


def test_leaving_status_out_of_an_edit_keeps_it(da, world):
    upsert(da, world, status="inactive")
    assert upsert(da, world, active_from="2026-09-01")["status"] == "inactive"


def test_a_protocol_line_marks_the_rule_estimated(da, world):
    rule = upsert(
        da,
        world,
        lines=[{"kind": "protocol", "given_paths": ["form.ors_group.ors_given"], "given_values": ["yes"], "quantity": 4, "unit": "sachet"}],
    )
    assert rule["estimated"] is True
    assert rule["lines"][0]["quantity"] == "4"


@pytest.mark.parametrize("extra", [{}, {"quantity": 1, "by_age": [{"from_months": 6, "to_months": 59, "quantity": 1}], "age_paths": ["form.age"]}])
def test_a_protocol_line_needs_exactly_one_of_quantity_or_bands(da, world, extra):
    line = {"kind": "protocol", "given_paths": ["form.g"], "given_values": ["yes"], "unit": "sachet", **extra}
    with pytest.raises(ValueError, match="either quantity or by_age"):
        upsert(da, world, lines=[line])


def test_age_bands_that_overlap_are_refused(da, world):
    line = {
        "kind": "protocol",
        "given_paths": ["form.g"],
        "given_values": ["yes"],
        "age_paths": ["form.age"],
        "by_age": [{"from_months": 6, "to_months": 12, "quantity": 1}, {"from_months": 12, "to_months": 59, "quantity": 2}],
        "unit": "sachet",
    }
    with pytest.raises(ValueError, match="overlap"):
        upsert(da, world, lines=[line])


def test_a_band_that_ends_before_it_starts_is_refused(da, world):
    line = {
        "kind": "protocol",
        "given_paths": ["form.g"],
        "age_paths": ["form.age"],
        "by_age": [{"from_months": 12, "to_months": 6, "quantity": 1}],
        "unit": "sachet",
    }
    with pytest.raises(ValueError, match="ends before it starts"):
        upsert(da, world, lines=[line])


def test_bands_need_an_age_path(da, world):
    line = {"kind": "protocol", "given_paths": ["form.g"], "by_age": [{"from_months": 6, "to_months": 59, "quantity": 1}], "unit": "sachet"}
    with pytest.raises(ValueError, match="names no form path"):
        upsert(da, world, lines=[line])


def test_a_path_outside_form_json_is_refused(da, world):
    with pytest.raises(ValueError, match="must start with 'form.'"):
        upsert(da, world, lines=[{"kind": "stated", "paths": ["rutf_sachets_dispensed"], "unit": "sachet"}])


def test_a_unit_the_item_cannot_count_in_is_refused(da, world):
    with pytest.raises(ValueError, match="no way to count"):
        upsert(da, world, lines=[{"kind": "stated", "paths": ["form.x"], "unit": "bottle"}])


def test_a_line_in_packs_is_accepted_when_the_item_states_its_pack(da, world):
    rule = upsert(da, world, lines=[{"kind": "stated", "paths": ["form.cartons"], "unit": "carton"}])
    assert rule["lines"][0]["unit"] == "carton"


def test_a_worker_cannot_be_the_resupply_point(da, world):
    with pytest.raises(ValueError, match="a store"):
        upsert(da, world, resupply_point_id=world["worker"]["id"])


def test_an_unknown_line_kind_fails_the_schema(da, world):
    with pytest.raises(jsonschema.ValidationError):
        upsert(da, world, lines=[{"kind": "guessed", "paths": ["form.x"], "unit": "sachet"}])


def test_reports_paths_are_validated(da, world):
    rule = upsert(
        da,
        world,
        reports={
            "balance_paths": ["form.var.new_stock_balance"],
            "receipt": {"quantity_paths": ["form.current_stock.sachets_received"], "date_paths": ["form.current_stock.date_received"]},
        },
    )
    assert rule["reports"]["receipt"]["date_paths"] == ["form.current_stock.date_received"]
    with pytest.raises(ValueError, match="must start with 'form.'"):
        upsert(da, world, reports={"balance_paths": ["new_stock_balance"]})


def test_a_rule_is_revisioned(da, world):
    rule = upsert(da, world)
    assert Revision.objects.filter(
        content_type=ContentType.objects.get_for_model(DispensingRule), object_id=str(rule["id"]), action="create"
    ).exists()


# ---- the screen -----------------------------------------------------------


@pytest.fixture
def scoped(client, django_user_model, monkeypatch, da):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.stock import visit_views  # noqa: F401

    for module in ("form_views", "views", "stock.visit_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", lambda request: da)
    client.force_login(django_user_model.objects.create_user(username="grace", password="x"))
    return client


def test_the_rules_page_lists_a_rule(scoped, da, world):
    upsert(da, world)
    body = scoped.get(reverse("supply_chain:dispensing_rules")).content.decode()
    assert "RUTF 150" in body
    assert "form.rutf_dispensing.rutf_sachets_dispensed" in body
    assert "Stated" in body


def test_a_rule_is_created_on_the_form(scoped, world):
    response = scoped.post(
        reverse("supply_chain:dispensing_rule_create"),
        {
            "opportunity_id": str(OPP),
            "item": str(world["item"]["id"]),
            "resupply_point": str(world["store"]["id"]),
            "active_from": "2026-08-01",
            "lines": json.dumps(RUTF_LINES),
            "form_names": "Screening\nVisit Form",
            "reports": "",
            "status": "active",
        },
    )
    assert response.status_code == 302
    rule = DispensingRule.objects.get()
    assert rule.forms == ["Screening", "Visit Form"]


def test_a_refused_rule_comes_back_as_an_error_on_the_form(scoped, world):
    response = scoped.post(
        reverse("supply_chain:dispensing_rule_create"),
        {
            "opportunity_id": str(OPP),
            "item": str(world["item"]["id"]),
            "resupply_point": str(world["store"]["id"]),
            "active_from": "2026-08-01",
            "lines": json.dumps([{"kind": "stated", "paths": ["rutf"], "unit": "sachet"}]),
            "form_names": "",
            "reports": "",
            "status": "active",
        },
    )
    assert response.status_code == 200
    assert "must start with" in response.content.decode()
    assert not DispensingRule.objects.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_dispensing_rules.py -q"`
Expected: FAIL with `KeyError: 'dispensing_rule_upsert'`.

- [ ] **Step 3: Add the model**

In `connect_labs/supply_chain/models.py`, after `class DistributionLine`:

```python
class DispensingRule(TimestampedModel):
    """What a visit on one opportunity gives out of one item (design 2026-09-28 §3.1).

    One per (program, opportunity, item): an iCCM deliver app gives out many
    commodities from one visit, so an opportunity carries a rule per item.
    Data, not code, because forms differ per opportunity and change: a deploy
    per renamed question is the wrong price for keeping stock honest.

    `lines` is validated by stock/services/dispensing.validate_lines. A rule
    with any `protocol` line produces ESTIMATED consumption, and says so.
    """

    program_id = models.IntegerField(db_index=True)
    opportunity_id = models.IntegerField(db_index=True)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="dispensing_rules")
    lines = models.JSONField(default=list)
    # Form names (form_json.form["@name"]) this rule reads; empty is every form.
    forms = models.JSONField(default=list, blank=True)
    # What the worker's own app says: {"balance_paths": [...], "receipt": {...}}.
    reports = models.JSONField(default=dict, blank=True)
    # Where a worker point this rule creates hangs from.
    resupply_point = models.ForeignKey(SupplyPoint, on_delete=models.PROTECT, related_name="dispensing_rules")
    # Visits before this are not read, so switching a rule on mid-programme
    # does not invent history the ledger never saw.
    active_from = models.DateField()
    status = models.CharField(max_length=16, default="active", choices=_choices(("active", "inactive")))

    class Meta:
        ordering = ["opportunity_id", "item_id"]
        constraints = [
            models.UniqueConstraint(fields=["program_id", "opportunity_id", "item"], name="uniq_dispensing_rule_opp_item")
        ]

    def __str__(self):
        return f"dispensing rule: {self.item} on opportunity {self.opportunity_id}"

    @property
    def estimated(self) -> bool:
        return any(line.get("kind") == "protocol" for line in self.lines or [])
```

- [ ] **Step 4: Write the validation**

Create `connect_labs/supply_chain/stock/services/dispensing.py`:

```python
"""Dispensing rules: what a visit's form answers say left the worker's bag.

Pure functions over a rule's `lines` and a visit's `form_json`. No database:
the reader (visit_reader.py) owns persistence, so every rule the domain
applies to a form answer can be tested with a dict.

Two kinds of line (design 2026-09-28 §3.1):

- **stated** -- read a number the worker entered, e.g.
  `form.rutf_dispensing.rutf_sachets_dispensed`. The first path that answers
  wins, so one line covers the same question on two forms.
- **protocol** -- the form records only THAT something was given
  (`va_delivered = child_fine`); the quantity comes from the protocol,
  optionally banded by the child's age in months. Anything a protocol line
  contributes is ESTIMATED, everywhere it appears.

Every path is a `form_json` path and so starts `form.` -- `/data/x` in the
app is `form.x` in a submission. A path that does not is refused at save,
because reading the top-level visit dict (the bug extract_rows had) returns
nothing and looks exactly like "nobody answered".
"""

from decimal import Decimal

from connect_labs.supply_chain.stock.services import ledger
from connect_labs.supply_chain.values import Unconfirmed


def base_unit(item) -> str:
    """The single unit a worker counts this item in: sachet, capsule, tablet."""
    unit = ledger._pack_spec(item)[1]
    if not unit:
        raise ValueError(
            f"{item.name} states no single unit (base_unit), so nothing given at a visit can be counted in it"
        )
    return unit


def _paths(values, what) -> list[str]:
    if not values:
        raise ValueError(f"{what} names no form path")
    for path in values:
        if not isinstance(path, str) or not path.startswith("form."):
            raise ValueError(
                f"{what}: {path!r} is not a form_json path -- it must start with 'form.' "
                "(the app's /data/x is form.x in a submission)"
            )
    return list(values)


def _bands(bands, what) -> list[dict]:
    ordered = sorted(bands, key=lambda band: band["from_months"])
    previous_end = None
    clean = []
    for band in ordered:
        start, end = band["from_months"], band["to_months"]
        if end < start:
            raise ValueError(f"{what}: the band {start}-{end} months ends before it starts")
        if previous_end is not None and start <= previous_end:
            raise ValueError(f"{what}: age bands overlap at {start} months, so a child of that age would get two doses")
        previous_end = end
        clean.append({"from_months": start, "to_months": end, "quantity": str(Decimal(str(band["quantity"])))})
    return clean


def validate_lines(lines, item) -> list[dict]:
    """The lines as they will be stored, or ValueError naming the first problem."""
    to_unit = base_unit(item)
    clean = []
    for number, line in enumerate(lines, start=1):
        what = f"line {number}"
        unit = line["unit"]
        if isinstance(ledger.convert(Decimal(1), unit, to_unit, item), Unconfirmed):
            raise ValueError(f"{what} is in {unit}s, and {item.name} gives no way to count {unit}s as {to_unit}s")
        if line["kind"] == "stated":
            clean.append({"kind": "stated", "paths": _paths(line.get("paths"), what), "unit": unit})
            continue
        has_quantity, has_bands = "quantity" in line, "by_age" in line
        if has_quantity == has_bands:
            raise ValueError(
                f"{what} is a protocol line: give either quantity or by_age, not {'both' if has_quantity else 'neither'}"
            )
        protocol = {
            "kind": "protocol",
            "given_paths": _paths(line.get("given_paths"), what),
            "given_values": line.get("given_values"),
            "unit": unit,
        }
        if has_quantity:
            protocol["quantity"] = str(Decimal(str(line["quantity"])))
        else:
            protocol["age_paths"] = _paths(line.get("age_paths"), f"{what}'s age")
            protocol["by_age"] = _bands(line["by_age"], what)
        clean.append(protocol)
    return clean


def validate_reports(reports) -> dict:
    """What the worker's own app reports: its running balance, and receipts."""
    if not reports:
        return {}
    clean = {}
    if reports.get("balance_paths"):
        clean["balance_paths"] = _paths(reports["balance_paths"], "the balance report")
    if reports.get("receipt"):
        receipt = reports["receipt"]
        clean["receipt"] = {
            "quantity_paths": _paths(receipt.get("quantity_paths"), "the receipt quantity"),
            "date_paths": _paths(receipt.get("date_paths"), "the receipt date"),
        }
    return clean
```

- [ ] **Step 5: Repository methods**

In `connect_labs/supply_chain/stock/repository.py`, add `DispensingRule` to the models import, then add after the supply-point section:

```python
    # ---- dispensing rules ------------------------------------------------

    def _dispensing_rules(self):
        return DispensingRule.objects.filter(program_id=self._require_program()).select_related(
            "item__commodity", "resupply_point"
        )

    def list_dispensing_rules(self, opportunity_id=None, include_inactive=False):
        qs = self._dispensing_rules()
        if opportunity_id is not None:
            qs = qs.filter(opportunity_id=opportunity_id)
        if not include_inactive:
            qs = qs.filter(status="active")
        return list(qs)

    def get_dispensing_rule(self, rule_id):
        return self._dispensing_rules().filter(pk=rule_id).first()

    @transaction.atomic
    def upsert_dispensing_rule(self, data):
        """Create or edit the rule for one item on one opportunity.

        Keyed on (opportunity, item), because two rules for one item would
        post the same sachets twice. Omitting `reports`, `forms` or `status`
        on an edit keeps what is there.
        """
        from connect_labs.supply_chain.data_access import _fresh
        from connect_labs.supply_chain.stock.services.dispensing import validate_lines, validate_reports

        item = self._resolve_item(data["item_id"])
        point = self._require_supply_point(data["resupply_point_id"], "resupply point")
        if point.kind in ("user_held", "in_transit"):
            raise ValueError(
                f"{point.name} is not a store: workers are resupplied from a store, so a rule's resupply "
                "point must be one"
            )
        if point.opportunity_id not in (None, data["opportunity_id"]):
            raise ValueError(f"{point.name} belongs to opportunity {point.opportunity_id}, not {data['opportunity_id']}")
        defaults = {
            "lines": validate_lines(data["lines"], item),
            "resupply_point": point,
            "active_from": data["active_from"],
        }
        for key in ("forms", "status"):
            if key in data:
                defaults[key] = data[key]
        if "reports" in data:
            defaults["reports"] = validate_reports(data["reports"])
        rule, _ = DispensingRule.objects.update_or_create(
            program_id=self._require_program(), opportunity_id=data["opportunity_id"], item=item, defaults=defaults
        )
        return _fresh(rule)
```

- [ ] **Step 6: Serializer**

In `connect_labs/supply_chain/serializers.py` add:

```python
def dispensing_rule(obj) -> dict:
    from connect_labs.supply_chain.stock.services import ledger

    return {
        "id": obj.pk,
        "opportunity_id": obj.opportunity_id,
        "item_id": obj.item_id,
        "item_name": obj.item.name,
        "commodity_slug": obj.item.commodity.slug,
        "unit": ledger._pack_spec(obj.item)[1],
        "lines": obj.lines,
        "forms": obj.forms,
        "reports": obj.reports,
        "resupply_supply_point_id": obj.resupply_point_id,
        "active_from": _date(obj.active_from),
        "status": obj.status,
        "estimated": obj.estimated,
    }
```

In `connect_labs/supply_chain/operations.py`, add `models.DispensingRule: serializers.dispensing_rule,` to `_SERIALIZERS`.

- [ ] **Step 7: The operations**

Create `connect_labs/supply_chain/stock/visit_operations.py`:

```python
"""Stock from visits: dispensing rules, the visit reader, and what we believe.

Registered into the single registry in operations.py, like stock/operations.py,
so the HTTP API and the MCP server both get them with no second list.
Design: docs/superpowers/specs/2026-09-28-supply-stock-from-visits-design.md.
"""

from connect_labs.supply_chain.operations import ID, QUANTITY, obj, record, register_operation

_DATE = {"type": "string", "format": "date"}
_PATHS = {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}
_UNIT = {"type": "string", "minLength": 1}

_STATED_LINE = obj({"kind": {"const": "stated"}, "paths": _PATHS, "unit": _UNIT}, required=("kind", "paths", "unit"))
_AGE_BAND = obj(
    {"from_months": {"type": "integer", "minimum": 0}, "to_months": {"type": "integer", "minimum": 0}, "quantity": QUANTITY},
    required=("from_months", "to_months", "quantity"),
)
_PROTOCOL_LINE = obj(
    {
        "kind": {"const": "protocol"},
        "given_paths": _PATHS,
        "given_values": {"type": ["array", "null"], "items": {"type": "string", "minLength": 1}},
        "quantity": QUANTITY,
        "by_age": {"type": "array", "minItems": 1, "items": _AGE_BAND},
        "age_paths": _PATHS,
        "unit": _UNIT,
    },
    required=("kind", "given_paths", "unit"),
)
_REPORTS = obj(
    {
        "balance_paths": _PATHS,
        "receipt": obj({"quantity_paths": _PATHS, "date_paths": _PATHS}, required=("quantity_paths", "date_paths")),
    }
)
_RULE_DATA = obj(
    {
        "opportunity_id": ID,
        "item_id": ID,
        "resupply_point_id": ID,
        "active_from": _DATE,
        "lines": {"type": "array", "minItems": 1, "items": {"oneOf": [_STATED_LINE, _PROTOCOL_LINE]}},
        "forms": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "reports": _REPORTS,
        "status": {"enum": ["active", "inactive"]},
    },
    required=("opportunity_id", "item_id", "resupply_point_id", "active_from", "lines"),
)


@register_operation(
    name="dispensing_rule_list",
    summary=(
        "The dispensing rules: per opportunity and item, which form answers say what a visit gave out. "
        "Inactive rules are left out unless include_inactive."
    ),
    input_schema=obj({"opportunity_id": ID, "include_inactive": {"type": "boolean"}}),
)
def dispensing_rule_list(access, opportunity_id=None, include_inactive=False):
    return [
        record(rule)
        for rule in access.list_dispensing_rules(opportunity_id=opportunity_id, include_inactive=include_inactive)
    ]


@register_operation(
    name="dispensing_rule_get",
    summary="One dispensing rule, with its lines, the forms it reads and the store its workers hang from.",
    input_schema=obj({"rule_id": ID}, required=("rule_id",)),
)
def dispensing_rule_get(access, rule_id):
    rule = access.get_dispensing_rule(rule_id)
    if rule is None:
        raise ValueError(f"dispensing rule {rule_id} does not exist in this programme")
    return record(rule)


@register_operation(
    name="dispensing_rule_upsert",
    summary=(
        "Create or edit what a visit on one opportunity gives out of one item. Each line is either "
        "stated -- read a number from form paths (form.x, the app's /data/x) -- or protocol -- the "
        "form says only that it was given, and the quantity is fixed or banded by the child's age in "
        "months. A protocol line makes every figure it feeds ESTIMATED. Visits before active_from are "
        "never read. forms limits the rule to named forms; reports names where the worker's own app "
        "keeps its balance and receipts."
    ),
    input_schema=obj({"data": _RULE_DATA}, required=("data",)),
    is_write=True,
)
def dispensing_rule_upsert(access, data):
    return record(access.upsert_dispensing_rule(data))
```

In `connect_labs/supply_chain/apps.py` `ready()` add
`from connect_labs.supply_chain.stock import visit_operations  # noqa: F401`, and in `connect_labs/supply_chain/mcp_tools.py` next to `_stock_operations` add
`from connect_labs.supply_chain.stock import visit_operations as _visit_operations  # noqa: F401`.

- [ ] **Step 8: History path and purge**

In `connect_labs/supply_chain/history/program.py` `PATHS`, add `"DispensingRule": _direct,` beside `"Movement": _direct,`.

In `connect_labs/supply_chain/data_access.py`, add `DispensingRule` to the models import. In `purge()`, add `reverses=None` to the `movements.update(...)` call (from Task 1; the self-reference is PROTECT). Then insert
`drop("dispensing rules", DispensingRule.objects.filter(program_id=program_id))`
immediately before `drop("update links", ...)`, because rules PROTECT both the item and the store.

- [ ] **Step 9: Generate the migration**

Run: `make manage CMD="makemigrations supply_chain --name dispensing_rule"`
Expected: `0036_dispensing_rule.py` with `CreateModel(name="DispensingRule", ...)` and the unique constraint. Check: `make manage CMD="makemigrations supply_chain --check --dry-run"` prints `No changes detected`.

- [ ] **Step 10: The web form**

Create `connect_labs/supply_chain/stock/dispensing_forms.py`:

```python
"""The dispensing rule as a page. Lines are JSON: they are a small program
over form paths, and a field-per-line form would hide the structure a person
has to get right. The operation validates; this only parses."""

from crispy_forms.helper import FormHelper
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.supply_chain.forms import INPUT, SEARCHABLE, SELECT, TEXTAREA
from connect_labs.supply_chain.models import Item, SupplyPoint


class DispensingRuleForm(forms.Form):
    opportunity_id = forms.IntegerField(min_value=1, label=_("Opportunity"), widget=forms.NumberInput(attrs=INPUT))
    item = forms.ModelChoiceField(queryset=Item.objects.none(), label=_("What is given out"), widget=forms.Select(attrs=SEARCHABLE))
    resupply_point = forms.ModelChoiceField(
        queryset=SupplyPoint.objects.none(),
        label=_("Workers are resupplied from"),
        widget=forms.Select(attrs=SEARCHABLE),
        help_text=_("A worker seen for the first time gets a supply point under this store."),
    )
    active_from = forms.DateField(
        label=_("Read visits from"),
        widget=forms.DateInput(attrs={**INPUT, "type": "date"}),
        help_text=_("Earlier visits are never read, so turning a rule on does not invent history."),
    )
    lines = forms.JSONField(
        label=_("Lines"),
        widget=forms.Textarea(attrs={**TEXTAREA, "rows": 10}),
        help_text=_(
            'A list. Stated: {"kind": "stated", "paths": ["form.x"], "unit": "sachet"}. Protocol: '
            '{"kind": "protocol", "given_paths": ["form.y"], "given_values": ["yes"], "quantity": "4", "unit": "sachet"}, '
            'or "by_age" bands with "age_paths" in place of quantity.'
        ),
    )
    # Not `forms`: a class attribute of that name would shadow django.forms for
    # every field declared after it.
    form_names = forms.CharField(
        required=False,
        label=_("Forms read"),
        widget=forms.Textarea(attrs={**TEXTAREA, "rows": 2}),
        help_text=_("One form name per line. Leave empty to read every form."),
    )
    reports = forms.JSONField(
        required=False,
        label=_("What the worker's app reports"),
        widget=forms.Textarea(attrs={**TEXTAREA, "rows": 4}),
        help_text=_('Optional: {"balance_paths": [...], "receipt": {"quantity_paths": [...], "date_paths": [...]}}'),
    )
    status = forms.ChoiceField(choices=[("active", _("On")), ("inactive", _("Off"))], widget=forms.Select(attrs=SELECT))

    def __init__(self, *args, access=None, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)
        if access is not None:
            self.fields["item"].queryset = Item.objects.filter(scope_key=access.scope_key)
            self.fields["resupply_point"].queryset = SupplyPoint.objects.filter(program_id=access.program_id).exclude(
                kind__in=("user_held", "in_transit")
            )
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True

    def payload(self) -> dict:
        cleaned = self.cleaned_data
        payload = {
            "opportunity_id": cleaned["opportunity_id"],
            "item_id": cleaned["item"].pk,
            "resupply_point_id": cleaned["resupply_point"].pk,
            "active_from": cleaned["active_from"].isoformat(),
            "lines": cleaned["lines"],
            "forms": [name.strip() for name in (cleaned.get("form_names") or "").splitlines() if name.strip()],
            "status": cleaned["status"],
        }
        if cleaned.get("reports"):
            payload["reports"] = cleaned["reports"]
        return payload
```

Create `connect_labs/supply_chain/stock/visit_views.py`:

```python
"""Screens for stock from visits: the rules (this task), the workers (Task 8)."""

from django.urls import reverse

from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.models import DispensingRule
from connect_labs.supply_chain.stock.dispensing_forms import DispensingRuleForm
from connect_labs.supply_chain.views import OperationBase


class DispensingRulesView(OperationBase):
    """Every rule in the programme, on and off, with the paths it reads."""

    template_name = "supply_chain/dispensing_rules.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if context["has_program_context"]:
            context["rules"] = self.op("dispensing_rule_list", include_inactive=True)
        return context


class _RuleScreen(OperationFormView):
    operation = "dispensing_rule_upsert"
    form_class = DispensingRuleForm
    submit_label = "Save rule"

    def breadcrumb(self, **kwargs):
        return [{"label": "Stock", "href": reverse("supply_chain:stock")}, {"label": "Dispensing rules", "href": reverse("supply_chain:dispensing_rules")}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:dispensing_rules")

    def redirect_to(self, result):
        return reverse("supply_chain:dispensing_rules")


class DispensingRuleCreateView(_RuleScreen):
    title = "New dispensing rule"
    intro = "What a visit on one opportunity gives out of one item, read from the form's own answers."


class DispensingRuleUpdateView(_RuleScreen):
    title = "Edit dispensing rule"
    intro = "Visits already posted are not read again; the change applies to visits read from now on."

    def get_initial(self):
        rule = DispensingRule.objects.filter(program_id=_access(self.request).program_id, pk=self.kwargs["rule_id"]).first()
        if rule is None:
            from django.http import Http404

            raise Http404("no such rule in this programme")
        return {
            "opportunity_id": rule.opportunity_id,
            "item": rule.item_id,
            "resupply_point": rule.resupply_point_id,
            "active_from": rule.active_from,
            "lines": rule.lines,
            "form_names": "\n".join(rule.forms or []),
            "reports": rule.reports or None,
            "status": rule.status,
        }
```

Create `connect_labs/templates/supply_chain/dispensing_rules.html`:

```html
{% extends 'supply_chain/base.html' %}
{% load supply_chain_extras %}
{% comment %}
The rules that turn a visit's answers into stock leaving a worker's bag.
Each line says which question it reads; a protocol line is labelled
Estimated here and on every figure it feeds.
{% endcomment %}
{% block supply_content %}
<div class="flex flex-wrap items-end justify-between gap-4 mb-5">
  <div>
    <h1 class="text-2xl font-semibold text-gray-900 leading-tight">Dispensing rules</h1>
    <p class="text-sm text-gray-500 mt-1">Which form answers say what each visit gave out, per opportunity and item.</p>
  </div>
  {% if has_program_context and not supply_as_of %}
    <a href="{% url 'supply_chain:dispensing_rule_create' %}" class="px-4 py-2 text-sm bg-brand-indigo hover:bg-brand-deep-purple text-white rounded-md">New rule</a>
  {% endif %}
</div>
{% if not has_program_context %}
  <p class="text-sm text-gray-600">Choose a programme above.</p>
{% elif not rules %}
  <p class="text-sm text-gray-600">No rules yet. Until there is one, no visit moves any stock.</p>
{% else %}
  <div class="bg-white border border-brand-border-light rounded-lg overflow-hidden">
    <table class="w-full text-sm">
      <thead class="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
        <tr><th class="text-left px-4 py-2">Item</th><th class="text-left px-4 py-2">Opportunity</th><th class="text-left px-4 py-2">Reads</th><th class="text-left px-4 py-2">From</th><th class="text-left px-4 py-2">Status</th><th class="px-4 py-2"><span class="sr-only">Actions</span></th></tr>
      </thead>
      <tbody class="divide-y divide-gray-100">
        {% for rule in rules %}
          <tr>
            <td class="px-4 py-2 text-gray-900">{{ rule.item_name }}{% if rule.estimated %} <span class="ml-1 px-1.5 py-0.5 rounded text-xs bg-amber-50 text-amber-900 border border-amber-200">Estimated</span>{% endif %}</td>
            <td class="px-4 py-2 text-gray-700">{{ rule.opportunity_id }}</td>
            <td class="px-4 py-2 text-gray-700">
              {% for line in rule.lines %}
                <div>{% if line.kind == "stated" %}Stated{% else %}Protocol{% endif %}, in {{ line.unit|unit_plural }}:
                  {% if line.kind == "stated" %}{% for p in line.paths %}<code class="text-xs">{{ p }}</code>{% if not forloop.last %} or {% endif %}{% endfor %}
                  {% else %}{% for p in line.given_paths %}<code class="text-xs">{{ p }}</code>{% if not forloop.last %} or {% endif %}{% endfor %}{% endif %}
                </div>
              {% endfor %}
            </td>
            <td class="px-4 py-2 text-gray-700">{{ rule.active_from|day }}</td>
            <td class="px-4 py-2 text-gray-700">{% if rule.status == "active" %}On{% else %}Off{% endif %}</td>
            <td class="px-4 py-2 text-right">{% if not supply_as_of %}<a href="{% url 'supply_chain:dispensing_rule_edit' rule.id %}" class="text-brand-indigo hover:underline">Edit</a>{% endif %}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
{% endif %}
{% endblock %}
```

In `connect_labs/supply_chain/urls.py`, import `from connect_labs.supply_chain.stock import visit_views` and add after the `stock/counts/new/` route:

```python
    # What a visit gives out, per opportunity and item. "new" before the id.
    path("stock/dispensing/", visit_views.DispensingRulesView.as_view(), name="dispensing_rules"),
    path("stock/dispensing/new/", visit_views.DispensingRuleCreateView.as_view(), name="dispensing_rule_create"),
    path("stock/dispensing/<int:rule_id>/edit/", visit_views.DispensingRuleUpdateView.as_view(), name="dispensing_rule_edit"),
```

In `connect_labs/supply_chain/navigation.py` `TAB_FOR_VIEW`, add the three names mapped to `"supply_chain:stock"`.

- [ ] **Step 11: Run the tests to verify they pass, plus parity, history and navigation**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_dispensing_rules.py connect_labs/supply_chain/tests/test_mcp_parity.py connect_labs/supply_chain/tests/test_history_models.py connect_labs/supply_chain/tests/test_navigation_tabs.py connect_labs/supply_chain/tests/test_data_access.py connect_labs/supply_chain/tests/test_serialisers_produce_json.py -q"`
Expected: all PASS. `test_every_agent_operation_is_exposed_as_an_mcp_tool` now includes the three new tools.

- [ ] **Step 12: Commit**

```bash
git add connect_labs/supply_chain/ connect_labs/templates/supply_chain/dispensing_rules.html
make commit  # message: "feat(supply): dispensing rules -- what a visit gives out, editable without a deploy"
```

---

### Task 3: Evaluate a rule's lines against one visit's `form_json`

**Files:**
- Modify: `connect_labs/supply_chain/stock/services/dispensing.py` (append)
- Test: `connect_labs/supply_chain/tests/test_dispensing_evaluate.py`

**Interfaces:**
- Consumes: `base_unit(item)` (Task 2), `ledger.convert`.
- Produces:
  - constants `DISPENSED = "dispensed"`, `NOTHING_GIVEN = "nothing_given"`, `NO_ANSWER = "no_answer"`, `UNIT_REFUSED = "unit_refused"`
  - `@dataclass(frozen=True) class Dispensed: outcome: str; quantity: Decimal; unit: str; estimated: bool; answers: dict; reasons: tuple[str, ...]`
  - `read_number(value) -> Decimal | None`
  - `first_answer(form_json: dict, paths: list[str]) -> tuple[str | None, object]`
  - `evaluate(lines: list[dict], form_json: dict, item) -> Dispensed`

Semantics: a line whose paths are all unanswered adds nothing. If **any** line is answered but unreadable (a stated answer that is not a non-negative number, or a protocol dose given to a child whose age falls in no band), the whole item is `NO_ANSWER`, because posting the other lines alone would understate silently. If no line is answered, the item is `NO_ANSWER`. If lines were answered and the total is zero, the item is `NOTHING_GIVEN`. Otherwise it is `DISPENSED`, and `estimated` is true when a protocol line contributed more than zero.

- [ ] **Step 1: Write the failing tests**

Create `connect_labs/supply_chain/tests/test_dispensing_evaluate.py`:

```python
"""A rule's lines against one visit's answers. Pure: no database.

THIS REPOSITORY IS PUBLIC. Every answer here is invented.
"""

from decimal import Decimal

import pytest

from connect_labs.supply_chain.models import Commodity, Item
from connect_labs.supply_chain.stock.services.dispensing import (
    DISPENSED,
    NO_ANSWER,
    NOTHING_GIVEN,
    UNIT_REFUSED,
    evaluate,
    read_number,
)


def _item(base_per_pack=150):
    commodity = Commodity(scope_key="prog:1", slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton", base_per_pack=base_per_pack)
    return Item(scope_key="prog:1", sku="rutf", name="RUTF 150", commodity=commodity, base_unit="sachet", pack_unit="carton", base_per_pack=base_per_pack)


RUTF = [
    {"kind": "stated", "paths": ["form.rutf_dispensing.rutf_sachets_dispensed", "form.visit_1.rutf_dispensing.rutf_sachets_dispensed"], "unit": "sachet"},
    {"kind": "stated", "paths": ["form.screening_outcome.rutf_stock_deduction", "form.var.appetite_test_stock_deduction"], "unit": "sachet"},
]
VITA = [
    {
        "kind": "protocol",
        "given_paths": ["form.vita_group.va_delivered"],
        "given_values": ["child_fine"],
        "age_paths": ["form.child_age_months"],
        "by_age": [{"from_months": 6, "to_months": 11, "quantity": "1"}, {"from_months": 12, "to_months": 59, "quantity": "2"}],
        "unit": "sachet",
    }
]
MRDT = [{"kind": "protocol", "given_paths": ["form.fever.mrdt_result"], "given_values": None, "quantity": "1", "unit": "sachet"}]


def form(**answers):
    """{"form": {...}} from dotted keys written with __ for dots."""
    root: dict = {}
    for key, value in answers.items():
        node = root
        parts = key.split("__")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return {"id": "xf-1", "form": root}


def test_stated_lines_add_up_to_one_consumption():
    result = evaluate(RUTF, form(visit_1__rutf_dispensing__rutf_sachets_dispensed="14", screening_outcome__rutf_stock_deduction="0.3333"), _item())
    assert (result.outcome, result.quantity, result.unit, result.estimated) == (DISPENSED, Decimal("14.3333"), "sachet", False)
    assert result.answers == {
        "form.visit_1.rutf_dispensing.rutf_sachets_dispensed": "14",
        "form.screening_outcome.rutf_stock_deduction": "0.3333",
    }


def test_the_first_path_that_answers_wins():
    result = evaluate(RUTF, form(rutf_dispensing__rutf_sachets_dispensed=7), _item())
    assert result.quantity == Decimal("7")


def test_a_protocol_dose_is_estimated():
    result = evaluate(VITA, form(vita_group__va_delivered="child_fine", child_age_months="8"), _item())
    assert (result.outcome, result.quantity, result.estimated) == (DISPENSED, Decimal("1"), True)


def test_the_age_band_sets_the_dose():
    assert evaluate(VITA, form(vita_group__va_delivered="child_fine", child_age_months="30"), _item()).quantity == Decimal("2")


def test_a_multiple_choice_answer_matches_any_of_its_words():
    result = evaluate(VITA, form(vita_group__va_delivered="referred child_fine", child_age_months="30"), _item())
    assert result.outcome == DISPENSED


def test_given_something_else_is_nothing_given_not_unknown():
    result = evaluate(VITA, form(vita_group__va_delivered="child_unwell", child_age_months="30"), _item())
    assert (result.outcome, result.quantity) == (NOTHING_GIVEN, Decimal("0"))


def test_a_dose_given_at_an_age_no_band_covers_is_unknown():
    result = evaluate(VITA, form(vita_group__va_delivered="child_fine", child_age_months="3"), _item())
    assert result.outcome == NO_ANSWER
    assert "no band" in result.reasons[0]


def test_a_dose_given_with_no_age_is_unknown():
    assert evaluate(VITA, form(vita_group__va_delivered="child_fine"), _item()).outcome == NO_ANSWER


def test_any_answer_counts_when_the_protocol_names_no_values():
    assert evaluate(MRDT, form(fever__mrdt_result="negative"), _item()).quantity == Decimal("1")


def test_a_form_that_never_asks_is_no_answer():
    assert evaluate(RUTF, form(other__question="1"), _item()).outcome == NO_ANSWER


@pytest.mark.parametrize("answer", ["", "two", "-3", "NaN", "Infinity", True])
def test_an_unreadable_answer_is_unknown_not_zero(answer):
    result = evaluate(RUTF, form(rutf_dispensing__rutf_sachets_dispensed=answer, screening_outcome__rutf_stock_deduction="0.3333"), _item())
    if answer == "":
        # An empty answer is no answer at all: the appetite test alone still stands.
        assert (result.outcome, result.quantity) == (DISPENSED, Decimal("0.3333"))
    else:
        assert result.outcome == NO_ANSWER
        assert "form.rutf_dispensing.rutf_sachets_dispensed" in result.reasons[0]


def test_zero_answered_is_nothing_given():
    assert evaluate(RUTF, form(rutf_dispensing__rutf_sachets_dispensed="0"), _item()).outcome == NOTHING_GIVEN


def test_a_line_in_cartons_is_counted_in_sachets():
    lines = [{"kind": "stated", "paths": ["form.cartons"], "unit": "carton"}]
    assert evaluate(lines, form(cartons="1"), _item()).quantity == Decimal("150")


def test_a_unit_the_item_cannot_convert_is_refused():
    lines = [{"kind": "stated", "paths": ["form.cartons"], "unit": "carton"}]
    result = evaluate(lines, form(cartons="1"), _item(base_per_pack=None))
    assert result.outcome == UNIT_REFUSED
    assert result.reasons


@pytest.mark.parametrize("value,expected", [("14", Decimal("14")), (3, Decimal("3")), ("0.5", Decimal("0.5")), (None, None), (True, None), ("1e999", None), (" 2 ", Decimal("2"))])
def test_read_number(value, expected):
    assert read_number(value) == expected
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_dispensing_evaluate.py -q"`
Expected: FAIL with `ImportError: cannot import name 'DISPENSED'`.

- [ ] **Step 3: Implement evaluation**

Append to `connect_labs/supply_chain/stock/services/dispensing.py` (add `from dataclasses import dataclass`, `from decimal import InvalidOperation` and `from connect_labs.labs.analysis.utils import extract_json_path` at the top):

```python
DISPENSED = "dispensed"
NOTHING_GIVEN = "nothing_given"
NO_ANSWER = "no_answer"
UNIT_REFUSED = "unit_refused"

# 1e6 sachets at one visit is a typo, not a ration; beyond it the number is
# not a quantity anyone gave out.
_MOST_AT_ONE_VISIT = Decimal("1000000")


@dataclass(frozen=True)
class Dispensed:
    """What one visit gave out of one item, in the item's single unit."""

    outcome: str
    quantity: Decimal
    unit: str
    estimated: bool
    answers: dict
    reasons: tuple = ()


def read_number(value) -> Decimal | None:
    """A form answer as a non-negative quantity, or None when it is not one."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite() or number < 0 or number > _MOST_AT_ONE_VISIT:
        return None
    return number


def first_answer(form_json, paths):
    """(path, raw answer) for the first path answered, or (None, None)."""
    for path in paths:
        value = extract_json_path(form_json, path)
        if value not in (None, ""):
            return path, value
    return None, None


def _given(value, given_values) -> bool:
    if given_values is None:
        return True
    # A CommCare multiple-choice answer is its choices joined by spaces.
    return any(word in given_values for word in str(value).split())


def _dose_by_age(bands, age):
    for band in bands:
        if band["from_months"] <= age <= band["to_months"]:
            return Decimal(band["quantity"])
    return None


def evaluate(lines, form_json, item) -> Dispensed:
    """Sum a rule's lines over one visit's answers. See the module docstring."""
    to_unit = base_unit(item)
    total = Decimal(0)
    estimated = answered = False
    answers: dict = {}
    reasons: list[str] = []
    for line in lines:
        if line["kind"] == "stated":
            path, raw = first_answer(form_json, line["paths"])
            if path is None:
                continue
            answers[path] = raw
            answered = True
            quantity = read_number(raw)
            if quantity is None:
                reasons.append(f"{path} answered {raw!r}, which is not a quantity")
                continue
        else:
            path, raw = first_answer(form_json, line["given_paths"])
            if path is None:
                continue
            answers[path] = raw
            answered = True
            if not _given(raw, line.get("given_values")):
                continue
            if "quantity" in line:
                quantity = Decimal(line["quantity"])
            else:
                age_path, age_raw = first_answer(form_json, line["age_paths"])
                if age_path is not None:
                    answers[age_path] = age_raw
                age = read_number(age_raw)
                quantity = _dose_by_age(line["by_age"], age) if age is not None else None
                if quantity is None:
                    reasons.append(
                        f"{path} says it was given, but the child's age ({age_raw!r}) is in no band the protocol states"
                    )
                    continue
            if quantity > 0:
                estimated = True
        converted = ledger.convert(quantity, line["unit"], to_unit, item)
        if isinstance(converted, Unconfirmed):
            return Dispensed(UNIT_REFUSED, Decimal(0), to_unit, False, answers, tuple(converted.reasons))
        total += converted.amount

    if reasons or not answered:
        return Dispensed(NO_ANSWER, Decimal(0), to_unit, False, answers, tuple(reasons))
    if total == 0:
        return Dispensed(NOTHING_GIVEN, Decimal(0), to_unit, False, answers)
    return Dispensed(DISPENSED, total.quantize(ledger.QUANTITY_SCALE), to_unit, estimated, answers)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_dispensing_evaluate.py connect_labs/supply_chain/tests/test_dispensing_rules.py -q"`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add connect_labs/supply_chain/stock/services/dispensing.py connect_labs/supply_chain/tests/test_dispensing_evaluate.py
make commit  # message: "feat(supply): evaluate a dispensing rule against one visit's answers"
```

---

### Task 4: Worker points, matched by username then Connect UUID, created on first sight

**Files:**
- Modify: `connect_labs/supply_chain/models.py` (`SupplyPoint`)
- Modify: `connect_labs/supply_chain/stock/operations.py` (`_SUPPLY_POINT_DATA`)
- Modify: `connect_labs/supply_chain/serializers.py` (`supply_point`)
- Create: `connect_labs/supply_chain/stock/services/workers.py`
- Create: `connect_labs/supply_chain/migrations/0037_supply_point_connect_user_uuid.py` (generated)
- Test: `connect_labs/supply_chain/tests/test_worker_points.py`

**Interfaces:**
- Consumes: `StockRepositoryMixin.upsert_supply_point(data)` (existing).
- Produces:
  - `SupplyPoint.connect_user_uuid: str`
  - `workers.worker_slug(opportunity_id: int, username: str) -> str`
  - `workers.WorkerIndex(access, opportunity_id)` with `.find(username, user_uuid) -> SupplyPoint | None`, `.ensure(username, user_uuid, parent: SupplyPoint) -> SupplyPoint | None`, and `.created: list[int]`

- [ ] **Step 1: Write the failing tests**

Create `connect_labs/supply_chain/tests/test_worker_points.py`:

```python
"""A worker's own supply point: found by username, then by Connect user id,
and made the first time the worker submits a visit.

THIS REPOSITORY IS PUBLIC. Every username and id here is invented.
"""

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.stock.services.workers import WorkerIndex, worker_slug

pytestmark = pytest.mark.django_db

PROGRAM = 10514
OPP = 10514
UUID_A = "6c1f1d0e-0000-4000-8000-00000000000a"
UUID_B = "6c1f1d0e-0000-4000-8000-00000000000b"


@pytest.fixture
def da():
    return SupplyDataAccess(program_id=PROGRAM, opportunity_id=OPP, caller=SYSTEM)


@pytest.fixture
def store():
    return SupplyPoint.objects.create(program_id=PROGRAM, slug="partner-store", name="Partner store", kind="regional_store", source="we_recorded")


def _worker(username, uuid=""):
    return SupplyPoint.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        slug=worker_slug(OPP, username),
        name=username,
        kind="user_held",
        connect_username=username,
        connect_user_uuid=uuid,
        source="connect_visit",
    )


def test_loading_the_index_is_one_query(da, store, django_assert_num_queries):
    _worker("worker-acacia")
    _worker("worker-baobab")
    with django_assert_num_queries(1):
        WorkerIndex(da, OPP)


def test_a_new_worker_gets_a_point_under_the_resupply_store(da, store):
    index = WorkerIndex(da, OPP)
    point = index.ensure("worker-acacia", UUID_A, parent=store)

    assert (point.kind, point.parent_id, point.opportunity_id, point.source) == ("user_held", store.pk, OPP, "connect_visit")
    assert (point.slug, point.connect_username, point.connect_user_uuid) == ("user-10514-worker-acacia", "worker-acacia", UUID_A)
    assert index.created == [point.pk]


def test_the_same_worker_is_found_again_without_a_second_point(da, store):
    index = WorkerIndex(da, OPP)
    first = index.ensure("worker-acacia", UUID_A, parent=store)
    again = WorkerIndex(da, OPP).ensure("worker-acacia", UUID_A, parent=store)
    assert again.pk == first.pk
    assert SupplyPoint.objects.filter(kind="user_held").count() == 1


def test_a_renamed_worker_is_found_by_uuid(da, store):
    known = _worker("worker-acacia", UUID_A)
    found = WorkerIndex(da, OPP).ensure("worker-acacia-2", UUID_A, parent=store)
    assert found.pk == known.pk
    assert SupplyPoint.objects.filter(kind="user_held").count() == 1


def test_a_matching_username_wins_over_a_uuid_that_names_someone_else(da, store):
    acacia = _worker("worker-acacia", UUID_A)
    _worker("worker-baobab", UUID_B)
    assert WorkerIndex(da, OPP).find("worker-acacia", UUID_B).pk == acacia.pk


def test_a_known_worker_gains_the_uuid_it_lacked(da, store):
    known = _worker("worker-acacia")
    WorkerIndex(da, OPP).ensure("worker-acacia", UUID_A, parent=store)
    known.refresh_from_db()
    assert known.connect_user_uuid == UUID_A


def test_a_visit_naming_nobody_resolves_to_nothing(da, store):
    assert WorkerIndex(da, OPP).ensure("", "", parent=store) is None
    assert not SupplyPoint.objects.filter(kind="user_held").exists()


def test_a_uuid_alone_finds_but_never_creates(da, store):
    assert WorkerIndex(da, OPP).ensure("", UUID_A, parent=store) is None
    known = _worker("worker-acacia", UUID_A)
    assert WorkerIndex(da, OPP).ensure("", UUID_A, parent=store).pk == known.pk


def test_another_opportunitys_worker_is_not_matched(da, store):
    SupplyPoint.objects.create(
        program_id=PROGRAM, opportunity_id=OPP + 1, slug="elsewhere", name="worker-acacia", kind="user_held",
        connect_username="worker-acacia", source="we_recorded",
    )
    assert WorkerIndex(da, OPP).find("worker-acacia", "") is None


def test_supply_point_upsert_carries_the_uuid(da):
    point = call_operation(
        "supply_point_upsert",
        da,
        {"data": {"slug": "user-10514-worker-cassia", "name": "worker-cassia", "kind": "user_held", "opportunity_id": OPP,
                  "connect_username": "worker-cassia", "connect_user_uuid": UUID_B, "source": "we_recorded"}},
    )
    assert point["connect_user_uuid"] == UUID_B
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_worker_points.py -q"`
Expected: FAIL with `ModuleNotFoundError: ...stock.services.workers`.

- [ ] **Step 3: Add the field**

In `connect_labs/supply_chain/models.py` `SupplyPoint`, after `connect_user_id`:

```python
    # The visit cache's `user_id` is Connect's user UUID, a string; the integer
    # `connect_user_id` above cannot hold it. Matched second, after the username.
    connect_user_uuid = models.CharField(max_length=64, blank=True, default="", db_default="", db_index=True)
```

In `SupplyPoint.clean`, change the condition to
`if self.kind == "user_held" and not (self.connect_username or self.connect_user_id or self.connect_user_uuid):`.

In `connect_labs/supply_chain/stock/operations.py` `_SUPPLY_POINT_DATA`, add `connect_user_uuid={"type": "string"},` after `connect_user_id=ID,`. In `serializers.supply_point`, add `"connect_user_uuid": obj.connect_user_uuid,` after `connect_user_id`.

- [ ] **Step 4: The index**

Create `connect_labs/supply_chain/stock/services/workers.py`:

```python
"""Finding -- and on first sight, making -- the supply point a worker's stock rests at.

Hand-made worker points never keep up with a real roster (design 2026-09-28
§2), so the reader makes one the first time a worker submits a visit: kind
`user_held`, under the rule's resupply store, slug `user-{opp}-{username}`
(the convention stock_report_ingest already used).

Matched on the username first and Connect's user UUID second: the username
is what every other supply screen and a distribution line use, and the UUID
is what survives a worker being renamed. A UUID alone finds a worker but
never makes one -- a point has to be named after somebody a person can
recognise.

Loaded once per run, so a thousand visits resolve their workers in memory
rather than with a query each.
"""

from django.utils.text import slugify

from connect_labs.supply_chain.models import SupplyPoint


def worker_slug(opportunity_id, username) -> str:
    return f"user-{opportunity_id}-{slugify(username)}"[:96]


class WorkerIndex:
    def __init__(self, access, opportunity_id):
        self.access = access
        self.opportunity_id = opportunity_id
        points = list(
            SupplyPoint.objects.filter(program_id=access.program_id, opportunity_id=opportunity_id, kind="user_held")
        )
        self.by_username = {p.connect_username: p for p in points if p.connect_username}
        self.by_uuid = {p.connect_user_uuid: p for p in points if p.connect_user_uuid}
        self.created: list[int] = []

    def find(self, username, user_uuid):
        found = self.by_username.get(username or "")
        if found is None and user_uuid:
            found = self.by_uuid.get(user_uuid)
        return found

    def ensure(self, username, user_uuid, parent):
        """The worker's point, made if new; None when the visit names nobody usable."""
        username = (username or "").strip()
        user_uuid = (user_uuid or "").strip()
        found = self.find(username, user_uuid)
        if found is not None:
            if user_uuid and not found.connect_user_uuid:
                found.connect_user_uuid = user_uuid
                found.save(update_fields=["connect_user_uuid", "updated_at"])
                self.by_uuid[user_uuid] = found
            return found
        if not username:
            return None
        point = self.access.upsert_supply_point(
            {
                "slug": worker_slug(self.opportunity_id, username),
                "name": username,
                "kind": "user_held",
                "opportunity_id": self.opportunity_id,
                "connect_username": username,
                "connect_user_uuid": user_uuid,
                "parent_supply_point_id": parent.pk,
                "source": "connect_visit",
            }
        )
        self.by_username[username] = point
        if user_uuid:
            self.by_uuid[user_uuid] = point
        self.created.append(point.pk)
        return point
```

- [ ] **Step 5: Generate the migration**

Run: `make manage CMD="makemigrations supply_chain --name supply_point_connect_user_uuid"`
Expected: `0037_supply_point_connect_user_uuid.py` with one `AddField` carrying `db_default=""`. Then `--check --dry-run` prints `No changes detected`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_worker_points.py connect_labs/supply_chain/tests/test_network_screens.py connect_labs/supply_chain/tests/test_supply_point_placement.py -q"`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add connect_labs/supply_chain/ 
make commit  # message: "feat(supply): worker points found by username then Connect UUID, made on first visit"
```

---
### Task 5: The visit reader: `WorkerVisit`, `visit_consumption_ingest`, command, beat task

**Files:**
- Modify: `connect_labs/supply_chain/models.py` (new `WorkerVisit` after `DispensingRule`)
- Create: `connect_labs/supply_chain/stock/services/visit_source.py`
- Create: `connect_labs/supply_chain/stock/services/visit_reader.py`
- Modify: `connect_labs/supply_chain/stock/visit_operations.py` (append the operation)
- Modify: `connect_labs/supply_chain/history/program.py`, `connect_labs/supply_chain/data_access.py` (`purge`)
- Create: `connect_labs/supply_chain/management/commands/supply_ingest_visit_consumption.py`
- Modify: `connect_labs/supply_chain/tasks.py`
- Create: `connect_labs/supply_chain/migrations/0038_worker_visit.py` (generated), `0039_seed_visit_consumption_beat_task.py` (hand-written)
- Modify: `connect_labs/supply_chain/tests/test_mcp_parity.py` (internal set)
- Test: `connect_labs/supply_chain/tests/test_visit_reader.py`, `connect_labs/supply_chain/tests/test_visit_consumption_ingest.py`

**Interfaces:**
- Consumes: `dispensing.evaluate`, `DISPENSED/NO_ANSWER/NOTHING_GIVEN/UNIT_REFUSED` (T3); `WorkerIndex` (T4); `posting.post_visit_consumption`, `post_visit_reversal` (T1); `DispensingRule` (T2).
- Produces:
  - model `WorkerVisit(program_id, opportunity_id, visit_id, xform_id, connect_username, supply_point, visit_date, status, form_name, outcomes, answers)`, unique `(program_id, visit_id)`; `outcomes` keyed `"item-<item_id>"`
  - `visit_reader.REVERSING_STATUSES = ("rejected", "duplicate")`, `visit_reader.APPROVED_STATUSES = ("approved", "over_limit")`
  - `visit_reader.outcome_key(item_id: int) -> str`
  - `visit_reader.ingest_visit_consumption(access, *, opportunity_id: int, visits: list[dict], until: date | None = None, today: date | None = None) -> dict` (report, keys below)
  - `visit_reader.run_scheduled() -> dict`
  - `visit_source.SYNTHETIC_TOKEN: str`, `visit_source.fetch_visits(opportunity_id, access_token, *, force_refresh=False) -> list[dict]`
  - operation `visit_consumption_ingest(opportunity_id, until?, refresh?)`, `internal=True`
  - celery task `connect_labs.supply_chain.tasks.ingest_visit_consumption`

Report keys: `opportunity_id, rules, visits_read, posted, estimated, reversed, skipped_already_posted, skipped_already_reversed, rejected_unposted, no_answer, nothing_given, before_active_from, after_until, future, undated, unmatched (list), unit_refused (list), reinstated_after_reversal (list of visit ids), created_supply_points (list)`. Task 6 adds `balances_recorded, receipts_recorded, reports_already_recorded`.

- [ ] **Step 1: Write the failing service tests**

Create `connect_labs/supply_chain/tests/test_visit_reader.py`:

```python
"""The visit reader, over visit dicts shaped exactly as fetch_raw_visits returns them.

THIS REPOSITORY IS PUBLIC. Every username, id and answer here is invented.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.models import Revision
from connect_labs.supply_chain.models import Commodity, DispensingRule, Item, Movement, SupplyPoint, WorkerVisit
from connect_labs.supply_chain.stock.services import ledger
from connect_labs.supply_chain.stock.services.dispensing import validate_lines
from connect_labs.supply_chain.stock.services.visit_reader import ingest_visit_consumption, outcome_key
from connect_labs.supply_chain.values import Quantity

pytestmark = pytest.mark.django_db

PROGRAM = 10515
OPP = 10515
TODAY = date(2026, 9, 28)
FROM = date(2026, 8, 1)
RUTF_PATH = "form.rutf_dispensing.rutf_sachets_dispensed"
RUTF_LINES = [{"kind": "stated", "paths": [RUTF_PATH], "unit": "sachet"}]
VITA_LINES = [{"kind": "protocol", "given_paths": ["form.vita_group.va_delivered"], "given_values": ["child_fine"], "quantity": "1", "unit": "capsule"}]


@pytest.fixture
def da():
    return SupplyDataAccess(program_id=PROGRAM, opportunity_id=OPP, caller=SYSTEM)


def _item(sku, slug, base, pack, per):
    commodity = Commodity.objects.create(scope_key=f"prog:{PROGRAM}", slug=slug, name=slug, base_unit=base, pack_unit=pack, base_per_pack=per)
    return Item.objects.create(scope_key=f"prog:{PROGRAM}", sku=sku, name=sku, commodity=commodity, base_unit=base, pack_unit=pack, base_per_pack=per)


@pytest.fixture
def rutf():
    return _item("rutf-150", "rutf", "sachet", "carton", 150)


@pytest.fixture
def vita():
    return _item("vita-500", "vitamin-a", "capsule", "bottle", 500)


@pytest.fixture
def store():
    return SupplyPoint.objects.create(program_id=PROGRAM, slug="partner-store", name="Partner store", kind="regional_store", source="we_recorded")


def _rule(item, store, lines, **extra):
    return DispensingRule.objects.create(
        program_id=PROGRAM, opportunity_id=OPP, item=item, lines=validate_lines(lines, item),
        resupply_point=store, active_from=extra.pop("active_from", FROM), **extra,
    )


@pytest.fixture
def rutf_rule(rutf, store):
    return _rule(rutf, store, RUTF_LINES)


def visit(vid, *, on="2026-09-20", username="worker-acacia", user_id="uuid-acacia", status="pending", name="Visit Form", answers=None, **extra):
    body = {"@name": name}
    for path, value in (answers or {}).items():
        node = body
        parts = path.split(".")[1:]
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return {
        "id": vid, "xform_id": f"xf-{vid}", "username": username, "user_id": user_id, "visit_date": on,
        "status": status, "flag_reason": {}, "status_modified_date": None,
        "form_json": {"id": f"xf-{vid}", "form": body}, **extra,
    }


def read(da, visits, **kwargs):
    return ingest_visit_consumption(da, opportunity_id=OPP, visits=visits, today=TODAY, **kwargs)


def _worker():
    return SupplyPoint.objects.get(kind="user_held", connect_username="worker-acacia")


def test_a_visit_posts_its_consumption_and_is_remembered(da, rutf, rutf_rule):
    report = read(da, [visit(9001, answers={RUTF_PATH: "14"})])

    assert (report["posted"], report["visits_read"], len(report["created_supply_points"])) == (1, 1, 1)
    movement = Movement.objects.get(kind="consumption")
    assert (movement.visit_id, movement.quantity, movement.quantity_unit, movement.from_supply_point_id) == ("9001", Decimal("14"), "sachet", _worker().pk)
    assert movement.occurred_on == date(2026, 9, 20)
    remembered = WorkerVisit.objects.get(visit_id="9001")
    assert (remembered.status, remembered.xform_id, remembered.supply_point_id) == ("pending", "xf-9001", _worker().pk)
    assert remembered.outcomes == {outcome_key(rutf.pk): "dispensed"}
    assert remembered.answers == {RUTF_PATH: "14"}


def test_reading_the_same_visits_again_writes_nothing(da, rutf, rutf_rule):
    visits = [visit(9001, answers={RUTF_PATH: "14"})]
    read(da, visits)
    movements, revisions = Movement.objects.count(), Revision.objects.count()

    report = read(da, visits)

    assert (report["posted"], report["skipped_already_posted"]) == (0, 1)
    assert (Movement.objects.count(), Revision.objects.count()) == (movements, revisions)


def test_a_rejected_visit_is_reversed_once(da, rutf, rutf_rule):
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])

    first = read(da, [visit(9001, status="rejected", answers={RUTF_PATH: "14"})])
    second = read(da, [visit(9001, status="rejected", answers={RUTF_PATH: "14"})])

    assert (first["reversed"], second["reversed"], second["skipped_already_reversed"]) == (1, 0, 1)
    assert ledger.balance(PROGRAM, _worker(), item=rutf) == Quantity(Decimal("0"), "sachet")
    assert WorkerVisit.objects.get(visit_id="9001").status == "rejected"


def test_a_visit_marked_duplicate_is_reversed(da, rutf, rutf_rule):
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])
    assert read(da, [visit(9001, status="duplicate", answers={RUTF_PATH: "14"})])["reversed"] == 1


def test_a_visit_flagged_as_a_duplicate_is_reversed(da, rutf, rutf_rule):
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])
    flagged = visit(9001, status="approved", answers={RUTF_PATH: "14"}, flag_reason={"flags": [["duplicate_submission", "already visited"]]})
    assert read(da, [flagged])["reversed"] == 1


def test_a_visit_first_seen_rejected_is_never_posted(da, rutf, rutf_rule):
    report = read(da, [visit(9001, status="rejected", answers={RUTF_PATH: "14"})])
    assert (report["posted"], report["rejected_unposted"]) == (0, 1)
    assert not Movement.objects.filter(kind="consumption").exists()


def test_no_answer_never_posts(da, rutf, rutf_rule):
    report = read(da, [visit(9001, answers={"form.other.question": "1"})])
    assert (report["posted"], report["no_answer"]) == (0, 1)
    assert not Movement.objects.filter(kind="consumption").exists()
    assert WorkerVisit.objects.get(visit_id="9001").outcomes == {outcome_key(rutf.pk): "no_answer"}


def test_active_from_is_respected(da, rutf, rutf_rule):
    report = read(da, [visit(9001, on="2026-07-31", answers={RUTF_PATH: "14"})])
    assert (report["posted"], report["before_active_from"]) == (0, 1)
    assert not WorkerVisit.objects.exists()


def test_a_visit_naming_no_worker_is_reported(da, rutf, rutf_rule):
    report = read(da, [visit(9001, username="", user_id="", answers={RUTF_PATH: "14"})])
    assert report["unmatched"] == [{"visit_id": "9001", "reason": "the visit names no worker (no username or user id)"}]
    assert not Movement.objects.filter(kind="consumption").exists()


def test_a_unit_the_item_cannot_convert_is_refused_and_reported(da, rutf, store):
    _rule(rutf, store, [{"kind": "stated", "paths": ["form.cartons"], "unit": "carton"}])
    Item.objects.filter(pk=rutf.pk).update(base_per_pack=None)
    Commodity.objects.filter(pk=rutf.commodity_id).update(base_per_pack=None)

    report = read(da, [visit(9001, answers={"form.cartons": "1"})])

    assert report["posted"] == 0
    assert report["unit_refused"][0]["visit_id"] == "9001"
    assert report["unit_refused"][0]["item_id"] == rutf.pk


def test_protocol_rows_are_marked_estimated(da, vita, store):
    _rule(vita, store, VITA_LINES)
    report = read(da, [visit(9001, answers={"form.vita_group.va_delivered": "child_fine"})])
    assert (report["posted"], report["estimated"]) == (1, 1)
    assert Movement.objects.get(kind="consumption").estimated is True


def test_a_second_rule_on_the_same_visit_posts_its_own_item(da, rutf, vita, store, rutf_rule):
    _rule(vita, store, VITA_LINES)
    read(da, [visit(9001, answers={RUTF_PATH: "14", "form.vita_group.va_delivered": "child_fine"})])
    assert sorted(Movement.objects.filter(kind="consumption").values_list("item_id", flat=True)) == sorted([rutf.pk, vita.pk])


def test_a_form_the_rule_does_not_read_is_neither_posted_nor_unanswered(da, rutf, store):
    _rule(rutf, store, RUTF_LINES, forms=["Visit Form"])
    report = read(da, [visit(9001, name="Stock Management", answers={"form.stock_balance.sachets_remaining": "40"})])
    assert (report["posted"], report["no_answer"]) == (0, 0)
    assert WorkerVisit.objects.get(visit_id="9001").outcomes == {}


def test_editing_a_rule_does_not_repost_old_visits(da, rutf, rutf_rule):
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])
    rutf_rule.lines = validate_lines([{"kind": "stated", "paths": [RUTF_PATH], "unit": "carton"}], rutf)
    rutf_rule.save()

    report = read(da, [visit(9001, answers={RUTF_PATH: "14"}), visit(9002, answers={RUTF_PATH: "1"})])

    assert (report["posted"], report["skipped_already_posted"]) == (1, 1)
    assert Movement.objects.get(visit_id="9001").quantity == Decimal("14")
    assert Movement.objects.get(visit_id="9002").quantity == Decimal("150")


def test_a_visit_reinstated_after_reversal_is_reported_not_reposted(da, rutf, rutf_rule):
    read(da, [visit(9001, answers={RUTF_PATH: "14"})])
    read(da, [visit(9001, status="rejected", answers={RUTF_PATH: "14"})])

    report = read(da, [visit(9001, status="approved", answers={RUTF_PATH: "14"})])

    assert report["reinstated_after_reversal"] == ["9001"]
    assert Movement.objects.filter(visit_id="9001").count() == 2
    assert ledger.balance(PROGRAM, _worker(), item=rutf) == Quantity(Decimal("0"), "sachet")


def test_future_and_undated_visits_are_skipped(da, rutf, rutf_rule):
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    report = read(
        da,
        [
            visit(9001, on=tomorrow, answers={RUTF_PATH: "14"}),
            visit(9002, on="", answers={RUTF_PATH: "14"}),
            visit(9003, on="not a date", answers={RUTF_PATH: "14"}),
            visit(None, answers={RUTF_PATH: "14"}),
        ],
    )
    assert (report["future"], report["undated"], report["posted"]) == (1, 3, 0)
    assert not WorkerVisit.objects.exists()


def test_until_reads_a_status_as_it_was_then(da, rutf, rutf_rule):
    later = visit(9001, status="rejected", answers={RUTF_PATH: "14"}, status_modified_date="2026-09-25T10:00:00")

    then = read(da, [later], until=date(2026, 9, 21))
    now = read(da, [later])

    assert (then["posted"], then["reversed"]) == (1, 0)
    assert now["reversed"] == 1


def test_visits_after_until_are_left_for_later(da, rutf, rutf_rule):
    report = read(da, [visit(9001, on="2026-09-22", answers={RUTF_PATH: "14"})], until=date(2026, 9, 21))
    assert (report["posted"], report["after_until"]) == (0, 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_visit_reader.py -q"`
Expected: FAIL with `ImportError: cannot import name 'WorkerVisit'`.

- [ ] **Step 3: The `WorkerVisit` model**

In `connect_labs/supply_chain/models.py` after `DispensingRule`:

```python
class WorkerVisit(TimestampedModel):
    """A Connect visit the stock reader has read, and what it made of it.

    The ledger cannot hold a visit's STATUS: a movement is append-only, and a
    visit goes pending -> approved (or rejected) after its stock has already
    left the bag. "How much of this figure is unapproved" (design §5) needs
    the status as it stands, so it lives here, updated on every read and
    revisioned like any supply record -- which is what lets an as-of page show
    the status a visit had that day.

    `outcomes` is {"item-<id>": dispensed | nothing_given | no_answer |
    unit_refused | reversed | not_counted}; `answers` holds only the answers
    at the rule's own paths (a count, a yes/no, an age in months), never the
    rest of the form.
    """

    program_id = models.IntegerField(db_index=True)
    opportunity_id = models.IntegerField(db_index=True)
    visit_id = models.CharField(max_length=64)
    xform_id = models.CharField(max_length=64, blank=True, default="")
    connect_username = models.CharField(max_length=150, blank=True, default="")
    supply_point = models.ForeignKey(SupplyPoint, on_delete=models.PROTECT, related_name="visits")
    visit_date = models.DateField(db_index=True)
    status = models.CharField(max_length=32, blank=True, default="")
    form_name = models.CharField(max_length=255, blank=True, default="")
    outcomes = models.JSONField(default=dict, blank=True)
    answers = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-visit_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["program_id", "visit_id"], name="uniq_worker_visit_program_visit")]
        indexes = [models.Index(fields=["supply_point", "visit_date"])]
```

`history/program.py` `PATHS`: add `"WorkerVisit": _direct,`. In `data_access.purge()`, add `WorkerVisit` to the models import and insert `drop("worker visits", WorkerVisit.objects.filter(program_id=program_id))` directly after the dispensing-rules drop (before supply points, which it PROTECTs).

Run `make manage CMD="makemigrations supply_chain --name worker_visit"`. Expected: `0038_worker_visit.py` with `CreateModel(name="WorkerVisit")`.

- [ ] **Step 4: The one place visits are read**

Create `connect_labs/supply_chain/stock/services/visit_source.py`:

```python
"""Where the stock reader gets visits: the analysis pipeline's raw-visit read.

Through `AnalysisPipeline.fetch_raw_visits`, which fills the shared visit
cache through `get_export_client` -- never a hand-built export client -- so a
synthetic opportunity's fixtures are read exactly as a real one's would be
(design §4.1). A synthetic client ignores the token; a real opportunity needs
one with the export scope, and the reader refuses real programmes for now.

Status changes reach the reader when the cached copy expires (or on
--refresh): the cache is shared with every pipeline on the opportunity, and a
forced walk per hourly run would re-download the whole opportunity each time.
"""

SYNTHETIC_TOKEN = "labs-synthetic-no-connect-token"


def fetch_visits(opportunity_id: int, access_token: str | None, *, force_refresh: bool = False) -> list[dict]:
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    pipeline = AnalysisPipeline(access_token=access_token or SYNTHETIC_TOKEN)
    return pipeline.fetch_raw_visits(opportunity_id=opportunity_id, force_refresh=force_refresh)
```

- [ ] **Step 5: The reader**

Create `connect_labs/supply_chain/stock/services/visit_reader.py`:

```python
"""Turning submitted visits into stock leaving each worker's bag (design 2026-09-28 §4).

For each visit on or after a rule's `active_from`:

- resolve the worker's point, making it on first sight (workers.WorkerIndex);
- for each active rule whose forms include this visit's form, sum the rule's
  lines (dispensing.evaluate) and post ONE consumption per item, dated the
  visit's day. Already posted: skip -- the rule may have been edited since,
  and a posted visit is not re-read;
- a visit `rejected`, `duplicate`, or flagged as a duplicate, whose
  consumption stands: post its reversal. A visit first seen rejected is never
  posted at all;
- no answer: no movement; counted, and remembered on the WorkerVisit.

Counted on arrival whatever the approval status (the owner's call,
2026-09-28): the sachets left the bag whether or not the visit is paid. How
much of a figure rests on unapproved visits is shown beside it (belief.py),
never folded in.

`until` replays history: visits after it are left for later, and a
rejection recorded after it (status_modified_date) is read as still pending.
The synthetic seeder uses it to build week-by-week history an as-of page can
walk back through.
"""

import logging
from datetime import date

from django.db import transaction
from django.utils import timezone

from connect_labs.supply_chain.models import DispensingRule, Movement, WorkerVisit
from connect_labs.supply_chain.stock.services import posting
from connect_labs.supply_chain.stock.services.dispensing import (
    DISPENSED,
    NO_ANSWER,
    NOTHING_GIVEN,
    UNIT_REFUSED,
    evaluate,
)
from connect_labs.supply_chain.stock.services.workers import WorkerIndex

logger = logging.getLogger(__name__)

REVERSING_STATUSES = ("rejected", "duplicate")
# over_limit is work reviewed clean that went past a payment cap: approved.
APPROVED_STATUSES = ("approved", "over_limit")


def outcome_key(item_id) -> str:
    return f"item-{item_id}"


def _day(value) -> date | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _flagged_duplicate(visit) -> bool:
    reason = visit.get("flag_reason")
    flags = reason.get("flags") if isinstance(reason, dict) else None
    for flag in flags or []:
        code = flag[0] if isinstance(flag, list | tuple) and flag else flag
        if "duplicate" in str(code).lower():
            return True
    return False


def visit_status(visit, until=None) -> str:
    """The visit's status -- as it stood on `until`, when given."""
    status = str(visit.get("status") or "").strip().lower()
    if status not in REVERSING_STATUSES and _flagged_duplicate(visit):
        status = "duplicate"
    if until is not None and status in REVERSING_STATUSES:
        changed = _day(visit.get("status_modified_date"))
        if changed is not None and changed > until:
            return "pending"
    return status


def _report(opportunity_id, visits, rules) -> dict:
    return {
        "opportunity_id": opportunity_id,
        "rules": len(rules),
        "visits_read": len(visits),
        "posted": 0,
        "estimated": 0,
        "reversed": 0,
        "skipped_already_posted": 0,
        "skipped_already_reversed": 0,
        "rejected_unposted": 0,
        "no_answer": 0,
        "nothing_given": 0,
        "before_active_from": 0,
        "after_until": 0,
        "future": 0,
        "undated": 0,
        "unmatched": [],
        "unit_refused": [],
        "reinstated_after_reversal": [],
        "created_supply_points": [],
    }


@transaction.atomic
def ingest_visit_consumption(access, *, opportunity_id, visits, until=None, today=None) -> dict:
    program_id = access._require_program()
    today = today or timezone.localdate()
    rules = list(
        DispensingRule.objects.filter(program_id=program_id, opportunity_id=opportunity_id, status="active")
        .select_related("item__commodity", "resupply_point")
        .order_by("pk")
    )
    report = _report(opportunity_id, visits, rules)
    if not rules:
        return report
    earliest = min(rule.active_from for rule in rules)

    ids = [str(v["id"]) for v in visits if v.get("id") not in (None, "")]
    posted = {
        (m.visit_id, m.item_id): m
        for m in Movement.objects.filter(program_id=program_id, visit_id__in=ids, kind="consumption", reverses__isnull=True)
    }
    reversed_ = set(
        Movement.objects.filter(program_id=program_id, visit_id__in=ids, reverses__isnull=False).values_list(
            "visit_id", "item_id"
        )
    )
    seen = {w.visit_id: w for w in WorkerVisit.objects.filter(program_id=program_id, visit_id__in=ids)}
    index = WorkerIndex(access, opportunity_id)

    for visit in visits:
        visit_id = str(visit.get("id") or "")
        on = _day(visit.get("visit_date"))
        if not visit_id or on is None:
            report["undated"] += 1
            continue
        if on > today:
            report["future"] += 1
            continue
        if until is not None and on > until:
            report["after_until"] += 1
            continue
        if on < earliest:
            report["before_active_from"] += 1
            continue

        status = visit_status(visit, until)
        form_json = visit.get("form_json") if isinstance(visit.get("form_json"), dict) else {}
        form_name = str((form_json.get("form") or {}).get("@name") or "")
        dated = [rule for rule in rules if on >= rule.active_from]
        reading = [rule for rule in dated if not rule.forms or form_name in rule.forms]

        point = index.ensure(visit.get("username"), str(visit.get("user_id") or ""), parent=dated[0].resupply_point)
        if point is None:
            report["unmatched"].append({"visit_id": visit_id, "reason": "the visit names no worker (no username or user id)"})
            continue

        outcomes, answers = {}, {}
        for rule in reading:
            key = (visit_id, rule.item_id)
            standing = posted.get(key)
            if status in REVERSING_STATUSES:
                if standing is None:
                    report["rejected_unposted"] += 1
                    outcomes[outcome_key(rule.item_id)] = "not_counted"
                elif key in reversed_:
                    report["skipped_already_reversed"] += 1
                else:
                    posting.post_visit_reversal(standing, reason=f"visit {status}")
                    reversed_.add(key)
                    report["reversed"] += 1
                    outcomes[outcome_key(rule.item_id)] = "reversed"
                continue
            if standing is not None:
                if key in reversed_:
                    report["reinstated_after_reversal"].append(visit_id)
                else:
                    report["skipped_already_posted"] += 1
                continue

            result = evaluate(rule.lines, form_json, rule.item)
            answers.update(result.answers)
            outcomes[outcome_key(rule.item_id)] = result.outcome
            if result.outcome == DISPENSED:
                posted[key] = posting.post_visit_consumption(
                    program_id=program_id,
                    opportunity_id=opportunity_id,
                    point=point,
                    item=rule.item,
                    quantity=result.quantity,
                    unit=result.unit,
                    occurred_on=on,
                    visit_id=visit_id,
                    estimated=result.estimated,
                )
                report["posted"] += 1
                report["estimated"] += int(result.estimated)
            elif result.outcome == NO_ANSWER:
                report["no_answer"] += 1
            elif result.outcome == NOTHING_GIVEN:
                report["nothing_given"] += 1
            elif result.outcome == UNIT_REFUSED:
                report["unit_refused"].append({"visit_id": visit_id, "item_id": rule.item_id, "reasons": list(result.reasons)})

        _remember(seen, access, opportunity_id, visit, visit_id, point, on, status, form_name, outcomes, answers)

    report["created_supply_points"] = list(index.created)
    if report["unmatched"] or report["unit_refused"] or report["reinstated_after_reversal"]:
        logger.warning(
            "supply visit reader, opportunity %s: %s unmatched, %s unit refusals, %s reinstated after reversal",
            opportunity_id,
            len(report["unmatched"]),
            len(report["unit_refused"]),
            len(report["reinstated_after_reversal"]),
        )
    return report


def _remember(seen, access, opportunity_id, visit, visit_id, point, on, status, form_name, outcomes, answers):
    """Create or update the WorkerVisit, saving only when something changed.

    An unchanged re-read writes nothing -- not even a revision -- which is what
    keeps an hourly run over the same visits quiet in the history.
    """
    existing = seen.get(visit_id)
    fields = {
        "xform_id": str(visit.get("xform_id") or (visit.get("form_json") or {}).get("id") or "")[:64],
        "connect_username": str(visit.get("username") or "")[:150],
        "supply_point": point,
        "visit_date": on,
        "status": status,
        "form_name": form_name[:255],
    }
    if existing is None:
        seen[visit_id] = WorkerVisit.objects.create(
            program_id=access.program_id,
            opportunity_id=opportunity_id,
            visit_id=visit_id,
            outcomes=outcomes,
            answers=answers,
            **fields,
        )
        return
    changed = [name for name, value in fields.items() if getattr(existing, name) != value]
    for name in changed:
        setattr(existing, name, fields[name])
    merged_outcomes = {**existing.outcomes, **outcomes}
    merged_answers = {**existing.answers, **answers}
    if merged_outcomes != existing.outcomes:
        existing.outcomes = merged_outcomes
        changed.append("outcomes")
    if merged_answers != existing.answers:
        existing.answers = merged_answers
        changed.append("answers")
    if changed:
        existing.save(update_fields=[*changed, "updated_at"])


def run_scheduled() -> dict:
    """The beat task's body: every opportunity with an active rule, synthetic programmes only."""
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain import scopes
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.operations import call_operation

    results = {}
    pairs = (
        DispensingRule.objects.filter(status="active")
        .values_list("program_id", "opportunity_id")
        .distinct()
        .order_by("program_id", "opportunity_id")
    )
    for program_id, opportunity_id in pairs:
        key = str(opportunity_id)
        if not scopes.is_synthetic(program_id):
            results[key] = {"skipped": "not a synthetic programme"}
            continue
        access = SupplyDataAccess(program_id=program_id, opportunity_id=opportunity_id, caller=SYSTEM)
        try:
            report = call_operation("visit_consumption_ingest", access, {"opportunity_id": opportunity_id}, channel="command")
        except Exception as error:  # one opportunity's failure must not stop the rest
            logger.exception("supply visit reader failed for opportunity %s", opportunity_id)
            results[key] = {"error": str(error)}
            continue
        results[key] = {k: report[k] for k in ("visits_read", "posted", "reversed", "no_answer")}
    return results
```

- [ ] **Step 6: Run the service tests to verify they pass**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_visit_reader.py -q"`
Expected: all PASS.

- [ ] **Step 7: Write the failing operation, command and task tests**

Create `connect_labs/supply_chain/tests/test_visit_consumption_ingest.py`:

```python
"""visit_consumption_ingest end to end: through the synthetic export client, as one recorded call.

THIS REPOSITORY IS PUBLIC. Every id and answer here is invented.
"""

from datetime import date
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django_celery_beat.models import PeriodicTask

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.models import OperationCall, Revision
from connect_labs.supply_chain.models import Commodity, DispensingRule, Item, Movement, SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.stock.services import visit_reader
from connect_labs.supply_chain.stock.services.dispensing import validate_lines

pytestmark = pytest.mark.django_db

OPP = 20871  # the programme is the opportunity's own
PATH = "form.rutf_dispensing.rutf_sachets_dispensed"


def export_record(vid, sachets, status="pending"):
    """One row as Connect's user_visits export (and a synthetic fixture) holds it."""
    return {
        "id": vid, "opportunity_id": OPP, "username": "worker-acacia", "user_id": "uuid-acacia",
        "visit_date": "2026-09-20", "status": status, "flag_reason": {}, "status_modified_date": "2026-09-20T12:00:00",
        "form_json": {"id": f"xf-{vid}", "form": {"@name": "Visit Form", "rutf_dispensing": {"rutf_sachets_dispensed": str(sachets)}}},
        "images": [],
    }


@pytest.fixture
def synthetic():
    SyntheticOpportunity.objects.create(opportunity_id=OPP, labs_only=True, enabled=True, label="reader tests", gdrive_folder_id="folder-test")
    registry.invalidate_cache()
    yield
    registry.invalidate_cache()


@pytest.fixture
def da(synthetic):
    return SupplyDataAccess(program_id=OPP, opportunity_id=OPP, caller=SYSTEM)


@pytest.fixture
def rule(da):
    commodity = Commodity.objects.create(scope_key=f"prog:{OPP}", slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton", base_per_pack=150)
    item = Item.objects.create(scope_key=f"prog:{OPP}", sku="rutf", name="RUTF", commodity=commodity, base_unit="sachet", pack_unit="carton", base_per_pack=150)
    store = SupplyPoint.objects.create(program_id=OPP, slug="partner-store", name="Partner store", kind="regional_store", source="we_recorded")
    return DispensingRule.objects.create(
        program_id=OPP, opportunity_id=OPP, item=item, resupply_point=store, active_from=date(2026, 8, 1),
        lines=validate_lines([{"kind": "stated", "paths": [PATH], "unit": "sachet"}], item),
    )


class FakeStore:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def load_endpoint(self, opp_id, endpoint_key):
        self.calls.append((opp_id, endpoint_key))
        return self.rows if endpoint_key == "user_visits" else []


def test_the_reader_reads_through_the_synthetic_export_client(da, rule):
    store = FakeStore([export_record(9001, 14)])
    with patch("connect_labs.labs.integrations.connect.factory._get_fixture_store", return_value=store):
        report = call_operation("visit_consumption_ingest", da, {"opportunity_id": OPP})

    assert report["posted"] == 1
    assert (OPP, "user_visits") in store.calls
    assert Movement.objects.get(kind="consumption").visit_id == "9001"


def test_the_run_is_one_recorded_call_and_its_rows_carry_revisions(da, rule):
    with patch("connect_labs.supply_chain.stock.services.visit_source.fetch_visits", return_value=[export_record(9001, 14)]):
        call_operation("visit_consumption_ingest", da, {"opportunity_id": OPP})

    call = OperationCall.objects.get(operation="visit_consumption_ingest")
    movement = Movement.objects.get(kind="consumption")
    assert Revision.objects.filter(call=call, object_id=str(movement.pk), action="create").exists()


def test_a_real_programme_is_refused(rule):
    real = SupplyDataAccess(program_id=263, opportunity_id=2230, caller=SYSTEM)
    with pytest.raises(ValueError, match="refusing"):
        call_operation("visit_consumption_ingest", real, {"opportunity_id": 2230})


def test_the_command_dry_run_keeps_nothing(da, rule, capsys):
    with patch("connect_labs.supply_chain.stock.services.visit_source.fetch_visits", return_value=[export_record(9001, 14)]):
        call_command("supply_ingest_visit_consumption", "--program", str(OPP), "--opportunity", str(OPP), "--dry-run")

    assert "would post 1" in capsys.readouterr().out
    assert not Movement.objects.filter(kind="consumption").exists()
    assert not OperationCall.objects.filter(operation="visit_consumption_ingest").exists()


def test_the_command_posts(da, rule, capsys):
    with patch("connect_labs.supply_chain.stock.services.visit_source.fetch_visits", return_value=[export_record(9001, 14)]):
        call_command("supply_ingest_visit_consumption", "--program", str(OPP), "--opportunity", str(OPP))
    assert "posted 1" in capsys.readouterr().out
    assert Movement.objects.filter(kind="consumption").count() == 1


def test_the_scheduled_run_reads_synthetic_programmes_and_skips_real_ones(da, rule):
    real_store = SupplyPoint.objects.create(program_id=263, slug="real-store", name="Real store", kind="regional_store", source="we_recorded")
    DispensingRule.objects.create(program_id=263, opportunity_id=2230, item=rule.item, resupply_point=real_store, active_from=date(2026, 8, 1), lines=rule.lines)

    with patch("connect_labs.supply_chain.stock.services.visit_source.fetch_visits", return_value=[export_record(9001, 14)]) as fetch:
        results = visit_reader.run_scheduled()

    assert results[str(OPP)]["posted"] == 1
    assert results["2230"] == {"skipped": "not a synthetic programme"}
    assert [c.args[0] for c in fetch.call_args_list] == [OPP]


def test_the_beat_schedule_exists():
    task = PeriodicTask.objects.get(name="supply_chain_visit_consumption")
    assert task.task == "connect_labs.supply_chain.tasks.ingest_visit_consumption"
```

In `connect_labs/supply_chain/tests/test_mcp_parity.py` `test_internal_operations_are_not_exposed_as_mcp_tools`, change the set to `{"catalogue_seed", "tracker_import", "stock_report_ingest", "visit_consumption_ingest"}`.

- [ ] **Step 8: Run them to verify they fail**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_visit_consumption_ingest.py -q"`
Expected: FAIL with `KeyError: 'visit_consumption_ingest'`.

- [ ] **Step 9: The operation**

Append to `connect_labs/supply_chain/stock/visit_operations.py`:

```python
@register_operation(
    name="visit_consumption_ingest",
    summary=(
        "Read an opportunity's visits and post what each gave out as consumption from the worker's own "
        "stock, per its dispensing rules. Idempotent per visit and item; a visit later rejected or marked "
        "duplicate gets a reversal. until replays history (visits after it wait; a later rejection reads "
        "as pending). Synthetic programmes only until the product owner says otherwise."
    ),
    input_schema=obj({"opportunity_id": ID, "until": _DATE, "refresh": {"type": "boolean"}}, required=("opportunity_id",)),
    is_write=True,
    internal=True,
)
def visit_consumption_ingest(access, opportunity_id, until=None, refresh=False):
    from datetime import date

    from connect_labs.supply_chain import scopes
    from connect_labs.supply_chain.stock.services import visit_reader, visit_source

    scopes.require_synthetic(access.program_id, "read visits into the stock ledger")
    visits = visit_source.fetch_visits(opportunity_id, access.access_token, force_refresh=refresh)
    return visit_reader.ingest_visit_consumption(
        access,
        opportunity_id=opportunity_id,
        visits=visits,
        until=date.fromisoformat(until) if until else None,
    )
```

- [ ] **Step 10: The command**

Create `connect_labs/supply_chain/management/commands/supply_ingest_visit_consumption.py`:

```python
"""Post consumption from an opportunity's visits, per its dispensing rules.

    make manage CMD="supply_ingest_visit_consumption --program 10000 --opportunity 10000 --dry-run"

Safe to re-run: posting is idempotent per visit and item. --dry-run runs the
whole thing inside a transaction it rolls back, so the report is exactly what
a real run would do and nothing -- not even the OperationCall -- is kept.

A real opportunity reads with an export-scoped token from SUPPLY_EXPORT_TOKEN
(never argv: a token on a command line lands in shell history); a synthetic
one needs none. Real programmes are refused by the operation until the
product owner says otherwise.
"""

import os

from django.core.management.base import BaseCommand
from django.db import transaction

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation


class Command(BaseCommand):
    help = "Post consumption from an opportunity's visits, per its dispensing rules."

    def add_arguments(self, parser):
        parser.add_argument("--program", type=int, required=True)
        parser.add_argument("--opportunity", type=int, required=True)
        parser.add_argument("--until", help="Read visits on or before this day (YYYY-MM-DD) only.")
        parser.add_argument("--refresh", action="store_true", help="Re-read visits rather than use the cached copy.")
        parser.add_argument("--dry-run", action="store_true", help="Run it all, report it, keep nothing.")

    def handle(self, *args, **options):
        access = SupplyDataAccess(
            access_token=os.environ.get("SUPPLY_EXPORT_TOKEN", ""),
            program_id=options["program"],
            opportunity_id=options["opportunity"],
            caller=SYSTEM,
        )
        payload = {
            "opportunity_id": options["opportunity"],
            "until": options.get("until"),
            "refresh": True if options["refresh"] else None,
        }
        with transaction.atomic():
            report = call_operation("visit_consumption_ingest", access, payload, channel="command")
            if options["dry_run"]:
                transaction.set_rollback(True)

        verb = "would post" if options["dry_run"] else "posted"
        self.stdout.write(
            f"{report['visits_read']} visit(s) read; {verb} {report['posted']} "
            f"({report['estimated']} estimated), reversed {report['reversed']}, "
            f"no answer {report['no_answer']}, nothing given {report['nothing_given']}, "
            f"before the rule began {report['before_active_from']}"
        )
        for row in report["unmatched"]:
            self.stdout.write(self.style.WARNING(f"  unmatched: visit {row['visit_id']} -- {row['reason']}"))
        for row in report["unit_refused"]:
            self.stdout.write(self.style.WARNING(f"  unit refused: visit {row['visit_id']} item {row['item_id']} -- {'; '.join(row['reasons'])}"))
        for visit_id in report["reinstated_after_reversal"]:
            self.stdout.write(self.style.WARNING(f"  reinstated after its reversal, not re-posted: visit {visit_id}"))
```

- [ ] **Step 11: The beat task and its schedule**

Append to `connect_labs/supply_chain/tasks.py`:

```python
@celery_app.task
def ingest_visit_consumption() -> dict:
    """Read every active dispensing rule's visits into the ledger. Hourly, by migration 0039.

    Synthetic programmes only (design 2026-09-28 §1); a real one is skipped by name.
    """
    from connect_labs.supply_chain.stock.services import visit_reader

    with audit_context(source="celery"):
        return visit_reader.run_scheduled()
```

Create `connect_labs/supply_chain/migrations/0039_seed_visit_consumption_beat_task.py`:

```python
"""Schedule the visit reader on celery beat, hourly.

The same pattern as 0012_seed_alert_beat_task: the worker runs beat with the
DatabaseScheduler, so the PeriodicTask row is what makes it fire. A run with
no active rule reads one table and stops.
"""

from django.db import migrations

TASK_NAME = "supply_chain_visit_consumption"
TASK_PATH = "connect_labs.supply_chain.tasks.ingest_visit_consumption"


def create_periodic_task(apps, schema_editor):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    schedule, _ = IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.HOURS)
    PeriodicTask.objects.update_or_create(
        name=TASK_NAME,
        defaults={"task": TASK_PATH, "interval": schedule, "crontab": None},
    )


def delete_periodic_task(apps, schema_editor):
    from django_celery_beat.models import PeriodicTask

    PeriodicTask.objects.filter(name=TASK_NAME, task=TASK_PATH).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("supply_chain", "0038_worker_visit"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(create_periodic_task, delete_periodic_task, hints={"run_on_secondary": False}),
    ]
```

- [ ] **Step 12: Run everything in this task**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_visit_reader.py connect_labs/supply_chain/tests/test_visit_consumption_ingest.py connect_labs/supply_chain/tests/test_mcp_parity.py connect_labs/supply_chain/tests/test_history_models.py connect_labs/supply_chain/tests/test_data_access.py -q"`
Expected: all PASS. If `test_the_reader_reads_through_the_synthetic_export_client` fails inside the SQL backend's cache fill, compare with `connect_labs/labs/synthetic/tests/test_visit_count_resync.py`, which patches the same `_get_fixture_store`. Do not bypass `fetch_visits`: this test is the spec's "synthetic routing test" (§8).

- [ ] **Step 13: Commit**

```bash
git add connect_labs/supply_chain/
make commit  # message: "feat(supply): read visits into the ledger -- consumption, reversals, a report per run"
```

---

### Task 6: What the worker's app says: `extract_rows` fix, app balances and reported receipts

**Files:**
- Modify: `connect_labs/supply_chain/records.py` (count kinds)
- Modify: `connect_labs/supply_chain/stock/services/ingest.py` (`extract_rows`, `existing_submission_ids`, `ingest_stock_reports(kind=)`)
- Modify: `connect_labs/supply_chain/stock/services/soh.py` (`last_count`), `stock/services/network.py` (`_latest_counts`)
- Modify: `connect_labs/supply_chain/stock/operations.py` (`_STOCK_COUNT_DATA.kind`)
- Modify: `connect_labs/supply_chain/stock/services/dispensing.py` (append `read_reports`, `read_date`)
- Modify: `connect_labs/supply_chain/stock/services/visit_reader.py` (collect and record reports)
- Modify: `connect_labs/supply_chain/management/commands/supply_ingest_stock_reports.py` (read through `visit_source`)
- Create: `connect_labs/supply_chain/migrations/0040_stock_count_reported_receipt.py` (generated)
- Test: `connect_labs/supply_chain/tests/test_worker_reports.py`

**Interfaces:**
- Consumes: `visit_source.fetch_visits` (T5), `first_answer`, `read_number` (T3), `ingest_visit_consumption` (T5).
- Produces:
  - `records.STOCK_COUNT_KINDS` gains `"reported_receipt"`; `records.ON_HAND_COUNT_KINDS = ("self_reported", "physical_count", "override")`
  - `ingest.ingest_stock_reports(..., kind="self_reported")`; `ingest.existing_submission_ids(program_id, ids, kind="self_reported", item=None)`
  - `ingest.extract_rows` reads `visit["form_json"]`; `form_submission_id` = xform id, falling back to `f"visit-{id}"`
  - `dispensing.read_reports(reports: dict, form_json: dict) -> {"balance": Decimal | None, "received": Decimal | None, "received_on": date | None}`; `dispensing.read_date(value) -> date | None`
  - reader report gains `balances_recorded`, `receipts_recorded`, `reports_already_recorded`

- [ ] **Step 1: Write the failing tests**

Create `connect_labs/supply_chain/tests/test_worker_reports.py`:

```python
"""What the worker's own app says: its running balance, and the stock it says it received.

THIS REPOSITORY IS PUBLIC. Every username, id and figure here is invented.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import jsonschema
import pytest
from django.core.management import call_command

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, DispensingRule, Item, Movement, StockCount, SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.stock.services import network, soh
from connect_labs.supply_chain.stock.services.dispensing import validate_lines, validate_reports
from connect_labs.supply_chain.stock.services.ingest import extract_rows
from connect_labs.supply_chain.stock.services.visit_reader import ingest_visit_consumption

pytestmark = pytest.mark.django_db

PROGRAM = 10516
OPP = 10516
TODAY = date(2026, 9, 28)


def real_shaped(vid, answers, xform="xf-1", on="2026-09-20", username="worker-acacia", name="Stock Management"):
    body = {"@name": name}
    for path, value in answers.items():
        node = body
        parts = path.split(".")[1:]
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return {"id": vid, "xform_id": xform, "username": username, "user_id": "uuid-a", "visit_date": on, "status": "pending", "flag_reason": {}, "form_json": {"id": xform, "form": body}}


# ---- extract_rows, the old path -------------------------------------------


def test_extract_rows_reads_the_answer_under_form_json():
    rows = extract_rows([real_shaped(9001, {"form.stock_balance.sachets_remaining": "40"}, xform="xf-9")], quantity_path="form.stock_balance.sachets_remaining")
    assert rows == [{"connect_username": "worker-acacia", "quantity": "40", "counted_on": "2026-09-20", "form_submission_id": "xf-9", "visit_id": "9001"}]


def test_a_top_level_path_reads_nothing_from_a_real_row():
    """The documented paths start `form.`; the old code applied them to the visit dict itself."""
    visit = real_shaped(9001, {"form.stock_balance.sachets_remaining": "40"})
    assert extract_rows([visit], quantity_path="stock_balance.sachets_remaining") == []


def test_a_row_with_no_xform_id_still_has_a_stable_submission_id():
    visit = real_shaped(9001, {"form.stock_balance.sachets_remaining": "40"})
    visit["xform_id"] = None
    del visit["form_json"]["id"]
    assert extract_rows([visit], quantity_path="form.stock_balance.sachets_remaining")[0]["form_submission_id"] == "visit-9001"


def test_the_stock_report_command_reads_through_the_visit_source():
    from connect_labs.supply_chain.management.commands import supply_ingest_stock_reports as command

    assert not hasattr(command, "ExportAPIClient")
    with patch.object(command, "fetch_visits", return_value=[]) as fetch, patch.dict("os.environ", {"SUPPLY_EXPORT_TOKEN": "t"}):
        call_command("supply_ingest_stock_reports", "--program", str(PROGRAM), "--opportunity", str(OPP), "--commodity", "rutf", "--unit", "sachet", "--quantity-path", "form.stock_balance.sachets_remaining")
    fetch.assert_called_once_with(OPP, "t")


# ---- reported receipts are not counts --------------------------------------


@pytest.fixture
def world():
    commodity = Commodity.objects.create(scope_key=f"prog:{PROGRAM}", slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton", base_per_pack=150)
    item = Item.objects.create(scope_key=f"prog:{PROGRAM}", sku="rutf", name="RUTF", commodity=commodity, base_unit="sachet", pack_unit="carton", base_per_pack=150)
    store = SupplyPoint.objects.create(program_id=PROGRAM, slug="partner-store", name="Partner store", kind="regional_store", source="we_recorded")
    return {"item": item, "store": store}


def _count(world, point, kind, quantity, on):
    return StockCount.objects.create(program_id=PROGRAM, supply_point=point, item=world["item"], commodity=world["item"].commodity, kind=kind, counted_on=on, quantity=Decimal(quantity), quantity_unit="sachet", source="commcare_form")


def test_a_reported_receipt_never_becomes_the_last_count(world):
    worker = SupplyPoint.objects.create(program_id=PROGRAM, opportunity_id=OPP, slug="w", name="w", kind="user_held", connect_username="w", source="we_recorded")
    _count(world, worker, "self_reported", 40, date(2026, 9, 1))
    _count(world, worker, "reported_receipt", 300, date(2026, 9, 10))

    assert soh.last_count(PROGRAM, worker, item=world["item"]).quantity == Decimal("40")
    assert network._latest_counts(PROGRAM, [worker], item=world["item"])[worker.pk].kind == "self_reported"


def test_a_receipt_cannot_be_typed_in_as_a_count(world):
    da = SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)
    with pytest.raises(jsonschema.ValidationError):
        call_operation(
            "stock_count_record",
            da,
            {"data": {"supply_point_id": world["store"].pk, "commodity_slug": "rutf", "kind": "reported_receipt", "counted_on": "2026-09-01", "quantity": "1", "quantity_unit": "sachet", "source": "we_recorded"}},
        )


# ---- the reader records what the app reports -------------------------------

REPORTS = {
    "balance_paths": ["form.var.new_stock_balance", "form.stock_balance.sachets_remaining"],
    "receipt": {"quantity_paths": ["form.current_stock.sachets_received"], "date_paths": ["form.current_stock.date_received"]},
}


@pytest.fixture
def rule(world):
    item = world["item"]
    return DispensingRule.objects.create(
        program_id=PROGRAM, opportunity_id=OPP, item=item, resupply_point=world["store"], active_from=date(2026, 8, 1),
        lines=validate_lines([{"kind": "stated", "paths": ["form.rutf_dispensing.rutf_sachets_dispensed"], "unit": "sachet"}], item),
        forms=["Visit Form"], reports=validate_reports(REPORTS),
    )


def read(visits):
    da = SupplyDataAccess(program_id=PROGRAM, opportunity_id=OPP, caller=SYSTEM)
    return ingest_visit_consumption(da, opportunity_id=OPP, visits=visits, today=TODAY)


def test_the_apps_balance_is_recorded_as_a_self_reported_count(rule):
    visit = real_shaped(9001, {"form.rutf_dispensing.rutf_sachets_dispensed": "14", "form.var.new_stock_balance": "86"}, xform="xf-9001", name="Visit Form")

    report = read([visit])
    again = read([visit])

    count = StockCount.objects.get(kind="self_reported")
    assert (count.quantity, count.counted_on, count.form_submission_id, count.visit_id) == (Decimal("86"), date(2026, 9, 20), "xf-9001", "9001")
    assert (report["balances_recorded"], again["balances_recorded"], again["reports_already_recorded"]) == (1, 0, 1)


def test_a_stock_management_form_records_both_its_receipt_and_its_balance(rule):
    visit = real_shaped(
        9002,
        {"form.current_stock.sachets_received": "300", "form.current_stock.date_received": "2026-09-18", "form.stock_balance.sachets_remaining": "320"},
        xform="xf-9002",
    )

    report = read([visit])

    receipt = StockCount.objects.get(kind="reported_receipt")
    assert (receipt.quantity, receipt.counted_on) == (Decimal("300"), date(2026, 9, 18))
    assert StockCount.objects.get(kind="self_reported").quantity == Decimal("320")
    assert (report["receipts_recorded"], report["balances_recorded"]) == (1, 1)
    # A receipt the worker reports is their account, never a ledger movement.
    assert not Movement.objects.filter(kind="distribution").exists()


def test_a_balance_of_zero_is_recorded_it_is_a_stockout(rule):
    read([real_shaped(9003, {"form.stock_balance.sachets_remaining": "0"}, xform="xf-9003")])
    assert StockCount.objects.get(kind="self_reported").quantity == Decimal("0")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_worker_reports.py -q"`
Expected: FAIL. The first test fails because `extract_rows` returns `[]`.

- [ ] **Step 3: The count kinds**

In `connect_labs/supply_chain/records.py`:

```python
# A worker's reported RECEIPT is kept beside the counts so the gap between it
# and what the store recorded is visible (design 2026-09-28 §3.4). It is not
# a statement of what is on hand, so every on-hand reader filters to
# ON_HAND_COUNT_KINDS.
STOCK_COUNT_KINDS = ("self_reported", "physical_count", "override", "reported_receipt")
ON_HAND_COUNT_KINDS = ("self_reported", "physical_count", "override")
```

In `stock/services/soh.py` `last_count`, add `.filter(kind__in=records.ON_HAND_COUNT_KINDS)` to `counts` (import `records`). In `stock/services/network.py`, change `_latest_counts` to:

```python
def _latest_counts(program_id, points, item=None, on_date=None):
    """{supply_point_id: StockCount} -- the most recent on-hand count per point, in one query."""
    counts = StockCount.objects.filter(
        program_id=program_id, supply_point__in=points, kind__in=records.ON_HAND_COUNT_KINDS
    ).order_by("supply_point_id", "-counted_on", "-id")
    if item is not None:
        counts = counts.filter(item=item)
    if on_date is not None:
        counts = counts.filter(counted_on__lte=on_date)
    latest: dict[int, StockCount] = {}
    for count in counts:
        latest.setdefault(count.supply_point_id, count)
    return latest
```

Also pass `on_date=on_date` in the one call inside `network_stock`, and import `records`. In `stock/operations.py` `_STOCK_COUNT_DATA`, change `kind={"enum": list(records.STOCK_COUNT_KINDS)}` to `kind={"enum": list(records.ON_HAND_COUNT_KINDS)}`.

Run `make manage CMD="makemigrations supply_chain --name stock_count_reported_receipt"`. Expected: `0040_stock_count_reported_receipt.py`, an `AlterField` on `stockcount.kind` choices. (`"reported_receipt"` is exactly 16 characters, which is the column's `max_length`.)

- [ ] **Step 4: Fix `extract_rows` and make idempotency kind-aware**

In `connect_labs/supply_chain/stock/services/ingest.py`:

```python
def existing_submission_ids(program_id, submission_ids, kind="self_reported", item=None) -> set[str]:
    """Which of these submissions already produced a count of this kind, in one query.

    Kind-aware because one Stock Management form carries both a receipt and a
    balance under the same submission id; keyed on the id alone, the second
    was skipped as "already ingested".
    """
    if not submission_ids:
        return set()
    counts = StockCount.objects.filter(program_id=program_id, kind=kind, form_submission_id__in=list(submission_ids))
    if item is not None:
        counts = counts.filter(item=item)
    return set(counts.values_list("form_submission_id", flat=True))
```

Add `kind="self_reported"` as a keyword to `ingest_stock_reports`, pass `kind=kind, item=item` to `existing_submission_ids`, and set `kind=kind` in the `StockCount.objects.create(...)` call (replacing the literal `"self_reported"`). Replace `extract_rows`:

```python
def extract_rows(visits, *, quantity_path, username_key="username", date_key="visit_date"):
    """Pull the reported quantity out of visit rows.

    The path is a form_json path (`form.stock.cartons_on_hand`): export and
    cache rows keep a submission's answers under `form_json`, so a path
    applied to the visit dict itself -- as this once did -- reads nothing, and
    "nobody reported" looks identical to nobody having reported.

    A visit that does not answer is skipped rather than read as zero. The
    submission id is the xform id (one form, one report); a row carrying none
    falls back to its visit id so a re-read still cannot double-post.
    """
    out = []
    for visit in visits:
        form_json = visit.get("form_json") if isinstance(visit.get("form_json"), dict) else {}
        raw = _dig(form_json, quantity_path)
        if raw in (None, ""):
            continue
        visit_id = str(visit.get("id") or "")
        xform_id = visit.get("xform_id") or form_json.get("id") or ""
        out.append(
            {
                "connect_username": visit.get(username_key) or visit.get("flw_username"),
                "quantity": raw,
                "counted_on": visit.get(date_key) or visit.get("visit_date"),
                "form_submission_id": str(xform_id) if xform_id else f"visit-{visit_id}",
                "visit_id": visit_id,
            }
        )
    return out
```

- [ ] **Step 5: Route the stock report command through `visit_source`**

In `connect_labs/supply_chain/management/commands/supply_ingest_stock_reports.py`: remove the `ExportAPIClient` import and `_base_url`. Add
`from connect_labs.supply_chain.stock.services.visit_source import fetch_visits`, and replace the Connect branch of `_visits` with:

```python
        # Through the analysis pipeline, so get_export_client decides between
        # Connect and a synthetic opportunity's fixtures (design §4, "Fix alongside").
        return fetch_visits(options["opportunity"], self._token(options))
```

Delete `--since` and its `params` handling, because the pipeline reads the whole opportunity. Update the module docstring's example accordingly. `_token` stays as it is.

- [ ] **Step 6: Read reports in the reader**

Append to `dispensing.py`:

```python
def read_date(value):
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def read_reports(reports, form_json) -> dict:
    """What the worker's app says on this visit: its balance, and any receipt."""
    found = {"balance": None, "received": None, "received_on": None}
    if not reports:
        return found
    if reports.get("balance_paths"):
        path, raw = first_answer(form_json, reports["balance_paths"])
        found["balance"] = read_number(raw) if path else None
    receipt = reports.get("receipt")
    if receipt:
        path, raw = first_answer(form_json, receipt["quantity_paths"])
        quantity = read_number(raw) if path else None
        if quantity is not None and quantity > 0:
            found["received"] = quantity
            date_path, date_raw = first_answer(form_json, receipt["date_paths"])
            found["received_on"] = read_date(date_raw) if date_path else None
    return found
```

(add `from datetime import date` at the top of `dispensing.py`).

In `visit_reader.py`, import `from connect_labs.supply_chain.stock.services import ingest` and `from connect_labs.supply_chain.stock.services.dispensing import base_unit, read_reports`. Add the three counters to `_report` (`"balances_recorded": 0, "receipts_recorded": 0, "reports_already_recorded": 0`). Before the visit loop, add `pending_reports: dict[int, dict] = {}`. Inside the loop, straight after `point` is resolved and not `None`:

```python
        submission = str(visit.get("xform_id") or form_json.get("id") or f"visit-{visit_id}")
        for rule in dated:
            if not rule.reports:
                continue
            said = read_reports(rule.reports, form_json)
            rows = pending_reports.setdefault(rule.pk, {"rule": rule, "balance": [], "receipt": []})
            common = {"connect_username": point.connect_username, "form_submission_id": submission, "visit_id": visit_id}
            if said["balance"] is not None:
                rows["balance"].append({**common, "quantity": str(said["balance"]), "counted_on": on.isoformat()})
            if said["received"] is not None:
                rows["receipt"].append({**common, "quantity": str(said["received"]), "counted_on": (said["received_on"] or on).isoformat()})
```

After the loop, before `report["created_supply_points"] = ...`:

```python
    for rows in pending_reports.values():
        rule = rows["rule"]
        for kind, counter, batch in (
            ("self_reported", "balances_recorded", rows["balance"]),
            ("reported_receipt", "receipts_recorded", rows["receipt"]),
        ):
            if not batch:
                continue
            # The existing stock_report_ingest path (design §3.4): a count beside
            # the ledger, never a movement.
            result = ingest.ingest_stock_reports(
                access,
                rows=batch,
                commodity_slug=rule.item.commodity.slug,
                quantity_unit=base_unit(rule.item),
                opportunity_id=opportunity_id,
                item_id=rule.item_id,
                kind=kind,
            )
            report[counter] += result["created"]
            report["reports_already_recorded"] += result["skipped_already_ingested"]
```

`ingest_stock_reports` resolves points by username through `access.get_supply_point_by_username`. Every row names a point the index has just resolved, so none come back unmatched. It stamps `source="commcare_form"`, which is correct: these are the app's own figures.

- [ ] **Step 7: Run the tests to verify they pass, with the suites that read counts**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_worker_reports.py connect_labs/supply_chain/tests/test_visit_reader.py connect_labs/supply_chain/tests/test_stock.py connect_labs/supply_chain/tests/test_stock_operations.py connect_labs/supply_chain/tests/test_stock_page_honesty.py connect_labs/supply_chain/tests/test_alerts.py -q"`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add connect_labs/supply_chain/
make commit  # message: "feat(supply): record the worker app's balance and reported receipts; read form_json in extract_rows"
```

---
### Task 7: What we believe: grouped SQL per point and the roll-up through the hierarchy

**Files:**
- Modify: `connect_labs/supply_chain/stock/services/resupply.py` (extract `rate_from`, `cover`)
- Create: `connect_labs/supply_chain/stock/services/belief.py`
- Modify: `connect_labs/supply_chain/stock/visit_operations.py` (`worker_stock`, `network_tree`)
- Test: `connect_labs/supply_chain/tests/test_belief.py`

**Interfaces:**
- Consumes: `MovementQuerySet.standing_consumption` (T1); `WorkerVisit`, `APPROVED_STATUSES`, `outcome_key` (T5); `network._latest_counts(program_id, points, item, on_date)` and `ON_HAND_COUNT_KINDS` (T6); `soh._variance` (existing).
- Produces:
  - `resupply.rate_from(total: Quantity | Unconfirmed, earliest: date | None, end: date, window_days: int, basis: str) -> Quantity | Unconfirmed`
  - `resupply.cover(on_hand, amc, basis, supply_point, item=None, window_days=DEFAULT_WINDOW_DAYS) -> dict` (the same keys `plan()` returns)
  - `belief.ISSUE_KINDS`, `belief.BELOW`, `belief.unit_of(item) -> str`
  - `@dataclass class Belief` (fields in Step 3)
  - `belief.beliefs_for(program_id, item, points, *, on_date=None, window_days=90) -> dict[int, Belief]`
  - `belief.worker_beliefs(program_id, item, *, opportunity_id=None, on_date=None, window_days=90) -> list[Belief]` (by name)
  - `belief.point_belief(program_id, point, item, *, on_date=None, window_days=90) -> Belief`
  - `belief.network_tree(program_id, item, *, on_date=None, window_days=90) -> list[Belief]` (roots; `.children`, `.subtree`, `.workers`, `.workers_below_min` filled)
  - `belief.unmatched_receipts(program_id, point, item, *, on_date=None) -> list[dict]`
  - `belief.wire(b: Belief) -> dict`
  - operations `worker_stock(item_id, opportunity_id?, as_of?, window_days?)` → `{item_id, item_name, unit, as_of, workers: [wire]}`, and `network_tree(item_id, as_of?, window_days?)` → `{item_id, item_name, unit, as_of, roots: [wire]}`

Every figure is in the item's **single unit** (sachets), at every level, so a store's row and its workers' rows can be read against each other. `variance` keeps the existing `stock_on_hand` sign, which is **reported − ledger** (see Deviations).

- [ ] **Step 1: Write the failing tests**

Create `connect_labs/supply_chain/tests/test_belief.py`:

```python
"""What we believe each worker holds, and every store above them.

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented.

The world: a central store feeds a partner store, which feeds two workers.
Worker A dispensed 100 sachets net (one visit rejected and reversed), 20 of
them on a visit still pending and 20 estimated from a protocol, and last
counted 190 against a ledger of 200. Worker B has dispensed for 16 days only.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, Item, Movement, StockCount, SupplyPoint, WorkerVisit
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.stock.services import belief, posting, resupply
from connect_labs.supply_chain.stock.services.visit_reader import outcome_key
from connect_labs.supply_chain.values import Quantity, Unconfirmed

pytestmark = pytest.mark.django_db

PROGRAM = 10517
OPP = 10517
TODAY = date(2026, 9, 28)


def ago(days):
    return TODAY - timedelta(days=days)


@pytest.fixture
def item():
    commodity = Commodity.objects.create(scope_key=f"prog:{PROGRAM}", slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton", base_per_pack=150)
    Item.objects.create(scope_key=f"prog:{PROGRAM}", sku="rutf", name="RUTF", commodity=commodity, base_unit="sachet", pack_unit="carton", base_per_pack=150)
    return Item.objects.select_related("commodity").get(sku="rutf")


def point(slug, kind, parent=None, **extra):
    return SupplyPoint.objects.create(program_id=PROGRAM, slug=slug, name=slug, kind=kind, parent=parent, source="we_recorded", **extra)


def move(item, kind, sachets, on, frm=None, to=None):
    return Movement.objects.create(
        program_id=PROGRAM, opportunity_id=OPP, kind=kind, occurred_on=on, from_supply_point=frm, to_supply_point=to,
        item=item, commodity=item.commodity, quantity=Decimal(sachets), quantity_unit="sachet", source="we_recorded",
    )


def dispense(item, worker, sachets, on, visit_id, status="approved", estimated=False):
    movement = posting.post_visit_consumption(
        program_id=PROGRAM, opportunity_id=OPP, point=worker, item=item, quantity=Decimal(sachets), unit="sachet",
        occurred_on=on, visit_id=visit_id, estimated=estimated,
    )
    WorkerVisit.objects.create(
        program_id=PROGRAM, opportunity_id=OPP, visit_id=visit_id, supply_point=worker, visit_date=on, status=status,
        outcomes={outcome_key(item.pk): "dispensed"},
    )
    return movement


@pytest.fixture
def world(item):
    central = point("central", "central_store", min_months_of_stock=Decimal("2"), max_months_of_stock=Decimal("6"))
    partner = point("partner", "regional_store", parent=central, min_months_of_stock=Decimal("1"), max_months_of_stock=Decimal("3"))
    a = point("worker-a", "user_held", parent=partner, opportunity_id=OPP, connect_username="worker-a", min_months_of_stock=Decimal("4"), max_months_of_stock=Decimal("8"))
    b = point("worker-b", "user_held", parent=partner, opportunity_id=OPP, connect_username="worker-b")
    move(item, "receipt", 1000, ago(80), to=central)
    move(item, "transfer", 600, ago(70), frm=central, to=partner)
    move(item, "distribution", 300, ago(60), frm=partner, to=a)
    move(item, "distribution", 100, ago(60), frm=partner, to=b)
    dispense(item, a, 30, ago(50), "v1")
    dispense(item, a, 30, ago(40), "v2")
    dispense(item, a, 20, ago(30), "v3", status="pending")
    dispense(item, a, 20, ago(20), "v4", estimated=True)
    rejected = dispense(item, a, 20, ago(10), "v5", status="rejected")
    posting.post_visit_reversal(rejected, reason="visit rejected")
    dispense(item, b, 10, ago(15), "v6")
    WorkerVisit.objects.create(program_id=PROGRAM, opportunity_id=OPP, visit_id="v7", supply_point=a, visit_date=ago(3), status="pending", outcomes={outcome_key(item.pk): "no_answer"})
    StockCount.objects.create(program_id=PROGRAM, supply_point=a, item=item, commodity=item.commodity, kind="self_reported", counted_on=ago(5), quantity=Decimal("190"), quantity_unit="sachet", source="commcare_form")
    return {"central": central, "partner": partner, "a": a, "b": b}


def sachets(n):
    return Quantity(Decimal(n), "sachet")


def test_a_workers_figures(item, world):
    a, b = belief.worker_beliefs(PROGRAM, item, on_date=TODAY)

    assert a.point.pk == world["a"].pk
    assert (a.issued, a.dispensed, a.unapproved, a.estimated) == (sachets(300), sachets(100), sachets(20), sachets(20))
    assert (a.on_hand, a.reported, a.reported_on, a.days_since_checked) == (sachets(200), sachets(190), ago(5), 5)
    assert a.variance == sachets(-10)
    assert a.no_answer_visits == 1
    assert a.amc == Quantity(Decimal("58.8235"), "sachet")
    assert a.status == "below_min"
    assert b.on_hand == sachets(90)
    assert isinstance(b.amc, Unconfirmed) and b.status == "unknown"


def test_every_points_cover_is_the_resupply_plans(item, world):
    found = belief.beliefs_for(PROGRAM, item, list(world.values()), on_date=TODAY)
    for name, pt in world.items():
        plan = resupply.plan(PROGRAM, pt, item=item, as_of=TODAY)
        mine = found[pt.pk]
        assert (mine.on_hand, mine.amc, mine.months_of_stock, mine.status) == (
            plan["on_hand"], plan["amc"], plan["months_of_stock"], plan["status"]
        ), name


def test_a_stores_subtree_is_its_own_stock_plus_its_childrens(item, world):
    (central,) = belief.network_tree(PROGRAM, item, on_date=TODAY)
    (partner,) = central.children

    assert partner.on_hand == sachets(200)
    assert partner.subtree["on_hand"] == sachets(490)
    assert central.subtree["on_hand"] == sachets(890)
    assert partner.subtree["dispensed"] == sachets(110)
    assert sorted(c.point.slug for c in partner.children) == ["worker-a", "worker-b"]


def test_subtree_cover_is_recomputed_from_its_consumption_not_summed(item, world):
    (central,) = belief.network_tree(PROGRAM, item, on_date=TODAY)
    (partner,) = central.children

    # 110 sachets dispensed below the partner since the first of them, 51 days ago.
    assert partner.subtree["amc"] == Quantity(Decimal("64.7059"), "sachet")
    assert partner.subtree["months_of_stock"] == Decimal("490") / Decimal("64.7059")


def test_workers_below_their_minimum_are_counted_up_the_tree(item, world):
    (central,) = belief.network_tree(PROGRAM, item, on_date=TODAY)
    (partner,) = central.children
    assert (partner.workers, partner.workers_below_min) == (2, 1)
    assert (central.workers, central.workers_below_min) == (2, 1)


def test_as_of_reproduces_a_past_day(item, world):
    a = belief.point_belief(PROGRAM, world["a"], item, on_date=ago(35))
    assert (a.on_hand, a.dispensed, a.reported) == (sachets(240), sachets(60), None)


def test_the_query_count_does_not_grow_with_the_workers(item, world):
    def add_workers(start, n):
        for i in range(start, start + n):
            w = point(f"worker-extra-{i}", "user_held", parent=world["partner"], opportunity_id=OPP, connect_username=f"worker-extra-{i}")
            move(item, "distribution", 50, ago(40), frm=world["partner"], to=w)
            dispense(item, w, 5, ago(30), f"extra-{i}")
            StockCount.objects.create(program_id=PROGRAM, supply_point=w, item=item, commodity=item.commodity, kind="self_reported", counted_on=ago(2), quantity=Decimal("45"), quantity_unit="sachet", source="commcare_form")

    with CaptureQueriesContext(connection) as few:
        belief.worker_beliefs(PROGRAM, item, on_date=TODAY)
    add_workers(0, 12)
    with CaptureQueriesContext(connection) as many:
        rows = belief.worker_beliefs(PROGRAM, item, on_date=TODAY)

    assert len(rows) == 14
    assert len(many.captured_queries) == len(few.captured_queries)


def test_a_reported_receipt_with_no_distribution_is_a_finding(item, world):
    for day, quantity in ((ago(58), "300"), (ago(3), "100")):
        StockCount.objects.create(program_id=PROGRAM, supply_point=world["a"], item=item, commodity=item.commodity, kind="reported_receipt", counted_on=day, quantity=Decimal(quantity), quantity_unit="sachet", source="commcare_form")

    found = belief.unmatched_receipts(PROGRAM, world["a"], item, on_date=TODAY)

    assert found == [{"reported_on": ago(3).isoformat(), "quantity": "100.0000", "unit": "sachet", "form_submission_id": ""}]


def test_the_operations_put_it_on_the_wire(item, world):
    da = SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)

    workers = call_operation("worker_stock", da, {"item_id": item.pk, "as_of": TODAY.isoformat()})
    tree = call_operation("network_tree", da, {"item_id": item.pk, "as_of": TODAY.isoformat()})

    first = workers["workers"][0]
    assert workers["unit"] == "sachet"
    assert first["on_hand"] == {"amount": "200", "unit": "sachet"}
    assert first["unapproved"] == {"amount": "20", "unit": "sachet"}
    assert first["reported_on"] == ago(5).isoformat()
    assert tree["roots"][0]["subtree"]["on_hand"] == {"amount": "890", "unit": "sachet"}
    assert tree["roots"][0]["children"][0]["workers_below_min"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_belief.py -q"`
Expected: FAIL with `ImportError: cannot import name 'belief'`.

- [ ] **Step 3: Extract `rate_from` and `cover` from `resupply`**

In `connect_labs/supply_chain/stock/services/resupply.py`, replace the body of `average_monthly_consumption` from `end = as_of or date.today()` onward, and all of `plan`, with the following. `blocked_on` and the band classification move into `cover` unchanged:

```python
def rate_from(total, earliest, end, window_days, basis):
    """A monthly rate from a window's total and the earliest demand ever recorded.

    The one rule, shared by one point's plan below and by the grouped figures
    in belief.py, so a worker's cover on the Workers page and on the resupply
    plan cannot disagree.
    """
    if earliest is None:
        return unconfirmed(NO_CONSUMPTION_YET)
    observed_days = min(window_days, (end - earliest).days + 1)
    if observed_days < MINIMUM_WINDOW_DAYS:
        what = "dispensing" if basis == CONSUMPTION else "releases"
        return unconfirmed(
            f"only {observed_days} days of {what} have been recorded here; "
            f"at least {MINIMUM_WINDOW_DAYS} are needed before a monthly rate means anything"
        )
    if isinstance(total, Unconfirmed):
        return total
    if total.amount == 0:
        return unconfirmed(f"no {basis} recorded in the window, so there is no rate to project")
    # Quantized for the same reason conversions are: a rate carried to 27
    # digits is false precision on a figure derived from counted cartons.
    rate = (total.amount / Decimal(observed_days) * DAYS_PER_MONTH).quantize(ledger.QUANTITY_SCALE)
    return Quantity(rate, total.unit)
```

`average_monthly_consumption` keeps its durable and short-window refusals, then ends:

```python
    end = as_of or date.today()
    start = end - timedelta(days=window_days)
    basis = demand_basis(program_id, supply_point, item=item)
    demand = _demand(program_id, supply_point, basis, item=item)
    earliest = demand.order_by("occurred_on").values_list("occurred_on", flat=True).first()
    by_unit = {unit[0]: total for unit, total in demand.between(start, end)._totals(["quantity_unit"]).items()}
    return rate_from(ledger.collapse(by_unit, item, None), earliest, end, window_days, basis)
```

`plan` becomes:

```python
def plan(program_id, supply_point, item=None, as_of=None, window_days=DEFAULT_WINDOW_DAYS) -> dict:
    on_hand = ledger.balance(program_id, supply_point, item=item, on_date=as_of)
    basis = demand_basis(program_id, supply_point, item=item)
    amc = average_monthly_consumption(program_id, supply_point, item=item, as_of=as_of, window_days=window_days)
    return cover(on_hand, amc, basis, supply_point, item=item, window_days=window_days)


def cover(on_hand, amc, basis, supply_point, item=None, window_days=DEFAULT_WINDOW_DAYS) -> dict:
    """Months of stock, days to stock-out, reorder point, resupply and status, from on-hand and a rate.

    `status` is a classification, not advice (design doc section 22).
    """
```

The body of `cover` is the old `plan` body from `if _is_durable(item):` to the final `return {...}`, moved verbatim. While moving it, drop the duplicated `"amc_basis": basis,` key in the two early returns. The existing callers (`resupply_plan`, `network_stock`) still call `plan`, so nothing else changes.

Run: `make test ARGS="connect_labs/supply_chain/tests/test_stock.py connect_labs/supply_chain/tests/test_stock_operations.py connect_labs/supply_chain/tests/test_stock_page_honesty.py connect_labs/supply_chain/tests/test_visit_movements.py -q"`
Expected: all PASS. This is a pure refactor.

- [ ] **Step 4: Write `belief.py`**

Create `connect_labs/supply_chain/stock/services/belief.py`:

```python
"""What we believe each worker holds -- and every store above them (design 2026-09-28 §5).

`network_stock` calls `resupply.plan` once per point: fine for thirty stores,
wrong for six hundred workers. This computes the same figures for a whole
set of points in a fixed number of grouped queries -- one pass per figure,
grouped by point and unit -- and then does the arithmetic in Python through
the SAME rules (`resupply.rate_from`, `resupply.cover`, `soh._variance`), so
the Workers page and a single point's resupply plan cannot disagree.

Per point, in the item's single unit:

  issued        receipts, issues, transfers, distributions and returns in
  dispensed     consumption out, net of reversals
    unapproved  of which on visits not (yet) approved   -- shown, never netted
    estimated   of which from protocol lines            -- shown, never netted
  on hand       the ledger balance
  reported      the latest on-hand count, its day, and the variance
  cover         months of stock, days to stock-out, status against the band

Stores carry the same figures summed over their subtree. Counts sum up the
hierarchy; cover is recomputed at each level from the subtree's own
consumption and never summed -- two workers at one month each are not a
store at two months.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Exists, Min, OuterRef, Q, Sum

from connect_labs.supply_chain.models import Movement, StockCount, SupplyPoint, WorkerVisit
from connect_labs.supply_chain.stock.services import ledger, resupply, soh
from connect_labs.supply_chain.stock.services.network import _latest_counts
from connect_labs.supply_chain.stock.services.visit_reader import APPROVED_STATUSES, outcome_key
from connect_labs.supply_chain.values import Quantity

ZERO = Decimal("0")
ISSUE_KINDS = ("receipt", "issue", "transfer", "distribution", "return")
BELOW = ("stockout", "below_min", "negative")
# A receipt a worker reports is matched to a distribution recorded in this window around it.
RECEIPT_MATCH_DAYS_BEFORE = 14
RECEIPT_MATCH_DAYS_AFTER = 3


def unit_of(item) -> str:
    return ledger._pack_spec(item)[1] or ledger.pack_unit_of(item) or ""


@dataclass
class Belief:
    point: SupplyPoint
    unit: str
    issued: object
    dispensed: object
    unapproved: object
    estimated: object
    no_answer_visits: int
    on_hand: object
    reported: Quantity | None
    reported_on: date | None
    reported_kind: str | None
    variance: object
    days_since_checked: int | None
    amc: object
    months_of_stock: object
    days_to_stockout: object
    status: str
    workers: int = 0
    workers_below_min: int = 0
    subtree: dict | None = None
    children: list = field(default_factory=list)


_SUMMED = ("in", "out", "issued", "dispensed", "unapproved", "estimated", "c_window", "r_window")


def _raw():
    return {**{key: {} for key in _SUMMED}, "c_earliest": None, "r_earliest": None, "dispenses": False, "no_answer": 0}


def _add(bucket, unit, amount):
    bucket[unit] = bucket.get(unit, ZERO) + (amount or ZERO)


def _earlier(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _raw_by_point(program_id, item, ids, on_date, start, end) -> dict:
    """Every per-point sum, in five grouped queries whatever the number of points."""
    raw = {pid: _raw() for pid in ids}
    moves = Movement.objects.for_program(program_id).as_of(on_date).filter(item=item)
    in_window = Q(occurred_on__gte=start, occurred_on__lte=end)

    inbound = moves.filter(to_supply_point_id__in=ids).values("to_supply_point_id", "kind", "quantity_unit")
    for row in inbound.annotate(total=Sum("quantity")):
        bucket = raw[row["to_supply_point_id"]]
        _add(bucket["in"], row["quantity_unit"], row["total"])
        if row["kind"] in ISSUE_KINDS:
            _add(bucket["issued"], row["quantity_unit"], row["total"])

    outbound = moves.filter(from_supply_point_id__in=ids).values("from_supply_point_id", "kind", "quantity_unit")
    for row in outbound.annotate(total=Sum("quantity")):
        bucket = raw[row["from_supply_point_id"]]
        _add(bucket["out"], row["quantity_unit"], row["total"])
        if row["kind"] == "consumption":
            bucket["dispenses"] = True

    releases = moves.filter(from_supply_point_id__in=ids, kind__in=resupply.RELEASE_KINDS, to_supply_point__isnull=False)
    for row in releases.values("from_supply_point_id", "quantity_unit").annotate(
        window=Sum("quantity", filter=in_window), earliest=Min("occurred_on")
    ):
        bucket = raw[row["from_supply_point_id"]]
        _add(bucket["r_window"], row["quantity_unit"], row["window"])
        bucket["r_earliest"] = _earlier(bucket["r_earliest"], row["earliest"])

    unapproved = Exists(
        WorkerVisit.objects.filter(program_id=program_id, visit_id=OuterRef("visit_id")).exclude(status__in=APPROVED_STATUSES)
    )
    standing = moves.standing_consumption().filter(from_supply_point_id__in=ids).annotate(unapproved=unapproved)
    for row in standing.values("from_supply_point_id", "quantity_unit", "estimated", "unapproved").annotate(
        total=Sum("quantity"), window=Sum("quantity", filter=in_window), earliest=Min("occurred_on")
    ):
        bucket = raw[row["from_supply_point_id"]]
        unit = row["quantity_unit"]
        _add(bucket["dispensed"], unit, row["total"])
        _add(bucket["c_window"], unit, row["window"])
        bucket["c_earliest"] = _earlier(bucket["c_earliest"], row["earliest"])
        if row["estimated"]:
            _add(bucket["estimated"], unit, row["total"])
        if row["unapproved"]:
            _add(bucket["unapproved"], unit, row["total"])

    unanswered = WorkerVisit.objects.filter(
        program_id=program_id, supply_point_id__in=ids, **{f"outcomes__{outcome_key(item.pk)}": "no_answer"}
    )
    if on_date is not None:
        unanswered = unanswered.filter(visit_date__lte=on_date)
    for row in unanswered.values("supply_point_id").annotate(n=Count("id")):
        raw[row["supply_point_id"]]["no_answer"] = row["n"]
    return raw


def _balance(raw) -> dict:
    return {u: raw["in"].get(u, ZERO) - raw["out"].get(u, ZERO) for u in set(raw["in"]) | set(raw["out"])}


def _own_cover(point, raw, item, unit, end, window_days):
    on_hand = ledger.collapse(_balance(raw), item, unit)
    if raw["dispenses"]:
        basis, total, earliest = resupply.CONSUMPTION, raw["c_window"], raw["c_earliest"]
    else:
        basis, total, earliest = resupply.RELEASES, raw["r_window"], raw["r_earliest"]
    amc = resupply.DURABLE if resupply._is_durable(item) else resupply.rate_from(
        ledger.collapse(total, item, unit), earliest, end, window_days, basis
    )
    return on_hand, resupply.cover(on_hand, amc, basis, point, item=item, window_days=window_days)


def _belief(point, raw, count, item, unit, end, window_days) -> Belief:
    on_hand, plan = _own_cover(point, raw, item, unit, end, window_days)
    reported = Quantity(count.quantity, count.quantity_unit) if count else None
    return Belief(
        point=point,
        unit=unit,
        issued=ledger.collapse(raw["issued"], item, unit),
        dispensed=ledger.collapse(raw["dispensed"], item, unit),
        unapproved=ledger.collapse(raw["unapproved"], item, unit),
        estimated=ledger.collapse(raw["estimated"], item, unit),
        no_answer_visits=raw["no_answer"],
        on_hand=on_hand,
        reported=reported,
        reported_on=count.counted_on if count else None,
        reported_kind=count.kind if count else None,
        variance=soh._variance(on_hand, reported, item) if count else None,
        days_since_checked=(end - count.counted_on).days if count else None,
        amc=plan["amc"],
        months_of_stock=plan["months_of_stock"],
        days_to_stockout=plan["days_to_stockout"],
        status=plan["status"],
    )


def _window(on_date, window_days):
    end = on_date or date.today()
    return end - timedelta(days=window_days), end


def beliefs_for(program_id, item, points, *, on_date=None, window_days=resupply.DEFAULT_WINDOW_DAYS) -> dict:
    points = list(points)
    if not points:
        return {}
    start, end = _window(on_date, window_days)
    unit = unit_of(item)
    raw = _raw_by_point(program_id, item, [p.pk for p in points], on_date, start, end)
    counts = _latest_counts(program_id, points, item=item, on_date=on_date)
    return {p.pk: _belief(p, raw[p.pk], counts.get(p.pk), item, unit, end, window_days) for p in points}


def worker_beliefs(program_id, item, *, opportunity_id=None, on_date=None, window_days=resupply.DEFAULT_WINDOW_DAYS):
    """Every active worker point, by name. No other order: nothing here ranks workers (§22)."""
    points = SupplyPoint.objects.filter(program_id=program_id, status="active", kind="user_held")
    if opportunity_id is not None:
        points = points.filter(opportunity_id=opportunity_id)
    points = list(points.order_by("name", "pk"))
    found = beliefs_for(program_id, item, points, on_date=on_date, window_days=window_days)
    return [found[p.pk] for p in points]


def point_belief(program_id, point, item, *, on_date=None, window_days=resupply.DEFAULT_WINDOW_DAYS) -> Belief:
    return beliefs_for(program_id, item, [point], on_date=on_date, window_days=window_days)[point.pk]


def _merge(total, raw):
    for key in _SUMMED:
        for unit, amount in raw[key].items():
            _add(total[key], unit, amount)
    total["c_earliest"] = _earlier(total["c_earliest"], raw["c_earliest"])
    total["dispenses"] = total["dispenses"] or raw["dispenses"]
    total["no_answer"] += raw["no_answer"]


def _subtree_figures(point, total, item, unit, end, window_days) -> dict:
    """A store and everything below it. The rate is the subtree's CONSUMPTION:
    releases between points inside it move stock around, they do not use it."""
    on_hand = ledger.collapse(_balance(total), item, unit)
    amc = resupply.DURABLE if resupply._is_durable(item) else resupply.rate_from(
        ledger.collapse(total["c_window"], item, unit), total["c_earliest"], end, window_days, resupply.CONSUMPTION
    )
    plan = resupply.cover(on_hand, amc, resupply.CONSUMPTION, point, item=item, window_days=window_days)
    return {
        "on_hand": on_hand,
        "dispensed": ledger.collapse(total["dispensed"], item, unit),
        "unapproved": ledger.collapse(total["unapproved"], item, unit),
        "estimated": ledger.collapse(total["estimated"], item, unit),
        "no_answer_visits": total["no_answer"],
        "amc": plan["amc"],
        "months_of_stock": plan["months_of_stock"],
        "days_to_stockout": plan["days_to_stockout"],
        "status": plan["status"],
    }


def network_tree(program_id, item, *, on_date=None, window_days=resupply.DEFAULT_WINDOW_DAYS) -> list[Belief]:
    """The network from its top points down, each store carrying its subtree's figures."""
    points = list(
        SupplyPoint.objects.filter(program_id=program_id, status="active").exclude(kind="in_transit").order_by("name", "pk")
    )
    if not points:
        return []
    start, end = _window(on_date, window_days)
    unit = unit_of(item)
    raw = _raw_by_point(program_id, item, [p.pk for p in points], on_date, start, end)
    counts = _latest_counts(program_id, points, item=item, on_date=on_date)
    beliefs = {p.pk: _belief(p, raw[p.pk], counts.get(p.pk), item, unit, end, window_days) for p in points}
    children: dict = {}
    for p in points:
        children.setdefault(p.parent_id, []).append(p)
    present = set(beliefs)

    def roll(p, seen):
        if p.pk in seen:  # a parent loop would recurse forever; stop at the repeat
            return _raw()
        seen = seen | {p.pk}
        node = beliefs[p.pk]
        total = _raw()
        _merge(total, raw[p.pk])
        for child in children.get(p.pk, []):
            _merge(total, roll(child, seen))
            below = beliefs[child.pk]
            node.children.append(below)
            if child.kind == "user_held":
                node.workers += 1
                node.workers_below_min += int(below.status in BELOW)
            node.workers += below.workers
            node.workers_below_min += below.workers_below_min
        if node.children:
            node.subtree = _subtree_figures(p, total, item, unit, end, window_days)
        return total

    roots = [p for p in points if p.parent_id not in present]
    for root in roots:
        roll(root, frozenset())
    return [beliefs[r.pk] for r in roots]


def unmatched_receipts(program_id, point, item, *, on_date=None) -> list[dict]:
    """Receipts the worker reported that no recorded distribution explains (design §3.4)."""
    receipts = StockCount.objects.filter(program_id=program_id, supply_point=point, item=item, kind="reported_receipt")
    if on_date is not None:
        receipts = receipts.filter(counted_on__lte=on_date)
    arrivals = set(
        Movement.objects.for_program(program_id)
        .as_of(on_date)
        .filter(to_supply_point=point, item=item, kind__in=ISSUE_KINDS)
        .values_list("occurred_on", flat=True)
    )
    found = []
    for receipt in receipts.order_by("counted_on", "pk"):
        earliest = receipt.counted_on - timedelta(days=RECEIPT_MATCH_DAYS_BEFORE)
        latest = receipt.counted_on + timedelta(days=RECEIPT_MATCH_DAYS_AFTER)
        if not any(earliest <= day <= latest for day in arrivals):
            found.append(
                {
                    "reported_on": receipt.counted_on.isoformat(),
                    "quantity": str(receipt.quantity),
                    "unit": receipt.quantity_unit,
                    "form_submission_id": receipt.form_submission_id,
                }
            )
    return found


def wire(b: Belief) -> dict:
    from connect_labs.supply_chain.operations import figure
    from connect_labs.supply_chain.stock.operations import _plain

    def band(value):
        return str(value) if value is not None else None

    return {
        "supply_point_id": b.point.pk,
        "name": b.point.name,
        "kind": b.point.kind,
        "connect_username": b.point.connect_username,
        "opportunity_id": b.point.opportunity_id,
        "parent_supply_point_id": b.point.parent_id,
        "unit": b.unit,
        "issued": figure(b.issued),
        "dispensed": figure(b.dispensed),
        "unapproved": figure(b.unapproved),
        "estimated": figure(b.estimated),
        "no_answer_visits": b.no_answer_visits,
        "on_hand": figure(b.on_hand),
        "reported": figure(b.reported) if b.reported is not None else None,
        "reported_on": b.reported_on.isoformat() if b.reported_on else None,
        "reported_kind": b.reported_kind,
        "variance": figure(b.variance) if b.variance is not None else None,
        "days_since_checked": b.days_since_checked,
        "amc": figure(b.amc),
        "months_of_stock": _plain(b.months_of_stock),
        "days_to_stockout": _plain(b.days_to_stockout),
        "status": b.status,
        "min_months_of_stock": band(b.point.min_months_of_stock),
        "max_months_of_stock": band(b.point.max_months_of_stock),
        "workers": b.workers,
        "workers_below_min": b.workers_below_min,
        "subtree": (
            {
                key: (value if key in ("status", "no_answer_visits") else _plain(value))
                for key, value in b.subtree.items()
            }
            if b.subtree
            else None
        ),
        "children": [wire(child) for child in b.children],
    }
```

`test_a_workers_figures` expects `58.8235`. `100 / 51 * 30` quantized to four places is 58.8235. `test_subtree_cover…` expects `64.7059` (`110 / 51 * 30`). If an assertion is off by one in the last place, the test is wrong, not the code: recompute with `Decimal`.

- [ ] **Step 5: The operations**

Append to `connect_labs/supply_chain/stock/visit_operations.py`:

```python
_WINDOW = {"type": "integer", "minimum": 30, "maximum": 730}


def _on(as_of):
    from datetime import date

    return date.fromisoformat(as_of) if as_of else None


@register_operation(
    name="worker_stock",
    summary=(
        "What we believe each field worker holds of one item: issued, dispensed (and how much of that "
        "rests on unapproved visits or protocol estimates), ledger on hand, the last count and its "
        "variance, days since checked, months of cover and days to stock-out. One row per worker, by "
        "name -- nothing is ranked. as_of reads a past day."
    ),
    input_schema=obj({"item_id": ID, "opportunity_id": ID, "as_of": _DATE, "window_days": _WINDOW}, required=("item_id",)),
)
def worker_stock(access, item_id, opportunity_id=None, as_of=None, window_days=90):
    from connect_labs.supply_chain.stock.services import belief

    item = access._resolve_item(item_id)
    rows = belief.worker_beliefs(access._require_program(), item, opportunity_id=opportunity_id, on_date=_on(as_of), window_days=window_days)
    return {"item_id": item.pk, "item_name": item.name, "unit": belief.unit_of(item), "as_of": as_of, "workers": [belief.wire(r) for r in rows]}


@register_operation(
    name="network_tree",
    summary=(
        "The network from central store to workers for one item. Each point carries its own figures; a "
        "store also carries its subtree's -- on hand and dispensed summed, cover recomputed from the "
        "subtree's own consumption (never summed), and how many workers below it are under their minimum."
    ),
    input_schema=obj({"item_id": ID, "as_of": _DATE, "window_days": _WINDOW}, required=("item_id",)),
)
def network_tree(access, item_id, as_of=None, window_days=90):
    from connect_labs.supply_chain.stock.services import belief

    item = access._resolve_item(item_id)
    roots = belief.network_tree(access._require_program(), item, on_date=_on(as_of), window_days=window_days)
    return {"item_id": item.pk, "item_name": item.name, "unit": belief.unit_of(item), "as_of": as_of, "roots": [belief.wire(r) for r in roots]}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_belief.py connect_labs/supply_chain/tests/test_mcp_parity.py connect_labs/supply_chain/tests/test_stock.py connect_labs/supply_chain/tests/test_stock_operations.py -q"`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add connect_labs/supply_chain/
make commit  # message: "feat(supply): what we believe each worker holds, in grouped SQL, rolled up the network"
```

---
### Task 8: Screens: network tree, Workers, Worker (with timeline chart), map markers

**Files:**
- Create: `connect_labs/supply_chain/stock/services/timeline.py`
- Modify: `connect_labs/supply_chain/stock/visit_operations.py` (`worker_stock_get`)
- Modify: `connect_labs/supply_chain/stock/visit_views.py` (`WorkersView`, `WorkerDetailView`, shared `_items` helper)
- Modify: `connect_labs/supply_chain/network/views.py` (`NetworkView` adds the tree)
- Create: `connect_labs/templates/supply_chain/workers.html`, `worker_detail.html`, `_network_node.html`
- Modify: `connect_labs/templates/supply_chain/network.html`
- Modify: `connect_labs/supply_chain/urls.py`, `connect_labs/supply_chain/navigation.py`
- Create: `connect_labs/static/supply_chain/marker_size.js`, `connect_labs/static/supply_chain/marker_size.test.js`
- Modify: `connect_labs/static/supply_chain/portfolio_map.js` (~2279–2305), `connect_labs/templates/supply_chain/portfolio_map.html` (~158)
- Test: `connect_labs/supply_chain/tests/test_timeline.py`, `connect_labs/supply_chain/tests/test_worker_screens.py`

**Interfaces:**
- Consumes: `belief.point_belief`, `belief.unmatched_receipts`, `belief.wire`, `belief.unit_of`, `belief.ISSUE_KINDS` (T7); `WorkerVisit`, `APPROVED_STATUSES`, `outcome_key` (T5); operations `worker_stock`, `network_tree`, `dispensing_rule_list`.
- Produces:
  - `timeline.worker_timeline(program_id, point, item, *, on_date=None) -> {"unit", "days": [{"on", "before", "issued", "dispensed", "other", "balance"}], "counts": [{"on", "quantity", "kind"}], "unconverted": [..]}` (all strings)
  - `timeline.timeline_svg(line: dict, *, width=720, height=220) -> str` ("" when there is nothing to draw)
  - operation `worker_stock_get(supply_point_id, item_id, as_of?, window_days?)` → `{worker: wire, timeline, visits: [...], arrivals: [...], unmatched_receipts: [...]}`
  - URL names `supply_chain:workers`, `supply_chain:worker_detail` (`workers/<int:supply_point_id>/`)
  - `window.SupplyMarkerSize.markerRadius(kind, amount, biggest) -> number`, `.biggestKey(kind, unit) -> string`

No chart library is loaded on any supply page (portfolio_map loads Mapbox only). The timeline is therefore server-rendered inline SVG. It renders inside the as-of rewind like every other part of the page, has no client state and can be tested as a string.

- [ ] **Step 1: Write the failing timeline tests**

Create `connect_labs/supply_chain/tests/test_timeline.py`:

```python
"""A worker's stock by day, and the chart drawn from it.

THIS REPOSITORY IS PUBLIC. Every figure here is invented.
"""

from datetime import date
from decimal import Decimal

import pytest

from connect_labs.supply_chain.models import Commodity, Item, Movement, StockCount, SupplyPoint
from connect_labs.supply_chain.stock.services import posting
from connect_labs.supply_chain.stock.services.timeline import timeline_svg, worker_timeline

PROGRAM = 10518


@pytest.fixture
def world(db):
    commodity = Commodity.objects.create(scope_key=f"prog:{PROGRAM}", slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton", base_per_pack=150)
    item = Item.objects.create(scope_key=f"prog:{PROGRAM}", sku="rutf", name="RUTF", commodity=commodity, base_unit="sachet", pack_unit="carton", base_per_pack=150)
    store = SupplyPoint.objects.create(program_id=PROGRAM, slug="store", name="store", kind="regional_store", source="we_recorded")
    worker = SupplyPoint.objects.create(program_id=PROGRAM, opportunity_id=PROGRAM, slug="w", name="w", kind="user_held", connect_username="w", parent=store, source="we_recorded")
    Movement.objects.create(program_id=PROGRAM, kind="distribution", occurred_on=date(2026, 9, 1), from_supply_point=store, to_supply_point=worker, item=item, commodity=commodity, quantity=Decimal("1"), quantity_unit="carton", source="we_recorded")
    for day, sachets, vid in ((3, 14, "a"), (5, 14, "b")):
        posting.post_visit_consumption(program_id=PROGRAM, opportunity_id=PROGRAM, point=worker, item=item, quantity=Decimal(sachets), unit="sachet", occurred_on=date(2026, 9, day), visit_id=vid, estimated=False)
    rejected = Movement.objects.get(visit_id="b")
    posting.post_visit_reversal(rejected, reason="visit rejected")
    StockCount.objects.create(program_id=PROGRAM, supply_point=worker, item=item, commodity=commodity, kind="self_reported", counted_on=date(2026, 9, 6), quantity=Decimal("136"), quantity_unit="sachet", source="commcare_form")
    StockCount.objects.create(program_id=PROGRAM, supply_point=worker, item=item, commodity=commodity, kind="reported_receipt", counted_on=date(2026, 9, 1), quantity=Decimal("150"), quantity_unit="sachet", source="commcare_form")
    return {"item": item, "worker": worker}


def test_the_timeline_steps_up_for_issues_and_down_for_dispensing(world):
    line = worker_timeline(PROGRAM, world["worker"], world["item"])

    assert line["unit"] == "sachet"
    assert [(d["on"], Decimal(d["issued"]), Decimal(d["dispensed"]), Decimal(d["balance"])) for d in line["days"]] == [
        ("2026-09-01", Decimal("150"), Decimal("0"), Decimal("150")),
        ("2026-09-03", Decimal("0"), Decimal("14"), Decimal("136")),
        # The rejected visit and its reversal on the same day cancel.
        ("2026-09-05", Decimal("0"), Decimal("0"), Decimal("136")),
    ]
    # Counts only: a reported receipt is not a statement of what is on hand.
    assert line["counts"] == [{"on": "2026-09-06", "quantity": "136.0000", "kind": "self_reported"}]


def test_as_of_stops_the_timeline_on_that_day(world):
    line = worker_timeline(PROGRAM, world["worker"], world["item"], on_date=date(2026, 9, 2))
    assert [d["on"] for d in line["days"]] == ["2026-09-01"]
    assert line["counts"] == []


def test_the_chart_draws_each_step_and_each_count(world):
    svg = timeline_svg(worker_timeline(PROGRAM, world["worker"], world["item"]))

    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert svg.count('data-kind="issued"') == 1
    assert svg.count('data-kind="dispensed"') == 1
    assert svg.count('data-kind="count"') == 1
    assert "counted 136 sachet" in svg


def test_nothing_to_draw_is_no_chart():
    assert timeline_svg({"unit": "sachet", "days": [], "counts": []}) == ""


def test_the_unit_is_escaped():
    svg = timeline_svg({"unit": "<b>", "days": [{"on": "2026-09-01", "before": "0", "issued": "1", "dispensed": "0", "other": "0", "balance": "1"}], "counts": []})
    assert "<b>" not in svg and "&lt;b&gt;" in svg
```

- [ ] **Step 2: Run them to verify they fail**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_timeline.py -q"`
Expected: FAIL with `ModuleNotFoundError: ...stock.services.timeline`.

- [ ] **Step 3: Write the timeline**

Create `connect_labs/supply_chain/stock/services/timeline.py`:

```python
"""A worker's stock day by day, and the chart drawn from it (design 2026-09-28 §6.3).

Issued steps up, dispensed steps down, reported counts are points, and the
ledger line runs between them. Everything is in the item's single unit.
The chart is inline SVG built from numbers and dates only -- the one piece
of text from data, the unit, is escaped -- because no chart library is loaded
on supply pages and an SVG renders inside the as-of rewind with the rest of
the page.
"""

from datetime import date
from decimal import Decimal

from django.db.models import Q
from django.utils.html import escape

from connect_labs.supply_chain import records
from connect_labs.supply_chain.models import Movement, StockCount
from connect_labs.supply_chain.stock.services import belief, ledger
from connect_labs.supply_chain.values import Quantity, quantity_digits

ZERO = Decimal("0")


def worker_timeline(program_id, point, item, *, on_date=None) -> dict:
    unit = belief.unit_of(item)
    moves = (
        Movement.objects.for_program(program_id)
        .as_of(on_date)
        .filter(item=item)
        .filter(Q(to_supply_point=point) | Q(from_supply_point=point))
        .order_by("occurred_on", "id")
        .values("occurred_on", "kind", "quantity", "quantity_unit", "to_supply_point_id")
    )
    days: dict = {}
    unconverted = []
    for m in moves:
        converted = ledger.convert(m["quantity"], m["quantity_unit"], unit, item)
        if not isinstance(converted, Quantity):
            unconverted.append({"on": m["occurred_on"].isoformat(), "reasons": list(converted.reasons)})
            continue
        amount = converted.amount
        day = days.setdefault(m["occurred_on"], {"issued": ZERO, "dispensed": ZERO, "other": ZERO})
        inbound = m["to_supply_point_id"] == point.pk
        if m["kind"] == "consumption":
            # A reversal comes back in: it takes that day's dispensing back.
            day["dispensed"] += -amount if inbound else amount
        elif inbound and m["kind"] in belief.ISSUE_KINDS:
            day["issued"] += amount
        else:
            day["other"] += amount if inbound else -amount

    balance = ZERO
    out = []
    for on in sorted(days):
        day = days[on]
        before = balance
        balance = balance + day["issued"] - day["dispensed"] + day["other"]
        out.append(
            {
                "on": on.isoformat(),
                "before": str(before),
                "issued": str(day["issued"]),
                "dispensed": str(day["dispensed"]),
                "other": str(day["other"]),
                "balance": str(balance),
            }
        )

    counts = StockCount.objects.filter(program_id=program_id, supply_point=point, item=item, kind__in=records.ON_HAND_COUNT_KINDS)
    if on_date is not None:
        counts = counts.filter(counted_on__lte=on_date)
    count_rows = []
    for count in counts.order_by("counted_on", "id"):
        converted = ledger.convert(count.quantity, count.quantity_unit, unit, item)
        if isinstance(converted, Quantity):
            count_rows.append({"on": count.counted_on.isoformat(), "quantity": str(converted.amount), "kind": count.kind})
    return {"unit": unit, "days": out, "counts": count_rows, "unconverted": unconverted}


def _segment(x1, y1, x2, y2, kind, colour, title=""):
    inner = f"<title>{title}</title>" if title else ""
    width = 1.5 if kind == "ledger" else 3
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{colour}" '
        f'stroke-width="{width}" data-kind="{kind}">{inner}</line>'
    )


_STEPS = (("issued", "#16a34a", 1), ("dispensed", "#ea580c", -1), ("other", "#6b7280", 1))


def timeline_svg(line: dict, *, width: int = 720, height: int = 220) -> str:
    days = [{**d, "on": date.fromisoformat(d["on"])} for d in line.get("days") or []]
    counts = [{**c, "on": date.fromisoformat(c["on"])} for c in line.get("counts") or []]
    if not days and not counts:
        return ""
    unit = escape(line.get("unit") or "")
    dates = [d["on"] for d in days] + [c["on"] for c in counts]
    first, last = min(dates), max(dates)
    span = max((last - first).days, 1)
    levels = [Decimal(d[k]) for d in days for k in ("before", "balance")] + [Decimal(c["quantity"]) for c in counts]
    top = max([*levels, Decimal(1)])
    bottom = min([*levels, ZERO])
    pad = 28

    def x(on):
        return pad + (on - first).days / span * (width - 2 * pad)

    def y(value):
        return pad + float(top - Decimal(value)) / float(top - bottom) * (height - 2 * pad)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Stock held, in {unit}s, by day" class="w-full h-auto" data-testid="worker-timeline">',
        _segment(pad, y(ZERO), width - pad, y(ZERO), "axis", "#e5e7eb"),
    ]
    previous = None
    for d in days:
        at = x(d["on"])
        level = Decimal(d["before"])
        if previous is not None:
            parts.append(_segment(previous, y(level), at, y(level), "ledger", "#9ca3af"))
        for key, colour, sign in _STEPS:
            amount = Decimal(d[key])
            if amount == 0:
                continue
            after = level + sign * amount
            title = f"{d['on'].isoformat()}: {key} {quantity_digits(amount)} {unit}"
            parts.append(_segment(at, y(level), at, y(after), key, colour, title))
            level = after
        previous = at
    if days:
        end = Decimal(days[-1]["balance"])
        parts.append(_segment(previous, y(end), x(last), y(end), "ledger", "#9ca3af"))
    for c in counts:
        parts.append(
            f'<circle cx="{x(c["on"]):.1f}" cy="{y(c["quantity"]):.1f}" r="4" fill="#4f46e5" data-kind="count">'
            f"<title>{c['on'].isoformat()}: counted {quantity_digits(Decimal(c['quantity']))} {unit}</title></circle>"
        )
    parts.append(f'<text x="{pad}" y="{height - 8}" font-size="11" fill="#4b5563">{first.isoformat()}</text>')
    parts.append(f'<text x="{width - pad}" y="{height - 8}" font-size="11" fill="#4b5563" text-anchor="end">{last.isoformat()}</text>')
    parts.append(f'<text x="{pad}" y="{pad - 10}" font-size="11" fill="#4b5563">{quantity_digits(top)} {unit}</text>')
    parts.append("</svg>")
    return "".join(parts)
```

Run: `make test ARGS="connect_labs/supply_chain/tests/test_timeline.py -q"`. Expected: PASS.

- [ ] **Step 4: The `worker_stock_get` operation**

Append to `connect_labs/supply_chain/stock/visit_operations.py`:

```python
@register_operation(
    name="worker_stock_get",
    summary=(
        "One worker's stock of one item: the figures worker_stock gives, their day-by-day timeline, the "
        "visits behind each step (status, what the reader made of it, and the answers at the rule's "
        "paths), the stock that arrived, and any receipt the worker reported that no distribution explains."
    ),
    input_schema=obj(
        {"supply_point_id": ID, "item_id": ID, "as_of": _DATE, "window_days": _WINDOW},
        required=("supply_point_id", "item_id"),
    ),
)
def worker_stock_get(access, supply_point_id, item_id, as_of=None, window_days=90):
    from connect_labs.supply_chain.models import Movement, WorkerVisit
    from connect_labs.supply_chain.stock.services import belief, timeline
    from connect_labs.supply_chain.stock.services.visit_reader import APPROVED_STATUSES, outcome_key

    program_id = access._require_program()
    point = access._require_supply_point(supply_point_id)
    item = access._resolve_item(item_id)
    on_date = _on(as_of)
    visits = WorkerVisit.objects.filter(program_id=program_id, supply_point=point)
    arrivals = Movement.objects.for_program(program_id).as_of(on_date).filter(to_supply_point=point, item=item, kind__in=belief.ISSUE_KINDS)
    if on_date is not None:
        visits = visits.filter(visit_date__lte=on_date)
    key = outcome_key(item.pk)
    return {
        "worker": belief.wire(belief.point_belief(program_id, point, item, on_date=on_date, window_days=window_days)),
        "timeline": timeline.worker_timeline(program_id, point, item, on_date=on_date),
        "visits": [
            {
                "visit_id": v.visit_id,
                "xform_id": v.xform_id,
                "visit_date": v.visit_date.isoformat(),
                "status": v.status,
                "approved": v.status in APPROVED_STATUSES,
                "form_name": v.form_name,
                "outcome": v.outcomes.get(key, ""),
                "answers": v.answers,
            }
            for v in visits.order_by("-visit_date", "-id")[:200]
        ],
        "arrivals": [
            {"occurred_on": m.occurred_on.isoformat(), "kind": m.kind, "quantity": str(m.quantity), "unit": m.quantity_unit, "reference": m.reference, "distribution_id": m.distribution_id}
            for m in arrivals.order_by("-occurred_on", "-id")[:100]
        ],
        "unmatched_receipts": belief.unmatched_receipts(program_id, point, item, on_date=on_date),
    }
```

- [ ] **Step 5: Write the failing screen tests**

Create `connect_labs/supply_chain/tests/test_worker_screens.py`:

```python
"""Network, Workers and Worker, driven through the browser, live and as of a day.

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented.
"""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, DispensingRule, Item, Movement, StockCount, SupplyPoint, WorkerVisit
from connect_labs.supply_chain.stock.services import posting
from connect_labs.supply_chain.stock.services.dispensing import validate_lines
from connect_labs.supply_chain.stock.services.visit_reader import outcome_key

pytestmark = pytest.mark.django_db

PROGRAM = 20883
TODAY = timezone.localdate()


class _ProgramContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.labs_context = {"program_id": PROGRAM} if request.user.is_authenticated else {}
        return self.get_response(request)


@pytest.fixture
def da():
    SyntheticOpportunity.objects.create(opportunity_id=PROGRAM, labs_only=True, enabled=True, label="worker screens", gdrive_folder_id="f")
    registry.invalidate_cache()
    return SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def client_in_program(client, django_user_model, monkeypatch, settings, da):
    from connect_labs.supply_chain import api_views, form_views, views  # noqa: F401
    from connect_labs.supply_chain.network import views as network_views  # noqa: F401
    from connect_labs.supply_chain.stock import visit_views  # noqa: F401

    settings.MIDDLEWARE = [*settings.MIDDLEWARE, f"{__name__}._ProgramContextMiddleware"]
    for module in ("api_views", "form_views", "views", "network.views", "stock.visit_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", lambda request: da)
    client.force_login(django_user_model.objects.create_user(username="sophie", password="x"))
    return client


@pytest.fixture
def world(da):
    commodity = Commodity.objects.create(scope_key=f"prog:{PROGRAM}", slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton", base_per_pack=150)
    item = Item.objects.create(scope_key=f"prog:{PROGRAM}", sku="rutf", name="RUTF 150", commodity=commodity, base_unit="sachet", pack_unit="carton", base_per_pack=150)
    store = SupplyPoint.objects.create(program_id=PROGRAM, slug="partner", name="Partner store", kind="regional_store", source="we_recorded")
    # The store receives exactly what it hands on, so its own balance is 0 and its subtree is its workers'.
    Movement.objects.create(program_id=PROGRAM, kind="receipt", occurred_on=TODAY - timedelta(days=70), to_supply_point=store, item=item, commodity=commodity, quantity=Decimal("300"), quantity_unit="sachet", source="we_recorded")
    DispensingRule.objects.create(program_id=PROGRAM, opportunity_id=PROGRAM, item=item, resupply_point=store, active_from=TODAY - timedelta(days=90), lines=validate_lines([{"kind": "stated", "paths": ["form.x"], "unit": "sachet"}], item))
    workers = {}
    for name, dispensed, status, estimated in (("worker-baobab", 40, "approved", False), ("worker-acacia", 30, "pending", True)):
        worker = SupplyPoint.objects.create(program_id=PROGRAM, opportunity_id=PROGRAM, slug=f"user-{name}", name=name, kind="user_held", connect_username=name, parent=store, source="connect_visit", min_months_of_stock=Decimal("1"), max_months_of_stock=Decimal("2"))
        Movement.objects.create(program_id=PROGRAM, kind="distribution", occurred_on=TODAY - timedelta(days=60), from_supply_point=store, to_supply_point=worker, item=item, commodity=commodity, quantity=Decimal("150"), quantity_unit="sachet", source="we_recorded")
        posting.post_visit_consumption(program_id=PROGRAM, opportunity_id=PROGRAM, point=worker, item=item, quantity=Decimal(dispensed), unit="sachet", occurred_on=TODAY - timedelta(days=45), visit_id=f"v-{name}", estimated=estimated)
        WorkerVisit.objects.create(program_id=PROGRAM, opportunity_id=PROGRAM, visit_id=f"v-{name}", xform_id=f"xf-{name}", supply_point=worker, visit_date=TODAY - timedelta(days=45), status=status, outcomes={outcome_key(item.pk): "dispensed"}, answers={"form.x": str(dispensed)})
        StockCount.objects.create(program_id=PROGRAM, supply_point=worker, item=item, commodity=commodity, kind="self_reported", counted_on=TODAY - timedelta(days=2), quantity=Decimal(150 - dispensed), quantity_unit="sachet", source="commcare_form")
        workers[name] = worker
    return {"item": item, "store": store, **workers}


def names_in_order(body):
    return re.findall(r'data-testid="worker-name">([^<]+)<', body)


def test_workers_are_listed_by_name_with_no_worst_first(client_in_program, world):
    body = client_in_program.get(reverse("supply_chain:workers")).content.decode()
    assert names_in_order(body) == ["worker-acacia", "worker-baobab"]


def test_workers_sort_by_a_column_when_asked(client_in_program, world):
    body = client_in_program.get(reverse("supply_chain:workers"), {"sort": "on_hand", "dir": "desc"}).content.decode()
    assert names_in_order(body) == ["worker-acacia", "worker-baobab"]  # 120 before 110
    body = client_in_program.get(reverse("supply_chain:workers"), {"sort": "on_hand", "dir": "asc"}).content.decode()
    assert names_in_order(body) == ["worker-baobab", "worker-acacia"]


def test_the_unapproved_and_estimated_parts_are_said_on_the_figure(client_in_program, world):
    body = client_in_program.get(reverse("supply_chain:workers")).content.decode()
    assert "30 sachets unapproved" in body
    assert "30 sachets estimated" in body


def test_a_worker_page_shows_the_timeline_and_the_visits_behind_it(client_in_program, world):
    worker = world["worker-acacia"]
    body = client_in_program.get(reverse("supply_chain:worker_detail", args=[worker.pk])).content.decode()
    assert 'data-testid="worker-timeline"' in body
    assert "xf-worker-acacia" in body
    assert "form.x" in body
    assert "Not yet approved" in body


def test_the_network_shows_a_stores_workers_and_their_totals(client_in_program, world):
    body = client_in_program.get(reverse("supply_chain:network")).content.decode()
    assert 'data-testid="network-tree"' in body
    assert "2 workers" in body
    assert "230 sachets" in body  # 120 + 110 below the partner store


def test_as_of_hides_the_write_controls(client_in_program, world):
    worker = world["worker-acacia"]
    network_live = client_in_program.get(reverse("supply_chain:network")).content.decode()
    network_past = client_in_program.get(reverse("supply_chain:network"), {"as_of": TODAY.isoformat()}).content.decode()
    detail_live = client_in_program.get(reverse("supply_chain:worker_detail", args=[worker.pk])).content.decode()
    detail_past = client_in_program.get(reverse("supply_chain:worker_detail", args=[worker.pk]), {"as_of": TODAY.isoformat()}).content.decode()

    new_point = reverse("supply_chain:supply_point_create")
    record_count = reverse("supply_chain:stock_count_record")
    assert new_point in network_live and new_point not in network_past
    assert record_count in detail_live and record_count not in detail_past
    assert "read-only" in detail_past


def test_a_worker_in_another_programme_is_a_404(client_in_program, world):
    other = SupplyPoint.objects.create(program_id=PROGRAM + 1, slug="x", name="x", kind="user_held", connect_username="x", source="we_recorded")
    assert client_in_program.get(reverse("supply_chain:worker_detail", args=[other.pk])).status_code == 404
```

- [ ] **Step 6: Run them to verify they fail**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_worker_screens.py -q"`
Expected: FAIL with `NoReverseMatch: 'workers'`.

- [ ] **Step 7: The views**

Append to `connect_labs/supply_chain/stock/visit_views.py` (add the imports `from decimal import Decimal`, `from django.http import Http404` and `from django.utils.safestring import mark_safe` at the top):

```python
def rule_items(op) -> list[dict]:
    """The items some dispensing rule gives out, in rule order, once each."""
    seen, items = set(), []
    for rule in op("dispensing_rule_list", include_inactive=True):
        if rule["item_id"] not in seen:
            seen.add(rule["item_id"])
            items.append({"id": rule["item_id"], "name": rule["item_name"]})
    return items


def chosen_item(request, items):
    wanted = request.GET.get("item_id")
    for item in items:
        if wanted and str(item["id"]) == wanted:
            return item
    return items[0] if items else None


def as_of_payload(request) -> dict:
    day = getattr(request, "supply_as_of", None)
    return {"as_of": day.isoformat()} if day else {}


def _amount(cell):
    try:
        return Decimal(cell["amount"]) if cell and "amount" in cell else None
    except (ArithmeticError, TypeError, ValueError):
        return None


def _share(part, whole):
    part, whole = _amount(part), _amount(whole)
    if part is None or not whole:
        return None
    return int((part / whole * 100).to_integral_value())


def _plain_number(value):
    try:
        return Decimal(value) if isinstance(value, str) else None
    except ArithmeticError:
        return None


SORTS = {
    "name": lambda row: row["name"].lower(),
    "on_hand": lambda row: _amount(row["on_hand"]),
    "days_to_stockout": lambda row: _plain_number(row["days_to_stockout"]),
    "variance": lambda row: _amount(row["variance"]),
    "days_since_checked": lambda row: row["days_since_checked"],
    "unapproved": lambda row: row["unapproved_share"],
    "estimated": lambda row: row["estimated_share"],
}


def sort_rows(rows, key, descending):
    """Sorted by the column asked for; a row with no figure goes last either way."""
    value = SORTS[key]
    present = [row for row in rows if value(row) is not None]
    missing = [row for row in rows if value(row) is None]
    return sorted(present, key=value, reverse=descending) + missing


class WorkersView(OperationBase):
    """One row per worker. Sorted by name until the reader picks a column (§22: facts, no ranking)."""

    template_name = "supply_chain/workers.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        items = rule_items(self.op)
        item = chosen_item(self.request, items)
        context.update(items=items, item=item)
        if item is None:
            return context
        data = self.op("worker_stock", item_id=item["id"], **as_of_payload(self.request))
        rows = []
        for row in data["workers"]:
            row["unapproved_share"] = _share(row["unapproved"], row["dispensed"])
            row["estimated_share"] = _share(row["estimated"], row["dispensed"])
            rows.append(row)
        sort = self.request.GET.get("sort") if self.request.GET.get("sort") in SORTS else "name"
        descending = self.request.GET.get("dir") == "desc"
        context.update(data=data, rows=sort_rows(rows, sort, descending), sort=sort, descending=descending, sorts=list(SORTS))
        return context


class WorkerDetailView(OperationBase):
    template_name = "supply_chain/worker_detail.html"

    def get_context_data(self, **kwargs):
        from connect_labs.supply_chain.stock.services.timeline import timeline_svg

        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        items = rule_items(self.op)
        item = chosen_item(self.request, items)
        context.update(items=items, item=item)
        if item is None:
            return context
        try:
            data = self.op("worker_stock_get", supply_point_id=self.kwargs["supply_point_id"], item_id=item["id"], **as_of_payload(self.request))
        except ValueError as error:
            raise Http404(str(error)) from error
        context.update(data=data, worker=data["worker"], chart=mark_safe(timeline_svg(data["timeline"])))
        return context
```

`mark_safe` is safe here because `timeline_svg` builds its markup from numbers and ISO dates, and escapes the one string that comes from data (the unit). `test_the_unit_is_escaped` pins that.

- [ ] **Step 8: The network tree**

In `connect_labs/supply_chain/network/views.py` `NetworkView.get_context_data`, before `return context`:

```python
        from connect_labs.supply_chain.stock.visit_views import as_of_payload, chosen_item, rule_items

        items = rule_items(self.op)
        item = chosen_item(self.request, items)
        context.update(tree_items=items, tree_item=item)
        if item is not None:
            context["tree"] = self.op("network_tree", item_id=item["id"], **as_of_payload(self.request))
```

- [ ] **Step 9: Templates**

Create `connect_labs/templates/supply_chain/_network_node.html`:

```html
{% load supply_chain_extras %}
{% comment %}
One point in the network tree, and -- for a store -- its subtree. Recursive:
a store includes this template for each child. Workers sit behind a
<details> so a store with two hundred of them stays one row until opened.
Every figure resting on unapproved visits or estimates says so beside it.
{% endcomment %}
<li class="py-2" data-testid="network-node">
  <div class="flex flex-wrap items-baseline gap-x-4 gap-y-1">
    {% if node.kind == "user_held" %}
      <a class="text-gray-900 hover:underline" href="{% url 'supply_chain:worker_detail' node.supply_point_id %}?item_id={{ tree.item_id }}">{{ node.name }}</a>
    {% else %}
      <span class="font-medium text-gray-900">{{ node.name }}</span>
    {% endif %}
    <span class="text-sm text-gray-700">on hand {{ node.on_hand|figure_text }}</span>
    <span class="text-sm text-gray-700">{% if node.months_of_stock and node.months_of_stock.unconfirmed %}cover unknown{% elif node.months_of_stock %}{{ node.months_of_stock }} months{% endif %}</span>
    <span class="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-700">{{ node.status|humanise }}</span>
    <span class="text-xs text-gray-600">{% if node.reported_on %}checked {{ node.reported_on|day }}{% else %}never counted{% endif %}</span>
  </div>
  {% if node.subtree %}
    <div class="text-sm text-gray-700 mt-1">
      With everything below it: {{ node.subtree.on_hand|figure_text }} on hand{% if node.subtree.months_of_stock and not node.subtree.months_of_stock.unconfirmed %}, {{ node.subtree.months_of_stock }} months of cover{% endif %}
      · {{ node.workers }} worker{{ node.workers|pluralize }}{% if node.workers_below_min %}, {{ node.workers_below_min }} below minimum{% endif %}
      {% if node.subtree.unapproved.amount and node.subtree.unapproved.amount != "0" %}· dispensing includes {{ node.subtree.unapproved|figure_text }} unapproved{% endif %}
      {% if node.subtree.estimated.amount and node.subtree.estimated.amount != "0" %}· {{ node.subtree.estimated|figure_text }} estimated{% endif %}
    </div>
    <details class="mt-1 ml-4" {% if not node.workers %}open{% endif %}>
      <summary class="text-sm text-brand-indigo cursor-pointer">Show what is below {{ node.name }}</summary>
      <ul class="ml-2 border-l border-gray-200 pl-4">
        {% for child in node.children %}{% include 'supply_chain/_network_node.html' with node=child tree=tree %}{% endfor %}
      </ul>
    </details>
  {% endif %}
</li>
```

`humanise` is the existing filter already used in `network.html`.

In `connect_labs/templates/supply_chain/network.html`, wrap the "New supply point" link in `{% if not supply_as_of %}…{% endif %}` and the per-row `Edit` link likewise. Directly after the header `</div>` and before `{% if not has_program_context %}`, insert:

```html
{% if tree %}
  <section class="mb-8" data-testid="network-tree">
    <div class="flex flex-wrap items-end justify-between gap-2 mb-2">
      <h2 class="text-sm font-semibold text-gray-900">Stock of {{ tree.item_name }} through the network, in {{ tree.unit|unit_plural }}</h2>
      {% if tree_items|length > 1 %}
        <form method="get" class="text-sm">
          <select name="item_id" onchange="this.form.submit()" class="rounded-md border border-gray-300 px-2 py-1">
            {% for choice in tree_items %}<option value="{{ choice.id }}" {% if choice.id == tree.item_id %}selected{% endif %}>{{ choice.name }}</option>{% endfor %}
          </select>
        </form>
      {% endif %}
    </div>
    <ul class="bg-white border border-brand-border-light rounded-lg px-4 divide-y divide-gray-100">
      {% for node in tree.roots %}{% include 'supply_chain/_network_node.html' with node=node tree=tree %}{% endfor %}
    </ul>
  </section>
{% endif %}
```

Create `connect_labs/templates/supply_chain/workers.html`:

```html
{% extends 'supply_chain/base.html' %}
{% load supply_chain_extras %}
{% comment %}
One row per field worker. Sorted by name until a column is chosen: the page
states facts and ranks nobody (September design §22). Every figure that rests
on unapproved visits or on protocol estimates says so in its own cell.
{% endcomment %}
{% block supply_content %}
<div class="mb-5">
  <h1 class="text-2xl font-semibold text-gray-900 leading-tight">Workers</h1>
  <p class="text-sm text-gray-500 mt-1">What we believe each field worker holds, from their visits and what was issued to them.</p>
</div>
{% if not has_program_context %}
  <p class="text-sm text-gray-600">Choose a programme above.</p>
{% elif not item %}
  <p class="text-sm text-gray-600">No dispensing rule yet, so no visit has moved any stock. <a class="text-brand-indigo hover:underline" href="{% url 'supply_chain:dispensing_rules' %}">Dispensing rules</a></p>
{% else %}
  {% if items|length > 1 %}
    <form method="get" class="mb-3 text-sm"><input type="hidden" name="sort" value="{{ sort }}">
      <select name="item_id" onchange="this.form.submit()" class="rounded-md border border-gray-300 px-2 py-1">
        {% for choice in items %}<option value="{{ choice.id }}" {% if choice.id == item.id %}selected{% endif %}>{{ choice.name }}</option>{% endfor %}
      </select>
    </form>
  {% endif %}
  <div class="bg-white border border-brand-border-light rounded-lg overflow-hidden">
    <div class="overflow-x-auto" tabindex="0" role="region" aria-label="Table, scrolls sideways">
      <table class="min-w-[56rem] w-full text-sm">
        <thead class="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
          <tr>
            {% for key, label in "name:Worker,on_hand:On hand,days_to_stockout:Days to stock-out,variance:Count minus ledger,days_since_checked:Days since checked,unapproved:Unapproved,estimated:Estimated"|column_pairs %}
              <th class="text-left px-4 py-2"><a class="hover:underline" href="?item_id={{ item.id }}&sort={{ key }}&dir={% if sort == key and not descending %}desc{% else %}asc{% endif %}">{{ label }}{% if sort == key %} {% if descending %}↓{% else %}↑{% endif %}{% endif %}</a></th>
            {% endfor %}
            <th class="text-left px-4 py-2">No answer</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          {% for row in rows %}
            <tr>
              <td class="px-4 py-2"><a data-testid="worker-name" class="text-gray-900 hover:underline" href="{% url 'supply_chain:worker_detail' row.supply_point_id %}?item_id={{ item.id }}">{{ row.name }}</a></td>
              <td class="px-4 py-2 tabular-nums">{{ row.on_hand|figure_text }}
                {% if row.unapproved.amount and row.unapproved.amount != "0" or row.estimated.amount and row.estimated.amount != "0" %}
                  <div class="text-xs text-amber-900">rests on {% if row.unapproved.amount != "0" %}{{ row.unapproved|figure_text }} unapproved{% endif %}{% if row.unapproved.amount != "0" and row.estimated.amount != "0" %} and {% endif %}{% if row.estimated.amount != "0" %}{{ row.estimated|figure_text }} estimated{% endif %}</div>
                {% endif %}
              </td>
              <td class="px-4 py-2 tabular-nums">{% if row.days_to_stockout.unconfirmed %}<span title="{{ row.days_to_stockout.unconfirmed|join:'; ' }}">unknown</span>{% else %}{{ row.days_to_stockout|default:"—" }}{% endif %}</td>
              <td class="px-4 py-2 tabular-nums">{{ row.variance|figure_text }}</td>
              <td class="px-4 py-2 tabular-nums">{% if row.days_since_checked is None %}never{% else %}{{ row.days_since_checked }}{% endif %}</td>
              <td class="px-4 py-2 tabular-nums">{% if row.unapproved_share is None %}—{% else %}{{ row.unapproved_share }}%{% endif %}</td>
              <td class="px-4 py-2 tabular-nums">{% if row.estimated_share is None %}—{% else %}{{ row.estimated_share }}%{% endif %}</td>
              <td class="px-4 py-2 tabular-nums">{{ row.no_answer_visits }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
{% endif %}
{% endblock %}
```

This template uses a new `column_pairs` filter. Add it to `connect_labs/supply_chain/templatetags/supply_chain_extras.py`:

```python
@register.filter
def column_pairs(spec):
    """"key:Label,key2:Label 2" -> [(key, label), ...], for a sortable header row."""
    return [tuple(part.split(":", 1)) for part in spec.split(",")]
```

Create `connect_labs/templates/supply_chain/worker_detail.html`:

```html
{% extends 'supply_chain/base.html' %}
{% load supply_chain_extras %}
{% comment %}
One worker's stock: the timeline (issues step up, dispensing steps down,
counts are points, the ledger line between), then the visits and arrivals
behind each step. Each visit shows its status, what the reader made of it,
and the answers at the rule's own paths -- the form answer the figure rests on.
{% endcomment %}
{% block supply_content %}
{% if not has_program_context %}
  <p class="text-sm text-gray-600">Choose a programme above.</p>
{% elif not item %}
  <p class="text-sm text-gray-600">No dispensing rule yet.</p>
{% else %}
  <div class="flex flex-wrap items-end justify-between gap-4 mb-4">
    <div>
      <p class="text-sm"><a class="text-brand-indigo hover:underline" href="{% url 'supply_chain:workers' %}?item_id={{ item.id }}">Workers</a></p>
      <h1 class="text-2xl font-semibold text-gray-900 leading-tight">{{ worker.name }}</h1>
      <p class="text-sm text-gray-500 mt-1">{{ item.name }}, in {{ worker.unit|unit_plural }}</p>
    </div>
    {% if not supply_as_of %}
      <a href="{% url 'supply_chain:stock_count_record' %}" class="px-4 py-2 text-sm bg-brand-indigo hover:bg-brand-deep-purple text-white rounded-md">Record a count</a>
    {% endif %}
  </div>
  <dl class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6 text-sm">
    <div class="bg-white border rounded-lg p-3"><dt class="text-gray-500">Ledger on hand</dt><dd class="text-lg text-gray-900">{{ worker.on_hand|figure_text }}</dd></div>
    <div class="bg-white border rounded-lg p-3"><dt class="text-gray-500">Dispensed</dt><dd class="text-lg text-gray-900">{{ worker.dispensed|figure_text }}</dd>
      {% if worker.unapproved.amount != "0" %}<dd class="text-xs text-amber-900">{{ worker.unapproved|figure_text }} unapproved</dd>{% endif %}
      {% if worker.estimated.amount != "0" %}<dd class="text-xs text-amber-900">{{ worker.estimated|figure_text }} estimated</dd>{% endif %}
      {% if worker.no_answer_visits %}<dd class="text-xs text-gray-600">{{ worker.no_answer_visits }} visit{{ worker.no_answer_visits|pluralize }} did not say</dd>{% endif %}
    </div>
    <div class="bg-white border rounded-lg p-3"><dt class="text-gray-500">Last count</dt><dd class="text-lg text-gray-900">{{ worker.reported|figure_text }}</dd><dd class="text-xs text-gray-600">{% if worker.reported_on %}{{ worker.reported_on|day }} · count minus ledger {{ worker.variance|figure_text }}{% else %}never counted{% endif %}</dd></div>
    <div class="bg-white border rounded-lg p-3"><dt class="text-gray-500">Days to stock-out</dt><dd class="text-lg text-gray-900">{% if worker.days_to_stockout.unconfirmed %}unknown{% else %}{{ worker.days_to_stockout|default:"—" }}{% endif %}</dd>{% if worker.days_to_stockout.unconfirmed %}<dd class="text-xs text-gray-600">{{ worker.days_to_stockout.unconfirmed|join:"; " }}</dd>{% endif %}</div>
  </dl>
  {% if chart %}<div class="bg-white border rounded-lg p-3 mb-6">{{ chart }}</div>{% else %}<p class="text-sm text-gray-600 mb-6">Nothing has reached or left this worker yet.</p>{% endif %}
  {% if data.unmatched_receipts %}
    <div class="rounded-lg border border-amber-300 bg-amber-50 p-3 mb-6 text-sm text-amber-900">
      {% for receipt in data.unmatched_receipts %}<p>Worker reports {{ receipt.quantity|qty:receipt.unit }} received on {{ receipt.reported_on|day }}; no distribution recorded.</p>{% endfor %}
    </div>
  {% endif %}
  <h2 class="text-sm font-semibold text-gray-900 mb-2">Visits</h2>
  <table class="w-full text-sm bg-white border rounded-lg mb-6">
    <thead class="bg-gray-50 text-xs uppercase text-gray-500"><tr><th class="text-left px-3 py-2">Day</th><th class="text-left px-3 py-2">Status</th><th class="text-left px-3 py-2">Read as</th><th class="text-left px-3 py-2">Form answer</th></tr></thead>
    <tbody class="divide-y divide-gray-100">
      {% for v in data.visits %}
        <tr>
          <td class="px-3 py-2">{{ v.visit_date|day }}</td>
          <td class="px-3 py-2">{% if v.approved %}Approved{% else %}Not yet approved ({{ v.status|humanise }}){% endif %}</td>
          <td class="px-3 py-2">{{ v.outcome|humanise|default:"—" }}</td>
          <td class="px-3 py-2"><details><summary class="cursor-pointer text-brand-indigo">{{ v.form_name|default:"form" }} <code class="text-xs">{{ v.xform_id }}</code></summary>
            <dl class="mt-1">{% for path, answer in v.answers.items %}<div><code class="text-xs">{{ path }}</code> = {{ answer }}</div>{% empty %}<div class="text-gray-600">No answer at the rule's paths.</div>{% endfor %}</dl></details></td>
        </tr>
      {% empty %}<tr><td colspan="4" class="px-3 py-2 text-gray-600">No visits read yet.</td></tr>{% endfor %}
    </tbody>
  </table>
  <h2 class="text-sm font-semibold text-gray-900 mb-2">Stock that arrived</h2>
  <ul class="text-sm bg-white border rounded-lg divide-y divide-gray-100">
    {% for m in data.arrivals %}<li class="px-3 py-2">{{ m.occurred_on|day }} · {{ m.quantity|qty:m.unit }} · {{ m.kind|humanise }}{% if m.reference %} · {{ m.reference }}{% endif %}</li>{% empty %}<li class="px-3 py-2 text-gray-600">Nothing issued to this worker.</li>{% endfor %}
  </ul>
{% endif %}
{% endblock %}
```

The "Record a count" link opens the existing count form unprefilled: `StockCountRecordView` reads no query parameters, and prefilling it is not part of this spec.

- [ ] **Step 10: URLs and nav**

In `connect_labs/supply_chain/urls.py`, after the dispensing routes:

```python
    path("workers/", visit_views.WorkersView.as_view(), name="workers"),
    path("workers/<int:supply_point_id>/", visit_views.WorkerDetailView.as_view(), name="worker_detail"),
```

In `connect_labs/supply_chain/navigation.py`, add `("supply_chain:workers", "Workers"),` to `SUPPLY_TABS` directly after `("supply_chain:stock", "Stock"),`, and `"supply_chain:worker_detail": "supply_chain:workers",` to `TAB_FOR_VIEW`.

- [ ] **Step 11: Size worker markers by on-hand on the map**

Create `connect_labs/static/supply_chain/marker_size.js`:

```js
/*
 * marker_size.js -- how big a place is drawn in the portfolio map's Stock mode.
 *
 * Area tracks on-hand (hence the square root), against the biggest holding of
 * the same unit among places of the same KIND: a worker's 40 sachets are
 * never drawn against a warehouse's 4,000 cartons, which would shrink every
 * worker to a dot. Colour stays the band (the page's COVER table), so size
 * is how much and colour is how long it lasts.
 */
(function (root) {
  'use strict';
  var STORE = { min: 5, span: 15 };
  var WORKER = { min: 2.5, span: 6.5 };

  function markerRadius(kind, amount, biggest) {
    var scale = kind === 'user_held' ? WORKER : STORE;
    if (!amount || !biggest || amount <= 0 || biggest <= 0) return scale.min;
    return scale.min + scale.span * Math.sqrt(Math.min(amount / biggest, 1));
  }

  function biggestKey(kind, unit) {
    return (kind === 'user_held' ? 'worker|' : 'place|') + (unit || '');
  }

  root.SupplyMarkerSize = { markerRadius: markerRadius, biggestKey: biggestKey };
})(typeof window !== 'undefined' ? window : globalThis);
```

Create `connect_labs/static/supply_chain/marker_size.test.js`:

```js
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
new Function(fs.readFileSync(path.join(here, 'marker_size.js'), 'utf8'))();
const { markerRadius, biggestKey } = globalThis.SupplyMarkerSize;

describe('markerRadius', () => {
  it('draws a worker with nothing at the smallest size, not zero', () => {
    expect(markerRadius('user_held', 0, 150)).toBe(2.5);
  });
  it('grows a worker by area with what they hold', () => {
    expect(markerRadius('user_held', 150, 150)).toBe(9);
    expect(markerRadius('user_held', 37.5, 150)).toBeCloseTo(2.5 + 6.5 * 0.5);
  });
  it('keeps stores on their own, larger scale', () => {
    expect(markerRadius('central_store', 400, 400)).toBe(20);
  });
  it('compares workers only with workers, per unit', () => {
    expect(biggestKey('user_held', 'sachet')).not.toBe(biggestKey('central_store', 'sachet'));
    expect(biggestKey('user_held', 'sachet')).not.toBe(biggestKey('user_held', 'carton'));
  });
});
```

In `connect_labs/static/supply_chain/portfolio_map.js`, in the stock-mode block (~2279–2305), replace the `biggest` computation and the `r:` line:

```js
    var SIZE = window.SupplyMarkerSize;
    var biggest = {};
    if (stockMode) {
      vis.forEach(function (pt) {
        var c = coverOf(pt);
        if (!c || !c.amount) return;
        var key = SIZE.biggestKey(pt.kind, (c.on_hand || {}).unit);
        biggest[key] = Math.max(biggest[key] || 0, c.amount);
      });
    }
```

and inside `feature` in stock mode:

```js
        var c = coverOf(pt);
        var unit = c && c.on_hand ? c.on_hand.unit || '' : '';
        return {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [pt._x, pt._y] },
          properties: {
            key: pt._key,
            name: pt.name,
            color: c ? (COVER[c.status] || COVER.unknown).color : '#1f2937',
            r: SIZE.markerRadius(pt.kind, c && c.amount, biggest[SIZE.biggestKey(pt.kind, unit)]),
            approx: whereIs(pt).coarse,
            sel: state.place === pt._key,
          },
        };
```

(The old `share` variable is gone.) In `connect_labs/templates/supply_chain/portfolio_map.html`, add
`<script src="{% static 'supply_chain/marker_size.js' %}"></script>`
on the line before the `portfolio_map.js` script tag.

Run: `npx vitest run connect_labs/static/supply_chain/marker_size.test.js`
Expected: 4 passed.

- [ ] **Step 12: Run everything in this task**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_timeline.py connect_labs/supply_chain/tests/test_worker_screens.py connect_labs/supply_chain/tests/test_network_screens.py connect_labs/supply_chain/tests/test_navigation_tabs.py connect_labs/supply_chain/tests/test_history_as_of.py connect_labs/supply_chain/tests/test_portfolio_map.py connect_labs/supply_chain/tests/test_no_raw_codes.py connect_labs/supply_chain/tests/test_mcp_parity.py -q"`
Expected: all PASS. If `test_no_raw_codes` flags a raw code (`no_answer`, `user_held`) in a new template, route it through `humanise` as the existing templates do.

Then look at the pages. Run `make manage CMD="runserver"` against a database seeded by Task 9, or use `gstack browse` after deploy (CLAUDE.md, "Browser Verification"). Open `/supply/network/`, `/supply/workers/` and one worker. Confirm that the chart draws and that the as-of control hides "Record a count".

- [ ] **Step 13: Commit**

```bash
git add connect_labs/supply_chain/ connect_labs/templates/supply_chain/ connect_labs/static/supply_chain/
make commit  # message: "feat(supply): network tree, Workers and Worker screens, worker markers sized by stock"
```

---
### Task 9: The synthetic world: a labs-only RUTF opportunity on the real app's paths

**Files:**
- Create: `connect_labs/supply_chain/demo/__init__.py` (empty), `connect_labs/supply_chain/demo/stock_from_visits.py`, `connect_labs/supply_chain/demo/README.md`
- Create: `connect_labs/supply_chain/management/commands/supply_seed_stock_from_visits.py`
- Test: `connect_labs/supply_chain/tests/test_stock_from_visits_seed.py`

**Interfaces:**
- Consumes: every operation above; `upload_and_register(drive=, opportunity_id=, opportunity_name=, fixtures=)` (`labs/synthetic/generator/io/uploader.py`); `seed_overrides` (`history/context.py`); `SyntheticOpportunity.next_labs_only_opp_id()`; `worker_slug` (T4); `belief.worker_beliefs`, `belief.unmatched_receipts` (T7, tests only).
- Produces:
  - `stock_from_visits.PATHS: dict[str, str]`, `WORKERS: tuple[str, ...]`, `RUNS_OUT`, `OVER_COUNTS`, `REJECTED_LATER`, `NEVER_ANSWERS`, `PRODUCTS: tuple[Product, ...]`
  - `stock_from_visits.build_world(start: date, *, weeks=8, seed=7) -> World` (pure)
  - `stock_from_visits.fixtures(world, opportunity_id) -> dict` (the five export endpoints)
  - `stock_from_visits.rule_data(items: dict[str, dict], resupply_point_id: int, start: date) -> list[dict]`
  - `stock_from_visits.seed(*, drive, reset=False, today=None) -> dict`
  - `manage.py supply_seed_stock_from_visits [--reset]`

The story (spec §7): a central store and a partner store, and 20 workers resupplied from the partner. Every worker has RUTF (stated) plus Vitamin A, amoxicillin, AL and mRDT (protocol, banded by age). ORS and zinc have no rule. Eight weeks of visits are read week by week, each read recorded on that week's Sunday, so an as-of page can walk back through them. Most workers' app balances reconcile with the ledger. `worker-kapok` runs out. `worker-marula` reports a receipt that no store recorded, so her count sits about 100 above the ledger. `worker-neem` has one week-2 visit rejected in week 4, and the reversal shows from week 4. `worker-sapele`'s visits never answer the RUTF question (`no_answer`).

Form paths follow spec §9 exactly where §9 gives a full path. Where §9 elides one (`…vita_group`, `…fever.mrdt_result`), and for the child's age, which §9 never names, this seeder uses the concrete paths in `PATHS` below, and the README says they are **stand-ins to confirm against the released app** before the rule is copied to opportunity 2230.

- [ ] **Step 1: Write the failing tests**

Create `connect_labs/supply_chain/tests/test_stock_from_visits_seed.py`:

```python
"""The synthetic stock-from-visits world, seeded end to end through the real operations.

THIS REPOSITORY IS PUBLIC. Every name here is invented; the form paths are
copied from an app definition, and no submission was read.
"""

import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.fixture_store import ENDPOINT_FILES
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.supply_chain.demo.stock_from_visits import (
    NEVER_ANSWERS,
    OVER_COUNTS,
    PATHS,
    REJECTED_LATER,
    RUNS_OUT,
    WORKERS,
    build_world,
    seed,
)
from connect_labs.supply_chain.history.models import Revision
from connect_labs.supply_chain.models import DispensingRule, Item, Movement, SupplyPoint, WorkerVisit
from connect_labs.supply_chain.stock.services import belief
from connect_labs.supply_chain.stock.services.visit_reader import outcome_key


def leaf_paths(node, prefix="form"):
    for key, value in node.items():
        if isinstance(value, dict):
            yield from leaf_paths(value, f"{prefix}.{key}")
        else:
            yield f"{prefix}.{key}"


def test_the_world_uses_the_apps_paths_and_invented_names():
    world = build_world(date(2026, 8, 3))
    paths = {p for v in world.visits for p in leaf_paths(v["form_json"]["form"]) if not p.endswith("@name")}

    for key in ("rutf_visit", "rutf_screening", "appetite_screening", "balance", "received", "received_on", "remaining"):
        assert PATHS[key] in paths, key
    assert not any("ors" in p or "zinc" in p for p in paths)
    assert len(WORKERS) == 20 and all(w.startswith("worker-") for w in WORKERS)
    assert {v["username"] for v in world.visits} == set(WORKERS)


def test_one_visit_is_rejected_after_it_was_counted():
    world = build_world(date(2026, 8, 3))
    rejected = [v for v in world.visits if v["status"] == "rejected"]
    assert [v["username"] for v in rejected] == [REJECTED_LATER]
    assert rejected[0]["status_modified_date"][:10] > rejected[0]["visit_date"]


class FakeDrive:
    def __init__(self):
        self.files = {}
        self.folders = 0

    def create_folder(self, name, parent_id):
        self.folders += 1
        return f"folder-{self.folders}"

    def upload_file(self, folder_id, filename, content):
        self.files[(folder_id, filename)] = json.loads(content)
        return f"file-{len(self.files)}"


class FakeStore:
    """Serves what FakeDrive holds for the opportunity's registered folder."""

    def __init__(self, drive):
        self.drive = drive

    def load_endpoint(self, opp_id, endpoint_key):
        folder = SyntheticOpportunity.objects.get(opportunity_id=opp_id).gdrive_folder_id
        return self.drive.files.get((folder, ENDPOINT_FILES[endpoint_key]), [])

    def reload(self, opp_id):
        return None


@pytest.fixture
def seeded(db, settings):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    drive = FakeDrive()
    registry.invalidate_cache()
    with patch("connect_labs.labs.integrations.connect.factory._get_fixture_store", return_value=FakeStore(drive)):
        summary = seed(drive=drive)
        yield {"drive": drive, "summary": summary, "opp": summary["opportunity_id"]}
    registry.invalidate_cache()


def rutf(opp):
    return Item.objects.select_related("commodity").get(scope_key=f"prog:{opp}", sku="syn-rutf-150")


def by_name(opp):
    return {b.point.connect_username: b for b in belief.worker_beliefs(opp, rutf(opp))}


def test_it_is_a_registered_labs_only_opportunity(seeded):
    row = SyntheticOpportunity.objects.get(opportunity_id=seeded["opp"])
    assert row.labs_only and row.enabled and row.gdrive_folder_id.startswith("folder-")
    assert seeded["summary"]["seeded"] is True and len(seeded["summary"]["weeks"]) == 8


def test_rules_exist_for_rutf_and_four_protocol_items_and_not_ors_or_zinc(seeded):
    skus = set(DispensingRule.objects.filter(program_id=seeded["opp"]).values_list("item__sku", flat=True))
    assert skus == {"syn-rutf-150", "syn-vita-500", "syn-amox-100", "syn-al-180", "syn-mrdt-25"}
    assert not Item.objects.filter(scope_key=f"prog:{seeded['opp']}", sku__icontains="ors").exists()


def test_the_reader_posted_visits_for_every_item(seeded):
    posted = Movement.objects.filter(program_id=seeded["opp"], kind="consumption", source="connect_visit")
    assert posted.filter(item__sku="syn-rutf-150").count() > 300
    assert posted.filter(estimated=True).exists()
    assert set(posted.values_list("item__sku", flat=True)) == {"syn-rutf-150", "syn-vita-500", "syn-amox-100", "syn-al-180", "syn-mrdt-25"}


def test_one_worker_never_answers_the_rutf_question(seeded):
    key = outcome_key(rutf(seeded["opp"]).pk)
    unanswered = WorkerVisit.objects.filter(program_id=seeded["opp"], connect_username=NEVER_ANSWERS, **{f"outcomes__{key}": "no_answer"})
    assert unanswered.count() >= 20
    assert by_name(seeded["opp"])[NEVER_ANSWERS].no_answer_visits >= 20


def test_one_visit_is_reversed(seeded):
    reversals = Movement.objects.filter(program_id=seeded["opp"], reverses__isnull=False)
    assert reversals.count() == 1
    assert reversals.get().to_supply_point.connect_username == REJECTED_LATER


def test_one_worker_runs_out(seeded):
    assert by_name(seeded["opp"])[RUNS_OUT].status == "stockout"


def test_one_worker_counts_well_above_the_ledger_and_reports_a_receipt_nobody_recorded(seeded):
    marula = by_name(seeded["opp"])[OVER_COUNTS]
    assert marula.variance.amount >= 90
    assert belief.unmatched_receipts(seeded["opp"], marula.point, rutf(seeded["opp"]))


def test_most_workers_reconcile(seeded):
    reconciled = [name for name, b in by_name(seeded["opp"]).items() if b.variance is not None and b.variance.amount == 0]
    assert len(reconciled) >= 17


def test_history_is_dated_week_by_week(seeded):
    three_days_ago = timezone.now() - timedelta(days=3)
    assert Revision.objects.filter(program_id=seeded["opp"], recorded_at__lt=three_days_ago).exists()


def test_seeding_again_changes_nothing_unless_reset(seeded):
    opp = seeded["opp"]
    before = Movement.objects.filter(program_id=opp).count()
    with patch("connect_labs.labs.integrations.connect.factory._get_fixture_store", return_value=FakeStore(seeded["drive"])):
        again = seed(drive=seeded["drive"])
        assert again["seeded"] is False
        assert Movement.objects.filter(program_id=opp).count() == before
        reset = seed(drive=seeded["drive"], reset=True)
    assert reset["opportunity_id"] == opp
    assert Movement.objects.filter(program_id=opp).count() == before
    assert SupplyPoint.objects.filter(program_id=opp, kind="user_held").count() == 20
```

- [ ] **Step 2: Run them to verify they fail**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_stock_from_visits_seed.py -q"`
Expected: FAIL with `ModuleNotFoundError: connect_labs.supply_chain.demo`.

- [ ] **Step 3: Write the seeder**

Create `connect_labs/supply_chain/demo/__init__.py` (empty) and `connect_labs/supply_chain/demo/stock_from_visits.py`:

```python
"""A synthetic RUTF opportunity whose visits use the real deliver app's form paths.

Design 2026-09-28 §7 and §9. Labs-only (opportunity id >= 10,000, its own
programme), invented names only -- THIS REPOSITORY IS PUBLIC.

Everything is written through the same operations a person or an agent uses,
dated with `seed_overrides` so the history reads week by week: an as-of page
on week 3 shows week 3. Visits are served as synthetic fixtures and read by
the real reader through `fetch_raw_visits`, so a rule written here transfers
to the real opportunity unchanged -- once the stand-in paths (see README) are
confirmed against the released app.
"""

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

LABEL = "Stock from visits: synthetic RUTF (invented)"
ORG_NAME = "Harmattan Nutrition Partners (synthetic)"
PROGRAM_NAME = "Stock from visits demo (synthetic)"
ALLOWED_DOMAINS = ["@dimagi.com"]
WEEKS = 8
RATION = Decimal("14")
APPETITE = Decimal("0.3333")
ISSUE = Decimal("180")
RUNS_OUT_ISSUE = Decimal("90")
UNRECORDED_RECEIPT = Decimal("100")

WORKERS = (
    "worker-acacia", "worker-baobab", "worker-cassia", "worker-doum", "worker-ebony",
    "worker-ficus", "worker-gmelina", "worker-hibiscus", "worker-iroko", "worker-jacaranda",
    "worker-kapok", "worker-locust", "worker-marula", "worker-neem", "worker-obeche",
    "worker-palm", "worker-quassia", "worker-raffia", "worker-sapele", "worker-tamarind",
)
RUNS_OUT = "worker-kapok"
OVER_COUNTS = "worker-marula"
REJECTED_LATER = "worker-neem"
NEVER_ANSWERS = "worker-sapele"

FORM_SCREENING, FORM_VISIT, FORM_STOCK = "Screening", "Visit Form", "Stock Management"

# form_json paths. Full paths are copied from spec §9. The ones marked
# STAND-IN are where §9 elides the path ("…vita_group") or never names it
# (the child's age): confirm them against the released app before copying a
# rule to a real opportunity.
PATHS = {
    "rutf_visit": "form.rutf_dispensing.rutf_sachets_dispensed",
    "rutf_screening": "form.visit_1.rutf_dispensing.rutf_sachets_dispensed",
    "appetite_screening": "form.screening_outcome.rutf_stock_deduction",
    "appetite_var": "form.var.appetite_test_stock_deduction",
    "balance": "form.var.new_stock_balance",
    "received": "form.current_stock.sachets_received",
    "received_on": "form.current_stock.date_received",
    "remaining": "form.stock_balance.sachets_remaining",
    "amox_screening": "form.visit_1.presumptive_amoxicillin_given",
    "vita_screening": "form.visit_1.vita_group.va_delivered",  # STAND-IN
    "vita_visit": "form.vita_group.va_delivered",  # STAND-IN
    "amox_visit": "form.fast_breathing_treatment.amoxicillin_given",  # STAND-IN
    "mrdt_screening": "form.visit_1.fever.mrdt_result",  # STAND-IN
    "mrdt_visit": "form.fever.mrdt_result",  # STAND-IN
    "age_screening": "form.visit_1.child_age_months",  # STAND-IN
    "age_visit": "form.child_age_months",  # STAND-IN
}


@dataclass(frozen=True)
class Product:
    slug: str
    name: str
    base: str
    pack: str
    per_pack: int
    sku: str
    opening: int  # into the central store, in base units
    first_issue: int  # to each worker in week 0, in base units


PRODUCTS = (
    Product("rutf", "Ready-to-use therapeutic food (synthetic)", "sachet", "carton", 150, "syn-rutf-150", 16000, 180),
    Product("vitamin-a", "Vitamin A 100,000 IU (synthetic)", "capsule", "bottle", 500, "syn-vita-500", 1500, 30),
    Product("amoxicillin-dt", "Amoxicillin 250 mg dispersible (synthetic)", "tablet", "pack", 100, "syn-amox-100", 8000, 150),
    Product("artemether-lumefantrine", "Artemether-lumefantrine 20/120 (synthetic)", "tablet", "pack", 180, "syn-al-180", 4000, 72),
    Product("mrdt", "Malaria rapid diagnostic test (synthetic)", "test", "kit", 25, "syn-mrdt-25", 1000, 15),
)
BY_SKU = {p.sku: p for p in PRODUCTS}


@dataclass
class World:
    start: date
    visits: list = field(default_factory=list)
    issues: list = field(default_factory=list)  # [{"on": date, "sku": str, "lines": {username: Decimal}}]
    uuids: dict = field(default_factory=dict)


def _nest(answers: dict) -> dict:
    body: dict = {}
    for path, value in answers.items():
        node = body
        parts = path.split(".")[1:]
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return body


def _visit(visit_id, username, user_id, on, status, form_name, answers, modified=None):
    xform = str(uuid.UUID(int=visit_id))
    return {
        "id": visit_id,
        "xform_id": xform,
        "username": username,
        "user_id": user_id,
        "deliver_unit": "",
        "deliver_unit_id": None,
        "entity_id": f"case-{visit_id}",
        "entity_name": "",
        "visit_date": on.isoformat(),
        "status": status,
        "reason": None,
        "location": "",
        "flagged": False,
        "flag_reason": {},
        "form_json": {"id": xform, "form": {"@name": form_name, **_nest(answers)}},
        "completed_work": "",
        "status_modified_date": f"{(modified or on).isoformat()}T12:00:00",
        "review_status": "",
        "review_created_on": None,
        "justification": None,
        "date_created": f"{on.isoformat()}T09:00:00",
        "completed_work_id": None,
        "images": [],
    }


def _decimal_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.0001")).normalize(), "f")


def build_world(start: date, *, weeks: int = WEEKS, seed: int = 7) -> World:
    """Every visit and every issue, deterministically. Pure: no database."""
    rng = random.Random(seed)
    world = World(start=start, uuids={w: str(uuid.UUID(int=rng.getrandbits(128))) for w in WORKERS})
    for product in PRODUCTS:
        lines = {w: Decimal(product.first_issue) for w in WORKERS}
        if product.sku == "syn-rutf-150":
            lines[RUNS_OUT] = RUNS_OUT_ISSUE
        world.issues.append({"on": start, "sku": product.sku, "lines": lines})
    world.issues.append(
        {"on": start + timedelta(weeks=4), "sku": "syn-rutf-150", "lines": {w: ISSUE for w in WORKERS if w != RUNS_OUT}}
    )
    rutf_issues = {(i["on"], w): q for i in world.issues if i["sku"] == "syn-rutf-150" for w, q in i["lines"].items()}
    rejected_on_day = start + timedelta(weeks=4, days=2)
    next_id = 700_000_001
    for username in WORKERS:
        app = Decimal(0)  # the app's own running RUTF balance on this worker's phone
        user_id = world.uuids[username]
        for week in range(weeks):
            monday = start + timedelta(weeks=week)
            status_now = "approved" if week < weeks - 2 else "pending"
            events = []
            received = rutf_issues.get((monday, username))
            if received:
                events.append((monday + timedelta(days=1), 0, "stock", received, monday))
            if username == OVER_COUNTS and week == 6:
                thursday = monday + timedelta(days=3)
                events.append((thursday, 0, "stock", UNRECORDED_RECEIPT, thursday))
            for n in range(3):
                events.append((monday + timedelta(days=1 + 2 * n), 1, "visit", n, None))
            for on, _order, kind, value, received_on in sorted(events, key=lambda e: (e[0], e[1])):
                visit_id, next_id = next_id, next_id + 1
                if kind == "stock":
                    app += value
                    answers = {
                        PATHS["received"]: _decimal_text(value),
                        PATHS["received_on"]: received_on.isoformat(),
                        PATHS["remaining"]: _decimal_text(app),
                    }
                    world.visits.append(_visit(visit_id, username, user_id, on, status_now, FORM_STOCK, answers))
                    continue
                screening = week == 0 and value == 0
                answers = {}
                if screening:
                    answers[PATHS["appetite_screening"]] = _decimal_text(APPETITE)
                    app -= APPETITE
                if username != NEVER_ANSWERS:
                    given = min(RATION, max(app, Decimal(0)))
                    answers[PATHS["rutf_screening"] if screening else PATHS["rutf_visit"]] = _decimal_text(given)
                    app -= given
                answers[PATHS["balance"]] = _decimal_text(app)
                answers[PATHS["age_screening"] if screening else PATHS["age_visit"]] = str(rng.randint(6, 59))
                # Every protocol question is ANSWERED, "not given" included: an
                # unanswered question is unknown, never zero (see Deviations, 17).
                answers[PATHS["vita_screening"] if screening else PATHS["vita_visit"]] = "child_fine" if rng.random() < 0.3 else "not_given"
                answers[PATHS["amox_screening"] if screening else PATHS["amox_visit"]] = "yes" if rng.random() < 0.15 else "no"
                answers[PATHS["mrdt_screening"] if screening else PATHS["mrdt_visit"]] = (
                    rng.choice(["positive", "negative"]) if rng.random() < 0.25 else "not_done"
                )
                status, modified = status_now, None
                if username == REJECTED_LATER and week == 2 and value == 0:
                    status, modified = "rejected", rejected_on_day
                world.visits.append(
                    _visit(visit_id, username, user_id, on, status, FORM_SCREENING if screening else FORM_VISIT, answers, modified)
                )
    return world


def fixtures(world: World, opportunity_id: int) -> dict:
    return {
        "opportunity": {"id": opportunity_id, "name": LABEL},
        "user_visits": [{**v, "opportunity_id": opportunity_id} for v in world.visits],
        "user_data": [{"username": w, "name": w} for w in WORKERS],
        "completed_works": [],
        "completed_module": [],
    }


def rule_data(items: dict, resupply_point_id: int, start: date) -> list[dict]:
    age = [PATHS["age_screening"], PATHS["age_visit"]]
    common = {"resupply_point_id": resupply_point_id, "active_from": start.isoformat(), "forms": [FORM_SCREENING, FORM_VISIT]}

    def banded(given_paths, given_values, bands, unit):
        return {
            "kind": "protocol",
            "given_paths": given_paths,
            "given_values": given_values,
            "age_paths": age,
            "by_age": [{"from_months": lo, "to_months": hi, "quantity": str(q)} for lo, hi, q in bands],
            "unit": unit,
        }

    mrdt = [PATHS["mrdt_screening"], PATHS["mrdt_visit"]]
    return [
        {
            **common,
            "item_id": items["syn-rutf-150"]["id"],
            "lines": [
                {"kind": "stated", "paths": [PATHS["rutf_visit"], PATHS["rutf_screening"]], "unit": "sachet"},
                {"kind": "stated", "paths": [PATHS["appetite_screening"], PATHS["appetite_var"]], "unit": "sachet"},
            ],
            "reports": {
                "balance_paths": [PATHS["balance"], PATHS["remaining"]],
                "receipt": {"quantity_paths": [PATHS["received"]], "date_paths": [PATHS["received_on"]]},
            },
        },
        {**common, "item_id": items["syn-vita-500"]["id"], "lines": [banded([PATHS["vita_screening"], PATHS["vita_visit"]], ["child_fine"], [(6, 11, 1), (12, 59, 2)], "capsule")]},
        {**common, "item_id": items["syn-amox-100"]["id"], "lines": [banded([PATHS["amox_screening"], PATHS["amox_visit"]], ["yes"], [(2, 11, 10), (12, 59, 20)], "tablet")]},
        {**common, "item_id": items["syn-al-180"]["id"], "lines": [banded(mrdt, ["positive"], [(6, 35, 6), (36, 59, 12)], "tablet")]},
        {**common, "item_id": items["syn-mrdt-25"]["id"], "lines": [{"kind": "protocol", "given_paths": mrdt, "given_values": ["positive", "negative"], "quantity": "1", "unit": "test"}]},
    ]


def _monday_on_or_before(day: date) -> date:
    return day - timedelta(days=day.weekday())


def seed(*, drive, reset: bool = False, today: date | None = None) -> dict:
    """Seed (or, with reset, rebuild) the synthetic opportunity. Server-side only."""
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.labs.synthetic import registry
    from connect_labs.labs.synthetic.generator.io.uploader import upload_and_register
    from connect_labs.labs.synthetic.models import SyntheticOpportunity
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.history.context import seed_overrides
    from connect_labs.supply_chain.models import DispensingRule
    from connect_labs.supply_chain.operations import call_operation
    from connect_labs.supply_chain.stock.services.visit_source import SYNTHETIC_TOKEN
    from connect_labs.supply_chain.stock.services.workers import worker_slug

    today = today or timezone.localdate()
    start = _monday_on_or_before(today - timedelta(weeks=WEEKS))
    world = build_world(start)

    opp = SyntheticOpportunity.objects.filter(labs_only=True, label=LABEL).first()
    if opp is None:
        opp = SyntheticOpportunity.objects.create(
            opportunity_id=SyntheticOpportunity.next_labs_only_opp_id(),
            labs_only=True,
            enabled=True,
            label=LABEL,
            org_name=ORG_NAME,
            program_name=PROGRAM_NAME,
            allowed_domains=ALLOWED_DOMAINS,
            gdrive_folder_id="",
        )
        registry.invalidate_cache()
    opp_id = opp.opportunity_id
    access = SupplyDataAccess(access_token=SYNTHETIC_TOKEN, program_id=opp_id, opportunity_id=opp_id, caller=SYSTEM)
    if DispensingRule.objects.filter(program_id=opp_id).exists():
        if not reset:
            return {"opportunity_id": opp_id, "seeded": False, "reason": "already seeded; pass --reset to rebuild it"}
        access.purge()

    upload_and_register(drive=drive, opportunity_id=opp_id, opportunity_name=LABEL, fixtures=fixtures(world, opp_id))
    registry.invalidate_cache()

    def op(day, hour, name, **payload):
        with seed_overrides(opp_id, channel="command", recorded_at=timezone.make_aware(datetime.combine(day, time(hour)))):
            return call_operation(name, access, payload)

    setup = start - timedelta(days=7)
    items = {}
    for p in PRODUCTS:
        op(setup, 9, "commodity_upsert", data={"slug": p.slug, "name": p.name, "base_unit": p.base, "pack_unit": p.pack, "base_per_pack": p.per_pack})
        items[p.sku] = op(setup, 9, "item_upsert", data={"sku": p.sku, "name": p.name, "commodity_slug": p.slug, "base_unit": p.base, "pack_unit": p.pack, "base_per_pack": p.per_pack})
    central = op(setup, 9, "supply_point_upsert", data={"slug": "central-store", "name": "Central store (synthetic)", "kind": "central_store", "source": "we_recorded", "min_months_of_stock": "2", "max_months_of_stock": "6"})
    partner = op(setup, 9, "supply_point_upsert", data={"slug": "partner-store", "name": "Partner store (synthetic)", "kind": "regional_store", "parent_supply_point_id": central["id"], "source": "partner_reported", "min_months_of_stock": "1", "max_months_of_stock": "3"})
    for p in PRODUCTS:
        base = {"commodity_slug": p.slug, "item_id": items[p.sku]["id"], "quantity_unit": p.base, "source": "we_recorded", "occurred_on": setup.isoformat()}
        op(setup, 10, "movement_record", data={**base, "kind": "receipt", "to_supply_point_id": central["id"], "quantity": str(p.opening)})
        op(setup, 11, "movement_record", data={**base, "kind": "transfer", "from_supply_point_id": central["id"], "to_supply_point_id": partner["id"], "quantity": str(p.opening // 2)})
    for data in rule_data(items, partner["id"], start):
        op(setup, 12, "dispensing_rule_upsert", data=data)
    # The roster is registered up front so week 0's distribution has somewhere
    # to go, under the slug the reader itself would give each worker on first
    # sight (test_visit_reader proves that path); the band is the programme's.
    for username in WORKERS:
        op(setup, 13, "supply_point_upsert", data={
            "slug": worker_slug(opp_id, username), "name": username, "kind": "user_held", "opportunity_id": opp_id,
            "connect_username": username, "connect_user_uuid": world.uuids[username],
            "parent_supply_point_id": partner["id"], "source": "connect_visit",
            "min_months_of_stock": "0.5", "max_months_of_stock": "1.5",
        })

    weeks = []
    for week in range(WEEKS):
        monday = start + timedelta(weeks=week)
        for issue in world.issues:
            if issue["on"] != monday:
                continue
            product = BY_SKU[issue["sku"]]
            op(monday, 8, "distribution_record", data={
                "supply_point_id": partner["id"], "opportunity_id": opp_id, "commodity_slug": product.slug,
                "distributed_on": monday.isoformat(), "source": "partner_reported",
                "lines": [
                    {"connect_username": u, "item_id": items[product.sku]["id"], "quantity": str(q), "quantity_unit": product.base}
                    for u, q in issue["lines"].items() if q
                ],
            })
        sunday = monday + timedelta(days=6)
        report = op(sunday, 20, "visit_consumption_ingest", opportunity_id=opp_id, until=sunday.isoformat())
        weeks.append({"week_of": monday.isoformat(), **{k: report[k] for k in ("posted", "reversed", "no_answer", "balances_recorded", "receipts_recorded")}})

    return {"opportunity_id": opp_id, "program_id": opp_id, "seeded": True, "start": start.isoformat(), "workers": len(WORKERS), "weeks": weeks}
```

`worker-kapok` ends at exactly zero because the last dispensing gives what is left (`min(RATION, app)`), which can be a fraction of a sachet. The app tracks thirds for the appetite test, so this is what its own arithmetic would do.

- [ ] **Step 4: The command**

Create `connect_labs/supply_chain/management/commands/supply_seed_stock_from_visits.py`:

```python
"""Seed the synthetic stock-from-visits RUTF opportunity. Invented data; labs-only.

    make manage CMD="supply_seed_stock_from_visits"           # once
    make manage CMD="supply_seed_stock_from_visits --reset"   # rebuild it

Runs server-side (the ECS worker, like every other seed): it uploads the visit
fixtures to Drive with the labs service account and dates history with
seed_overrides, which exists only in-process.
"""

import json

from django.core.management.base import BaseCommand

from connect_labs.supply_chain.demo.stock_from_visits import seed


class Command(BaseCommand):
    help = "Seed the synthetic stock-from-visits RUTF opportunity (labs-only, invented data)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Purge this synthetic programme and seed it again.")

    def handle(self, *args, **options):
        from connect_labs.labs.synthetic.gdrive import DriveClient

        summary = seed(drive=DriveClient(), reset=options["reset"])
        self.stdout.write(json.dumps(summary, indent=2, default=str))
```

- [ ] **Step 5: The README**

Create `connect_labs/supply_chain/demo/README.md`:

```markdown
# Stock from visits: the synthetic RUTF world

`manage.py supply_seed_stock_from_visits [--reset]` builds a labs-only
opportunity (its own programme, id >= 10,000) whose visits are shaped like
the real RUTF deliver app's submissions, and reads them into the supply
ledger week by week. Invented names only; this repository is public.

What it shows (design `docs/superpowers/specs/2026-09-28-supply-stock-from-visits-design.md` §7):
a partner store resupplying 20 workers; RUTF read from the stated
`rutf_sachets_dispensed` answer plus the appetite test's third of a sachet;
Vitamin A, amoxicillin, AL and mRDT from the protocol (estimated, banded by
age); ORS and zinc deliberately off until the programme settles 4 sachets
versus 2 co-packs. Most workers' app balances reconcile; `worker-kapok` runs
out; `worker-marula` reports a receipt no store recorded; one of
`worker-neem`'s visits is rejected two weeks after it was counted and is
reversed; `worker-sapele` never answers the RUTF question.

Where to look: `/supply/network/`, `/supply/workers/`, one worker's page,
and `?as_of=` any day in the eight weeks.

Stand-in paths. `PATHS` in `stock_from_visits.py` copies spec §9's full paths.
Where §9 elides a path (`…vita_group`, `…fever.mrdt_result`, the pneumonia
amoxicillin question) or never names one (the child's age in months), the
seeder uses a stand-in, marked `STAND-IN`. Before copying these rules to
opportunity 2230, read the released app with `get_opportunity_apps` and
replace each stand-in. Protocol doses are illustrative and must come from
the programme.

`--reset` purges this programme's supply data and history (allowed only for
a registered labs-only programme) and uploads a fresh fixture folder.
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `make test ARGS="connect_labs/supply_chain/tests/test_stock_from_visits_seed.py -q"`
Expected: all PASS. If `test_most_workers_reconcile` or `test_one_worker_runs_out` fails, print `by_name(opp)` and trace the worker through the reader report before changing any number. A mismatch here is exactly the finding the page exists to show, so find which side is wrong.

- [ ] **Step 7: Commit**

```bash
git add connect_labs/supply_chain/demo/ connect_labs/supply_chain/management/commands/supply_seed_stock_from_visits.py connect_labs/supply_chain/tests/test_stock_from_visits_seed.py
make commit  # message: "feat(supply): synthetic stock-from-visits RUTF world on the real app's paths"
```

---

### Task 10: Docs

**Files:**
- Modify: `CLAUDE.md` (the `supply_chain/` app-map row; MCP tool counts in "MCP Servers")
- Modify: `docs/superpowers/specs/2026-09-28-supply-stock-from-visits-design.md` (status line only)

- [ ] **Step 1: Count what the registry now holds**

Run:

```bash
make manage CMD="shell -c \"from connect_labs.supply_chain.operations import all_operations, agent_operations; import connect_labs.supply_chain.mcp_tools; print(len(all_operations()), len(agent_operations()), sorted(n for n,o in all_operations().items() if o.internal))\""
```

Expected: `93 89 ['catalogue_seed', 'stock_report_ingest', 'tracker_import', 'visit_consumption_ingest']`. That is 86 + 7 new, of which 6 are agent-facing. If `main` has moved, use the printed numbers, not these.

- [ ] **Step 2: Update CLAUDE.md**

In the `supply_chain/` row of the App Map, after "A field worker is a supply point, so distributing to one reuses the ledger.", add: "Consumption is derived from submitted visits: per-opportunity `DispensingRule`s map form answers to stock leaving the worker (`visit_consumption_ingest`, hourly for synthetic programmes), and `stock/services/belief.py` rolls what each worker holds up the network in grouped SQL; see `docs/superpowers/specs/2026-09-28-supply-stock-from-visits-design.md`." In the same row's key-files cell, add `stock/services/belief.py`.

In "MCP Servers → connect_labs", update the three counts from Step 1: "registers **211 tools**", "89 of them generated", "The registry holds 93: four are `internal=True` (`catalogue_seed`, `tracker_import`, `stock_report_ingest`, `visit_consumption_ingest`)". The catalogue total is 205 + 6; use Step 1's numbers if `main` moved.

- [ ] **Step 3: Mark the spec**

Change the spec's `Status:` line to: `Status: design agreed 2026-09-28; §9 mapped the same day; implementation plan docs/superpowers/plans/2026-09-28-supply-stock-from-visits.md.`

- [ ] **Step 4: Full suite, then commit**

Run: `make test ARGS="connect_labs/supply_chain -q"` and `npx vitest run connect_labs/static/supply_chain/`
Expected: all PASS.

```bash
git add CLAUDE.md docs/superpowers/specs/2026-09-28-supply-stock-from-visits-design.md
make commit  # message: "docs(supply): stock from visits in the app map and MCP counts"
```

---

## Deviations from the spec, and why

Each of these is a place where the spec, read against the real code, could not be built exactly as written. The plan's choice is stated; each is a candidate for the product owner to overrule.

1. **§3.4 "a `StockCount`-style report of receipt".** A `StockCount` means "what is on hand", and `soh.last_count`, `network._latest_counts` and the alert scope all read it that way. A receipt stored as one would become a worker's "last count". The plan adds the kind `reported_receipt`, adds `records.ON_HAND_COUNT_KINDS` (every on-hand reader filters to it), and takes the kind off `stock_count_record`. Task 6.
2. **§5 "of which unapproved" needs a visit's current status.** An append-only `Movement` cannot carry it, because status changes after posting. The plan adds `WorkerVisit`, a mutable, revisioned row per visit, which also gives the Worker page its visit list and form answers. Task 5.
3. **§9 paths with "…" are not buildable, and §9 names no age path.** VitA, amoxicillin (pneumonia), AL and mRDT use stand-in paths in the synthetic world, marked `STAND-IN`. The released app for opportunity 2230 was not readable from this environment (`get_opportunity_apps` returned no access). Task 9, README.
4. **The appetite-test double count.** §9 lists `form.screening_outcome.rutf_stock_deduction` as the ⅓-sachet line. If the app's `rutf_stock_deduction` is actually its *total* deduction (ration plus appetite), summing it with `rutf_sachets_dispensed` counts the ration twice. This needs confirming in the app before the rule reaches a real opportunity.
5. **§7 "count well above the ledger (under-reported dispensing)" is self-contradictory.** Under-reported dispensing leaves the ledger *above* the physical count. The seeder produces "count above ledger" the coherent way, through a receipt the worker reports that no store recorded, which also exercises §3.4's gap finding.
6. **Reversal date (the spec is silent).** A reversal carries the original visit's date, so AMC over any window nets exactly (`standing_consumption` drops the pair). What a page showed on a past day comes from the revision rewind, not from `occurred_on`.
7. **A visit first seen already rejected** is never posted (the spec only covers reversing a consumption that exists). **A visit reinstated after reversal** cannot be re-posted under the spec's own one-consumption-plus-one-reversal rule. It is reported (`reinstated_after_reversal`) for a human to resolve.
8. **"Flagged duplicate" is undefined.** The plan treats `status == "duplicate"` or any `flag_reason.flags` code containing "duplicate" as reversing. The programme should name the real flag code. A visit a reviewer has **approved** (or `over_limit`) is never reversed by the flag: approving a flagged visit judges it genuine (final review, I1).
9. **Every Stock Management visit would be `no_answer` for every rule.** The rule gains `forms` (names from `form_json.form["@name"]`) so that a form that never asks the question is not counted as unanswered.
10. **`reverses` is a `OneToOneField`** (a nullable FK that is unique), so "at most one reversal" holds in the database, and the visit uniqueness constraints include `program_id`.
11. **`movement_record` could forge a visit or a reversal**, because `_columns` passes any model field. `record_movement` now strips `visit_id`, `reverses`, `reverses_id` and `estimated`.
12. **`stock_report_ingest` idempotency on `form_submission_id` alone** would skip a Stock Management form's balance once its receipt was recorded. It is now keyed on `(id, kind, item)`.
13. **The beat task has no Connect token.** Synthetic clients ignore it, and real programmes are refused, as §1 requires. Going live needs a decision on a headless export token. CLAUDE.md records why a stored-user-token beat job was removed elsewhere.
14. **Status changes arrive on cache expiry.** `fetch_raw_visits` serves the shared visit cache, so a rejection reaches the reader when the cache is refreshed. `--refresh` forces a read.
15. **Variance sign.** §5 says "ledger − reported", but the existing `stock_on_hand` (which §5 says to reuse) is reported − ledger. The plan keeps the existing sign, and the screens label it "count minus ledger".
16. **The spec's network page lists stores and workers in one tree.** The existing grouped directory (with its edit links) is kept below the new tree rather than replaced, so no existing screen test or workflow loses its page.
17. **Skip logic reads as "no answer".** A protocol question the app hides by skip logic (mRDT when there is no fever) is absent from `form_json`, and the reader, following §2, counts it as unknown rather than as nothing given. The synthetic app answers every protocol question (`not_given`, `no`, `not_done`). For the real app, each protocol rule needs either the question's always-present parent or a stated "not asked means none" decision from the programme. That decision was deliberately not made here.
18. **Not built: agreement with the app's "Alert Low Stock" survey** (§9, "the belief view should agree with it or say why not"). The spec gives no path or threshold for that survey. Once they are known, it is a further `reports` entry on the RUTF rule plus a check in `checks.py`.
19. **Only the Stock page moved onto `belief.py` (§5 "the stock page moves onto it").** `/supply/stock/` now rates every point in one grouped pass per item (then `network_stock(grouped=True)`), with a query-count test constant in worker count; a point holding several items gets no rate rather than one summed across items, and a point whose stock was recorded against the product with no trade item is still planned on its own. **Known follow-up:** the other callers still run `resupply.plan` per point, and a visit makes every worker a supply point, so their cost grows with the roster: the portfolio map (`portfolio/map_data.py:259`, `:301`, through the `network_stock` operation), the checks (`checks.py:950`, which also calls `soh.stock_on_hand` per counted point), the hourly alert and summary (`summary.py:163`), and the two other pages that call `network_stock` (`views.py:654`, `views.py:1054`). Nothing is slow at synthetic scale (20 workers); move them onto `belief.beliefs_for` before a real programme with hundreds of workers is switched on. Controller ruling, final review I2.
    **Follow-up done (2026-10-01).** Every caller now rates through grouped SQL: `network_stock` takes rates from `belief.beliefs_for` (one pass per item held) and, for a point with no one item, from `network._rates_across_items` (`resupply.plan(item=None)`'s rate, grouped), so the `network_stock` operation (map, portfolio, item and order pages), the chain summary and the checks no longer plan per point. The checks' count-day variance is `soh.counted_against_ledger` (grouped per item) and the never-reported check's "holds stock" is one query. Figures are unchanged: a point holding several items keeps its summed rate everywhere except the Stock page (`several_items="refuse"`), and `network_stock(per_point=True)` is kept only as the reference `tests/test_belief_callers.py` checks the grouped rows, variances and stock checks against. `supply_ingest_stock_reports` and `stock_report_ingest` now refuse a programme or opportunity that is not labs-only, as `visit_consumption_ingest` does.

---

## Resolved from the released app (2026-09-28, after this plan was drafted)

Read from opportunity 2230's released deliver app definition (no submissions). These OVERRIDE the plan wherever they conflict; implementers of Tasks 2, 3 and 9 must apply them.

1. **New line kind `value_map`** — `{"kind": "value_map", "path": ..., "map": {"<answer>": <quantity>}, "unit": ...}`. Reads one answer and looks its quantity up; an answer not in the map is `unit_refused`-style *unmapped* (reported, never zero). It is **estimated** (the app chose the dose, the worker did not count it). It replaces `by_age` for every real rule: the child's age (`childs_age_in_months`) is a case property and never appears in the form, but the app writes the dose it chose into the submission. Keep `by_age` in the validator only if already built; the seeder must not use it.
2. **RUTF, Screening form:** use `form.screening_outcome.rutf_stock_deduction` ALONE (stated, sachet). Its calc is `if(rutf_enrollment='yes', coalesce(visit_1.rutf_dispensing.rutf_sachets_dispensed, 14), if(appetite_result != '', 1, 0))` — it already is the total. Do NOT add `rutf_sachets_dispensed` to it.
   **RUTF, Visit Form:** `form.rutf_dispensing.rutf_sachets_dispensed` + `form.var.appetite_test_stock_deduction` (the latter is 1 only when no ration was dispensed), both stated. The appetite test deducts a whole sachet, not a third.
   Rules therefore need a `forms` filter (the plan already adds one) keyed on the form's xmlns or name.
3. **Amoxicillin DT (tablet):** `value_map` on `form.visit_1.fast_breathing_treatment.dosage_pneumonia`, `form.comorbid_conditions.comorbid_question_list.cough_assessment.dosage_pneumonia` (Screening) and `form.visit_2_or_greater.cough_assessment.dosage_pneumonia` (Visit Form): `"1 tablet every 12 hours (total 10 tablets)": 10`, `"2 tablets every 12 hours(total 20 tablets)": 20` (note the missing space — match exactly). Presumptive amoxicillin: protocol on `form.visit_1.presumptive_amoxicillin_given = yes`; its dose text is a label, not a field, so it is `protocol` with quantity 10 and marked estimated.
4. **Vitamin A (capsule):** given = `…vita_group.va_delivered` contains `child_fine`; which dose question was answered gives the strength: `…prepare_vita_dosage.va_eligible_dose_6mo_to_11mo` → 1 × 100,000 IU capsule; `va_eligible_dose_1yr_2year` or `va_eligible_dose_2yr_5yr` → 1 × 200,000 IU capsule. Two items.
5. **Albendazole:** given = `…dw_group.dw_delivered` contains `child_fine` (Screening `form.chc_commodities.dw_group`, Visit Form `form.visit_2_or_greater.dw_group`); `dw_eligible_dose_1yr_2year` → 200 mg, `dw_eligible_dose_2yr_6yr` → 400 mg.
6. **mRDT (test):** one per answered `form.visit_1.fever_treatment.mrdt_result` / `form.visit_2_or_greater.fever.mrdt_result` (any value, including `invalid`), protocol.
7. **Paracetamol:** given = `…paracetamol_given = yes`; quantity by `value_map` on `…paracetamol_dosage`. The units differ by band (syrup ml vs tablet fraction); make it TWO items (syrup ml, 500 mg tablet) or leave paracetamol off. Recommended: off in v1, listed in the rule catalogue as "not tracked: mixed units".
8. **AL (antimalarial):** no dose field in the form (the dose is only in a label). Protocol, given when `mrdt_result = positive`, quantity from the national AL band table is impossible without age → leave AL **off** in v1 and say so on the rules page. Do not guess.
9. **ORS / zinc:** stay off (the app contradicts itself: "4 ORS sachets" vs "2 copacks" per child).
10. **Skip logic ("not asked")**: a hidden question is absent from `form_json`. For protocol / value_map lines, *absent given-path = not given* (the app only shows the step when the condition applies); for **stated** lines, absent = `no_answer`. This resolves the plan's open skip-logic question.
11. **Worker's own stock (Stock Management form):** `form.current_stock.sachets_received`, `form.current_stock.date_received` → `reported_receipt`; `form.stock_balance.sachets_remaining` and every visit's `form.var.new_stock_balance` → `self_reported` balance.

Synthetic fixtures (Task 9) must use exactly these paths and these answer strings so rules transfer to the real opportunity unchanged.
