# Fund Allocations + E2E Tests Design

**Date:** 2026-03-15
**Branch:** jj/regranting

## Goal

Add fund allocation tracking (embedded in FundRecord) and comprehensive Playwright e2e tests using the `test-user` profile against the Baobob-Demo-Workspace org.

## Data Model

FundRecord.data gains an `allocations` array. Each allocation is a dict:

```json
{
  "program_id": 45,
  "program_name": "KMC Uganda",
  "amount": 200000,
  "type": "retroactive",
  "notes": "Existing Q1 commitment",
  "solicitation_id": null,
  "response_id": null,
  "org_id": "",
  "org_name": ""
}
```

Types: `retroactive` (pre-existing commitment), `award` (from solicitation award flow), `manual` (ad-hoc).

Computed properties on FundRecord:
- `allocations` — returns the list
- `committed_amount` — sum of all allocation amounts
- `remaining_amount` — total_budget minus committed_amount

## Award Auto-Allocation

When `SolicitationsDataAccess.award_response()` completes and the solicitation has a `fund_id`:
1. Fetch the fund via `FunderDashboardDataAccess`
2. Append allocation with `type="award"`, amount from `reward_budget`, solicitation/response IDs
3. Save the fund

This requires `award_response()` to accept an optional `fund_id` or look it up from the solicitation.

## UI Changes

### fund_detail.html
- Updated KPIs: Total Budget, Committed, Remaining (with color coding)
- Allocations table: Program, Amount, Type, Notes, Remove button
- Empty state when no allocations

### fund_form.html
- Dynamic allocations section using Alpine.js
- Add row: program (dropdown from user's programs), amount, type, notes
- Remove row button
- Hidden input serializes allocations as JSON on submit

## E2E Tests

### Infrastructure
- `conftest.py`: Add `--profile` CLI option (default `test-user`), pass `?profile=<name>` to `/labs/test-auth/`
- Tests use Baobob-Demo-Workspace org (slug: jjackson)
- Real data created against production API

### test_fund_flow.py
1. Portfolio page loads with KPIs
2. Create fund with name, description, budget
3. Fund detail shows correct data
4. Edit fund — add a retroactive allocation (program, amount, notes)
5. Fund detail shows updated Committed/Remaining KPIs
6. Portfolio reflects the new fund

### test_award_flow.py
1. Create a solicitation linked to the test fund
2. Submit a response to the solicitation
3. View responses list — response appears
4. View response detail — Award button visible
5. Award the response with org_id and reward_budget
6. Verify response shows "Awarded" badge
7. Navigate to fund detail — verify auto-allocation appeared and KPIs updated

## Files Changed

| File | Change |
|------|--------|
| `funder_dashboard/models.py` | `allocations`, `committed_amount`, `remaining_amount` properties |
| `funder_dashboard/data_access.py` | `add_allocation()`, `remove_allocation()` methods |
| `funder_dashboard/views.py` | Pass programs for dropdown, handle allocations in form POST |
| `funder_dashboard/templates/fund_detail.html` | Allocations table, updated KPIs |
| `funder_dashboard/templates/fund_form.html` | Dynamic allocations section |
| `solicitations/data_access.py` | Auto-create allocation in `award_response()` |
| `funder_dashboard/tests/e2e/conftest.py` | `--profile` option |
| `funder_dashboard/tests/e2e/test_fund_flow.py` | New |
| `funder_dashboard/tests/e2e/test_award_flow.py` | New |
| Unit tests for allocation logic | New/updated |

## Future: Fund Visualizations

Once allocations and e2e tests are solid, the fund detail page will gain rich visualizations built from real prod data (programs, opportunities, delivery metrics). That's a separate phase.
