# Fund Allocations + E2E Tests Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add fund allocation tracking (embedded array in FundRecord) with auto-allocation on award, and write comprehensive Playwright e2e tests using the `test-user` CLI profile against the Baobob-Demo-Workspace org.

**Architecture:** Allocations are stored as a JSON array inside `FundRecord.data["allocations"]`. Each entry has program_id, amount, type (retroactive/award/manual), and optional solicitation/response linkage. The award flow auto-appends an allocation when the solicitation has a `fund_id`. E2E tests use a dedicated test-user OAuth profile via `?profile=test-user` on `/labs/test-auth/`.

**Tech Stack:** Django 4.2, LabsRecordAPIClient (httpx), Tailwind CSS, Alpine.js for dynamic form rows, Playwright for e2e, pytest

---

### Task 1: Add allocation properties to FundRecord model

**Files:**
- Modify: `commcare_connect/funder_dashboard/models.py`
- Test: `commcare_connect/funder_dashboard/tests/test_models.py`

**Step 1: Write failing tests**

Add to `commcare_connect/funder_dashboard/tests/test_models.py`:

```python
class TestFundRecordAllocations:
    def _make_fund(self, allocations=None, total_budget=500000):
        data = {"total_budget": total_budget, "allocations": allocations or []}
        return FundRecord({"data": data})

    def test_allocations_empty_default(self):
        fund = FundRecord({"data": {}})
        assert fund.allocations == []

    def test_allocations_returns_list(self):
        allocs = [{"program_id": 1, "amount": 100000, "type": "retroactive"}]
        fund = self._make_fund(allocations=allocs)
        assert fund.allocations == allocs

    def test_committed_amount_sums_allocations(self):
        allocs = [
            {"program_id": 1, "amount": 200000, "type": "retroactive"},
            {"program_id": 2, "amount": 50000, "type": "award"},
        ]
        fund = self._make_fund(allocations=allocs)
        assert fund.committed_amount == 250000

    def test_committed_amount_zero_when_empty(self):
        fund = self._make_fund()
        assert fund.committed_amount == 0

    def test_remaining_amount(self):
        allocs = [{"program_id": 1, "amount": 200000, "type": "retroactive"}]
        fund = self._make_fund(allocations=allocs, total_budget=500000)
        assert fund.remaining_amount == 300000

    def test_remaining_amount_no_budget(self):
        fund = FundRecord({"data": {"allocations": [{"amount": 100}]}})
        assert fund.remaining_amount == 0
```

**Step 2: Run tests to verify they fail**

Run: `pytest commcare_connect/funder_dashboard/tests/test_models.py::TestFundRecordAllocations -v`
Expected: FAIL — `allocations`, `committed_amount`, `remaining_amount` not defined

**Step 3: Implement properties**

Add to `commcare_connect/funder_dashboard/models.py` inside `FundRecord`:

```python
    @property
    def allocations(self):
        return self.data.get("allocations", [])

    @property
    def committed_amount(self):
        return sum(a.get("amount", 0) for a in self.allocations)

    @property
    def remaining_amount(self):
        budget = self.total_budget or 0
        return max(0, budget - self.committed_amount)
```

**Step 4: Run tests to verify they pass**

Run: `pytest commcare_connect/funder_dashboard/tests/test_models.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add commcare_connect/funder_dashboard/models.py commcare_connect/funder_dashboard/tests/test_models.py
git commit -m "feat: add allocation properties to FundRecord model"
```

---

### Task 2: Add allocation methods to data access layer

**Files:**
- Modify: `commcare_connect/funder_dashboard/data_access.py`
- Test: `commcare_connect/funder_dashboard/tests/test_data_access.py`

**Step 1: Write failing tests**

Add to `commcare_connect/funder_dashboard/tests/test_data_access.py`:

```python
class TestAddAllocation:
    def test_adds_allocation_to_fund(self):
        fund_data = {"name": "Test Fund", "total_budget": 500000, "allocations": []}
        mock_fund = FundRecord({"id": 1, "data": fund_data})
        updated_data = dict(fund_data)
        updated_data["allocations"] = [
            {"program_id": 45, "program_name": "KMC", "amount": 200000, "type": "retroactive", "notes": ""}
        ]
        mock_updated = FundRecord({"id": 1, "data": updated_data})

        da = FunderDashboardDataAccess(org_id="1", access_token="tok")
        with patch.object(da, "get_fund_by_id", return_value=mock_fund):
            with patch.object(da, "update_fund", return_value=mock_updated) as mock_update:
                result = da.add_allocation(
                    fund_id=1,
                    allocation={"program_id": 45, "program_name": "KMC", "amount": 200000, "type": "retroactive"},
                )
                call_data = mock_update.call_args[0][1]
                assert len(call_data["allocations"]) == 1
                assert call_data["allocations"][0]["program_id"] == 45


class TestRemoveAllocation:
    def test_removes_allocation_by_index(self):
        allocs = [
            {"program_id": 1, "amount": 100000, "type": "retroactive"},
            {"program_id": 2, "amount": 50000, "type": "award"},
        ]
        fund_data = {"name": "Test Fund", "total_budget": 500000, "allocations": allocs}
        mock_fund = FundRecord({"id": 1, "data": fund_data})
        expected_data = dict(fund_data)
        expected_data["allocations"] = [allocs[1]]
        mock_updated = FundRecord({"id": 1, "data": expected_data})

        da = FunderDashboardDataAccess(org_id="1", access_token="tok")
        with patch.object(da, "get_fund_by_id", return_value=mock_fund):
            with patch.object(da, "update_fund", return_value=mock_updated) as mock_update:
                da.remove_allocation(fund_id=1, index=0)
                call_data = mock_update.call_args[0][1]
                assert len(call_data["allocations"]) == 1
                assert call_data["allocations"][0]["program_id"] == 2
```

**Step 2: Run tests to verify they fail**

Run: `pytest commcare_connect/funder_dashboard/tests/test_data_access.py::TestAddAllocation -v`
Expected: FAIL — `add_allocation` not defined

**Step 3: Implement methods**

Add to `commcare_connect/funder_dashboard/data_access.py` inside `FunderDashboardDataAccess`:

```python
    def add_allocation(self, fund_id: int, allocation: dict) -> FundRecord:
        """Append an allocation entry to a fund's allocations array."""
        fund = self.get_fund_by_id(fund_id)
        if not fund:
            raise ValueError(f"Fund {fund_id} not found")
        data = dict(fund.data)
        allocations = list(data.get("allocations", []))
        allocations.append(allocation)
        data["allocations"] = allocations
        return self.update_fund(fund_id, data)

    def remove_allocation(self, fund_id: int, index: int) -> FundRecord:
        """Remove an allocation entry by index."""
        fund = self.get_fund_by_id(fund_id)
        if not fund:
            raise ValueError(f"Fund {fund_id} not found")
        data = dict(fund.data)
        allocations = list(data.get("allocations", []))
        if 0 <= index < len(allocations):
            allocations.pop(index)
        data["allocations"] = allocations
        return self.update_fund(fund_id, data)
```

**Step 4: Run tests to verify they pass**

Run: `pytest commcare_connect/funder_dashboard/tests/test_data_access.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add commcare_connect/funder_dashboard/data_access.py commcare_connect/funder_dashboard/tests/test_data_access.py
git commit -m "feat: add allocation methods to FunderDashboardDataAccess"
```

---

### Task 3: Auto-create allocation in award flow

**Files:**
- Modify: `commcare_connect/solicitations_new/data_access.py`
- Modify: `commcare_connect/solicitations_new/views.py`
- Test: `commcare_connect/solicitations_new/tests/test_data_access.py`

**Step 1: Write failing test**

Add to `commcare_connect/solicitations_new/tests/test_data_access.py`:

```python
class TestAwardResponseAutoAllocation:
    def test_award_creates_fund_allocation(self):
        """When the solicitation has a fund_id, awarding auto-creates a fund allocation."""
        response_data = {
            "solicitation_id": 100,
            "status": "submitted",
            "llo_entity_id": "org1",
            "llo_entity_name": "Partner Org",
        }
        mock_response = ResponseRecord({"id": 10, "data": response_data})
        awarded_data = dict(response_data)
        awarded_data.update({"status": "awarded", "reward_budget": 50000, "org_id": "42"})
        mock_awarded = ResponseRecord({"id": 10, "data": awarded_data})

        solicitation_data = {"title": "Test RFP", "fund_id": 5}
        mock_solicitation = SolicitationRecord({"id": 100, "data": solicitation_data})

        da = SolicitationsNewDataAccess(program_id="1", access_token="tok")
        with (
            patch.object(da, "get_response_by_id", return_value=mock_response),
            patch.object(da, "update_response", return_value=mock_awarded),
            patch.object(da, "get_solicitation_by_id", return_value=mock_solicitation),
            patch(
                "commcare_connect.solicitations_new.data_access.FunderDashboardDataAccess"
            ) as MockFDA,
        ):
            mock_fda_instance = MockFDA.return_value
            da.award_response(10, reward_budget=50000, org_id="42")
            mock_fda_instance.add_allocation.assert_called_once()
            alloc = mock_fda_instance.add_allocation.call_args[1]["allocation"]
            assert alloc["amount"] == 50000
            assert alloc["type"] == "award"
            assert alloc["response_id"] == 10
            assert alloc["solicitation_id"] == 100
```

**Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/solicitations_new/tests/test_data_access.py::TestAwardResponseAutoAllocation -v`
Expected: FAIL — no auto-allocation logic yet

**Step 3: Implement auto-allocation in `award_response()`**

Modify `commcare_connect/solicitations_new/data_access.py`:

Add import at top:
```python
import logging

logger = logging.getLogger(__name__)
```

Replace the existing `award_response` method:

```python
    def award_response(self, response_id: int, reward_budget: int, org_id: str) -> ResponseRecord:
        """Mark a response as awarded with budget and org_id.

        If the parent solicitation has a fund_id, auto-creates a fund allocation.
        """
        current = self.get_response_by_id(response_id)
        if not current:
            raise ValueError(f"Response {response_id} not found")

        data = dict(current.data)
        data["status"] = "awarded"
        data["reward_budget"] = reward_budget
        data["org_id"] = org_id
        result = self.update_response(response_id, data)

        # Auto-create fund allocation if solicitation has a fund_id
        try:
            solicitation = self.get_solicitation_by_id(current.solicitation_id)
            if solicitation and solicitation.fund_id:
                from commcare_connect.funder_dashboard.data_access import FunderDashboardDataAccess

                fda = FunderDashboardDataAccess(access_token=self.access_token)
                fda.add_allocation(
                    fund_id=solicitation.fund_id,
                    allocation={
                        "program_id": self.program_id,
                        "program_name": "",
                        "amount": reward_budget,
                        "type": "award",
                        "solicitation_id": current.solicitation_id,
                        "response_id": response_id,
                        "org_id": org_id,
                        "org_name": current.llo_entity_name,
                        "notes": f"Award from {solicitation.title}",
                    },
                )
        except Exception:
            logger.exception("Failed to auto-create fund allocation for response %s", response_id)

        return result
```

**Step 4: Run tests to verify they pass**

Run: `pytest commcare_connect/solicitations_new/tests/test_data_access.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add commcare_connect/solicitations_new/data_access.py commcare_connect/solicitations_new/tests/test_data_access.py
git commit -m "feat: auto-create fund allocation on award"
```

---

### Task 4: Update fund_detail.html with allocations table and KPIs

**Files:**
- Modify: `commcare_connect/templates/funder_dashboard/fund_detail.html`

**Step 1: Update the KPI grid**

Replace the existing 4-column KPI grid (lines 43-90 of `fund_detail.html`) with a 4-column grid that replaces the "Programs" and "Currency" KPIs with "Committed" and "Remaining":

```html
    <!-- Fund KPIs -->
    <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <div class="bg-white rounded-xl shadow-sm p-5 border border-gray-200">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-indigo-50 rounded-lg flex items-center justify-center">
                    <i class="fa-solid fa-dollar-sign text-indigo-600"></i>
                </div>
                <div>
                    <div class="text-xs text-gray-500 uppercase tracking-wider">Total Budget</div>
                    <div class="text-xl font-bold text-gray-900">
                        {% if fund.total_budget %}${{ fund.total_budget|intcomma }}{% else %}&mdash;{% endif %}
                    </div>
                </div>
            </div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-5 border border-gray-200">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-amber-50 rounded-lg flex items-center justify-center">
                    <i class="fa-solid fa-file-invoice-dollar text-amber-600"></i>
                </div>
                <div>
                    <div class="text-xs text-gray-500 uppercase tracking-wider">Committed</div>
                    <div class="text-xl font-bold text-amber-600">${{ fund.committed_amount|intcomma }}</div>
                </div>
            </div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-5 border border-gray-200">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-green-50 rounded-lg flex items-center justify-center">
                    <i class="fa-solid fa-coins text-green-600"></i>
                </div>
                <div>
                    <div class="text-xs text-gray-500 uppercase tracking-wider">Remaining</div>
                    <div class="text-xl font-bold {% if fund.remaining_amount > 0 %}text-green-600{% else %}text-red-600{% endif %}">${{ fund.remaining_amount|intcomma }}</div>
                </div>
            </div>
        </div>
        <div class="bg-white rounded-xl shadow-sm p-5 border border-gray-200">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center">
                    <i class="fa-solid fa-flag text-blue-600"></i>
                </div>
                <div>
                    <div class="text-xs text-gray-500 uppercase tracking-wider">Status</div>
                    <div class="text-xl font-bold {% if fund.status == 'active' %}text-green-600{% else %}text-gray-600{% endif %}">{{ fund.status|title }}</div>
                </div>
            </div>
        </div>
    </div>
```

**Step 2: Add allocations table section**

Insert after the KPI grid and before the "Linked Programs" section (before line 92):

```html
    <!-- Allocations -->
    <div class="bg-white rounded-xl shadow-sm p-8 mb-6">
        <h2 class="text-lg font-semibold text-brand-deep-purple mb-4">
            <i class="fa-solid fa-file-invoice-dollar mr-2"></i>
            Allocations
        </h2>
        {% if fund.allocations %}
            <div class="overflow-x-auto">
                <table class="min-w-full divide-y divide-gray-200">
                    <thead>
                        <tr>
                            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Program / Recipient</th>
                            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Amount</th>
                            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Type</th>
                            <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Notes</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-100">
                        {% for alloc in fund.allocations %}
                        <tr>
                            <td class="px-4 py-3 text-sm text-gray-900">
                                {{ alloc.program_name|default:alloc.org_name|default:"—" }}
                                {% if alloc.program_id %}
                                    <span class="text-xs text-gray-400 ml-1">#{{ alloc.program_id }}</span>
                                {% endif %}
                            </td>
                            <td class="px-4 py-3 text-sm font-medium text-gray-900">${{ alloc.amount|intcomma }}</td>
                            <td class="px-4 py-3 text-sm">
                                {% if alloc.type == 'award' %}
                                    <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800">Award</span>
                                {% elif alloc.type == 'retroactive' %}
                                    <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">Retroactive</span>
                                {% else %}
                                    <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-800">Manual</span>
                                {% endif %}
                            </td>
                            <td class="px-4 py-3 text-sm text-gray-500">{{ alloc.notes|default:"" }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        {% else %}
            <div class="text-center py-6">
                <i class="fa-solid fa-file-invoice text-3xl text-gray-300 mb-2"></i>
                <p class="text-sm text-gray-500">No allocations yet. Edit this fund to add allocations.</p>
            </div>
        {% endif %}
    </div>
```

**Step 3: Run server and visually verify**

Run: `python manage.py runserver` and navigate to a fund detail page.

**Step 4: Commit**

```bash
git add commcare_connect/templates/funder_dashboard/fund_detail.html
git commit -m "feat: add allocations table and committed/remaining KPIs to fund detail"
```

---

### Task 5: Add allocations management to fund edit form

**Files:**
- Modify: `commcare_connect/funder_dashboard/forms.py`
- Modify: `commcare_connect/funder_dashboard/views.py`
- Modify: `commcare_connect/templates/funder_dashboard/fund_form.html`

**Step 1: Add `allocations_json` field to FundForm**

In `commcare_connect/funder_dashboard/forms.py`, add to the `FundForm` class:

```python
    allocations_json = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        label="Allocations (JSON)",
    )
```

And update `to_data_dict()` to handle it — add after the `delivery_types` block:

```python
        raw_allocations = self.cleaned_data.get("allocations_json", "")
        if raw_allocations:
            try:
                data["allocations"] = json.loads(raw_allocations)
            except (json.JSONDecodeError, TypeError):
                data["allocations"] = []
        else:
            data["allocations"] = []
```

**Step 2: Update FundEditView to pass allocations initial data**

In `commcare_connect/funder_dashboard/views.py`, in `FundEditView.get_context_data()`, add to the `initial` dict:

```python
                "allocations_json": json.dumps(fund.allocations),
```

**Step 3: Update fund_form.html with Alpine.js allocations section**

Insert before `{{ form.program_ids_json }}` (line 96) and after the Fund Details card closing `</div>` (line 94):

```html
        <!-- Allocations -->
        <div class="bg-white rounded-xl shadow-sm p-8" x-data='allocationsManager()'>
            <h2 class="text-xl font-semibold text-brand-deep-purple mb-6 pb-2 border-b border-gray-200">
                <i class="fa-solid fa-file-invoice-dollar mr-2"></i>
                Allocations
            </h2>

            <template x-for="(alloc, index) in allocations" :key="index">
                <div class="flex items-end gap-3 mb-3 p-3 bg-gray-50 rounded-lg border border-gray-200">
                    <div class="flex-1">
                        <label class="block text-xs font-medium text-gray-600 mb-1">Program Name</label>
                        <input type="text" x-model="alloc.program_name" placeholder="e.g. KMC Uganda"
                               class="w-full px-2 py-1.5 border border-gray-300 rounded text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500">
                    </div>
                    <div class="w-32">
                        <label class="block text-xs font-medium text-gray-600 mb-1">Amount</label>
                        <input type="number" x-model.number="alloc.amount" placeholder="0"
                               class="w-full px-2 py-1.5 border border-gray-300 rounded text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500">
                    </div>
                    <div class="w-36">
                        <label class="block text-xs font-medium text-gray-600 mb-1">Type</label>
                        <select x-model="alloc.type"
                                class="w-full px-2 py-1.5 border border-gray-300 rounded text-sm bg-white focus:outline-none focus:ring-1 focus:ring-indigo-500">
                            <option value="retroactive">Retroactive</option>
                            <option value="manual">Manual</option>
                            <option value="award">Award</option>
                        </select>
                    </div>
                    <div class="flex-1">
                        <label class="block text-xs font-medium text-gray-600 mb-1">Notes</label>
                        <input type="text" x-model="alloc.notes" placeholder="Optional notes"
                               class="w-full px-2 py-1.5 border border-gray-300 rounded text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500">
                    </div>
                    <button type="button" @click="removeAllocation(index)"
                            class="px-2 py-1.5 text-red-500 hover:text-red-700 hover:bg-red-50 rounded transition">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                </div>
            </template>

            <button type="button" @click="addAllocation()"
                    class="inline-flex items-center px-3 py-1.5 text-sm text-indigo-600 hover:text-indigo-700 hover:bg-indigo-50 rounded-lg transition">
                <i class="fa-solid fa-plus mr-1.5"></i> Add Allocation
            </button>

            <input type="hidden" name="allocations_json" :value="JSON.stringify(allocations)">
        </div>

        <script>
            function allocationsManager() {
                const raw = document.querySelector('input[name="allocations_json"][type="hidden"]:not([x-bind\\:value])')
                let initial = []
                if (raw && raw.value) {
                    try { initial = JSON.parse(raw.value) } catch(e) {}
                    raw.remove()
                }
                return {
                    allocations: initial,
                    addAllocation() {
                        this.allocations.push({
                            program_id: '', program_name: '', amount: 0,
                            type: 'retroactive', notes: '', org_id: '', org_name: ''
                        })
                    },
                    removeAllocation(index) {
                        this.allocations.splice(index, 1)
                    }
                }
            }
        </script>
```

**Step 4: Run server and test the form**

Run: `python manage.py runserver`, go to fund edit, add/remove allocations, save.

**Step 5: Commit**

```bash
git add commcare_connect/funder_dashboard/forms.py commcare_connect/funder_dashboard/views.py commcare_connect/templates/funder_dashboard/fund_form.html
git commit -m "feat: add allocations management to fund edit form"
```

---

### Task 6: Update existing unit tests for allocation changes

**Files:**
- Modify: `commcare_connect/funder_dashboard/tests/test_e2e_fund_flow.py`
- Modify: `commcare_connect/solicitations_new/tests/test_e2e_award_flow.py`

**Step 1: Update test_e2e_fund_flow.py**

In `TestStep3FundDetail.test_detail_shows_fund`, the KPIs changed — update assertions to check for "Committed" and "Remaining" instead of "Programs" and "Currency":

```python
    def test_detail_shows_fund(self):
        # ... existing setup ...
        assert "Committed" in content
        assert "Remaining" in content
```

In `TestStep4EditFund.test_edit_form_renders_with_initial`, verify allocations_json is in the form initial data.

**Step 2: Update test_e2e_award_flow.py**

The `award_response()` mock may need updating since it now tries to call `get_solicitation_by_id`. Add a mock for the solicitation lookup (returning a solicitation without `fund_id` so the auto-allocation path is skipped in unit tests).

**Step 3: Run all funder_dashboard and solicitations_new tests**

Run: `pytest commcare_connect/funder_dashboard/ commcare_connect/solicitations_new/ -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add commcare_connect/funder_dashboard/tests/ commcare_connect/solicitations_new/tests/
git commit -m "test: update unit tests for allocation changes"
```

---

### Task 7: Update e2e conftest with --profile option

**Files:**
- Modify: `commcare_connect/funder_dashboard/tests/e2e/conftest.py`

**Step 1: Add --profile option and update authenticated_context**

In `conftest.py`, add to `pytest_addoption()`:

```python
    parser.addoption(
        "--profile",
        action="store",
        default="test-user",
        help="TokenManager profile name for auth (default: test-user)",
    )
```

Update `authenticated_context` fixture to use the profile:

```python
@pytest.fixture(scope="session")
def authenticated_context(request, browser, live_server_url):
    """Create a browser context with a valid OAuth session using the specified profile."""
    profile = request.config.getoption("--profile")
    context = browser.new_context()
    page = context.new_page()

    auth_url = f"{live_server_url}/labs/test-auth/"
    if profile:
        auth_url += f"?profile={profile}"

    response = page.goto(auth_url)
    assert response.status == 200, f"test-auth failed: {page.content()}"

    body = response.json()
    assert body.get("success"), f"test-auth returned: {body}"

    page.close()
    yield context
    context.close()
```

Note: add `request` to the fixture signature.

**Step 2: Commit**

```bash
git add commcare_connect/funder_dashboard/tests/e2e/conftest.py
git commit -m "feat: add --profile option to e2e conftest"
```

---

### Task 8: Write e2e test — fund flow

**Files:**
- Create: `commcare_connect/funder_dashboard/tests/e2e/test_fund_flow.py`

**Step 1: Write the test file**

```python
"""
E2E test: Fund CRUD lifecycle.

Creates a fund, views it, edits it (adds an allocation),
verifies KPIs update correctly.

Run:
    pytest commcare_connect/funder_dashboard/tests/e2e/test_fund_flow.py \
        --ds=config.settings.local -o "addopts=" -v
"""
import time

import pytest

pytestmark = pytest.mark.e2e


class TestFundCRUDLifecycle:
    """Full fund create → view → edit (add allocation) → verify KPIs."""

    def test_portfolio_loads(self, auth_page, live_server_url, org_id):
        """Step 1: Portfolio page loads with KPI cards."""
        page = auth_page
        page.set_default_timeout(30_000)
        page.goto(f"{live_server_url}/funder/?organization_id={org_id}")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1").filter(has_text="Funder Dashboard").is_visible()
        assert page.get_by_text("Total Funds").is_visible()
        assert page.get_by_text("Create Fund").is_visible()

    def test_create_fund(self, auth_page, live_server_url, org_id):
        """Step 2: Create a new fund via the form."""
        page = auth_page
        page.set_default_timeout(30_000)

        # Navigate to create form
        page.goto(f"{live_server_url}/funder/fund/create/?organization_id={org_id}")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1").filter(has_text="Create New Fund").is_visible()

        # Fill in the form
        test_name = f"E2E Test Fund {int(time.time())}"
        page.fill("input[name='name']", test_name)
        page.fill("textarea[name='description']", "Created by e2e test")
        page.fill("input[name='total_budget']", "500000")
        page.fill("input[name='currency']", "USD")
        page.select_option("select[name='status']", "active")

        # Submit via page.request.post to avoid navigation timeout
        csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()
        response = page.request.post(
            f"{live_server_url}/funder/fund/create/?organization_id={org_id}",
            form={
                "csrfmiddlewaretoken": csrf_token,
                "name": test_name,
                "description": "Created by e2e test",
                "total_budget": "500000",
                "currency": "USD",
                "status": "active",
                "program_ids_json": "[]",
                "delivery_types_json": "[]",
                "allocations_json": "[]",
            },
            timeout=60_000,
        )
        assert response.ok or response.status == 302

        # Navigate to portfolio and verify fund appears
        page.goto(f"{live_server_url}/funder/?organization_id={org_id}")
        page.wait_for_load_state("domcontentloaded")
        assert page.get_by_text(test_name).is_visible(timeout=10_000)

    def test_fund_detail_shows_kpis(self, auth_page, live_server_url, org_id):
        """Step 3: Fund detail page shows correct KPIs including Committed/Remaining."""
        page = auth_page
        page.set_default_timeout(30_000)

        # Navigate to portfolio and click the first fund
        page.goto(f"{live_server_url}/funder/?organization_id={org_id}")
        page.wait_for_load_state("domcontentloaded")

        # Click the first fund card link
        fund_link = page.locator("a[href*='/funder/fund/']").first
        fund_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Verify KPIs are visible
        assert page.get_by_text("Total Budget").is_visible()
        assert page.get_by_text("Committed").is_visible()
        assert page.get_by_text("Remaining").is_visible()
        assert page.get_by_text("Status").is_visible()

    def test_edit_fund_add_allocation(self, auth_page, live_server_url, org_id):
        """Step 4: Edit a fund and add a retroactive allocation."""
        page = auth_page
        page.set_default_timeout(30_000)

        # Navigate to portfolio, click first fund, then Edit
        page.goto(f"{live_server_url}/funder/?organization_id={org_id}")
        page.wait_for_load_state("domcontentloaded")

        fund_link = page.locator("a[href*='/funder/fund/']").first
        fund_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Get the fund detail URL to extract the pk
        detail_url = page.url
        # Click Edit Fund
        page.get_by_text("Edit Fund").click()
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1").filter(has_text="Edit Fund").is_visible()

        # Click "Add Allocation"
        page.get_by_text("Add Allocation").click()

        # Fill allocation fields
        page.locator("input[x-model='alloc.program_name']").first.fill("Test Program")
        page.locator("input[x-model\\.number='alloc.amount']").first.fill("100000")
        page.locator("select[x-model='alloc.type']").first.select_option("retroactive")
        page.locator("input[x-model='alloc.notes']").first.fill("E2E test allocation")

        # Submit the form
        page.locator("button[type='submit']").click()
        page.wait_for_load_state("domcontentloaded")

        # Navigate back to the fund detail and verify allocation appears
        page.goto(detail_url)
        page.wait_for_load_state("domcontentloaded")

        assert page.get_by_text("Test Program").is_visible()
        assert page.get_by_text("Retroactive").is_visible()
        assert page.get_by_text("100,000").is_visible()
```

**Step 2: Run the test**

Run: `pytest commcare_connect/funder_dashboard/tests/e2e/test_fund_flow.py --ds=config.settings.local -o "addopts=" -v`

Note: Requires `inv up` (docker services), Django dev server on 8001 (auto-started by conftest), and valid `test-user` CLI token.

**Step 3: Commit**

```bash
git add commcare_connect/funder_dashboard/tests/e2e/test_fund_flow.py
git commit -m "test: add e2e fund CRUD lifecycle test"
```

---

### Task 9: Write e2e test — award flow with auto-allocation

**Files:**
- Create: `commcare_connect/funder_dashboard/tests/e2e/test_award_flow.py`

**Step 1: Write the test file**

```python
"""
E2E test: Solicitation award flow with fund auto-allocation.

Creates a solicitation linked to a fund, submits a response,
awards it, and verifies the allocation appears on the fund.

Run:
    pytest commcare_connect/funder_dashboard/tests/e2e/test_award_flow.py \
        --ds=config.settings.local -o "addopts=" -v
"""
import time

import pytest

pytestmark = pytest.mark.e2e


class TestAwardWithFundAllocation:
    """Award a solicitation response and verify fund allocation is created."""

    def test_full_award_flow(self, auth_page, live_server_url, org_id, program_id):
        """
        End-to-end:
        1. Create a fund
        2. Create a solicitation linked to the fund
        3. Submit a response
        4. Award the response
        5. Verify fund detail shows the auto-allocation
        """
        page = auth_page
        page.set_default_timeout(30_000)
        timestamp = int(time.time())

        # --- Step 1: Create a fund ---
        fund_name = f"E2E Award Fund {timestamp}"
        csrf_url = f"{live_server_url}/funder/fund/create/?organization_id={org_id}"
        page.goto(csrf_url)
        page.wait_for_load_state("domcontentloaded")
        csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()

        response = page.request.post(
            csrf_url,
            form={
                "csrfmiddlewaretoken": csrf_token,
                "name": fund_name,
                "description": "E2E award test fund",
                "total_budget": "1000000",
                "currency": "USD",
                "status": "active",
                "program_ids_json": "[]",
                "delivery_types_json": "[]",
                "allocations_json": "[]",
            },
            timeout=60_000,
        )
        assert response.ok or response.status == 302

        # Find the fund to get its ID
        page.goto(f"{live_server_url}/funder/?organization_id={org_id}")
        page.wait_for_load_state("domcontentloaded")
        fund_link = page.locator(f"a:has-text('{fund_name}')").first
        fund_href = fund_link.get_attribute("href")
        fund_id = fund_href.strip("/").split("/")[-1]

        # --- Step 2: Create a solicitation linked to the fund ---
        sol_title = f"E2E Test RFP {timestamp}"
        sol_url = f"{live_server_url}/solicitations_new/create/?program_id={program_id}"
        page.goto(sol_url)
        page.wait_for_load_state("domcontentloaded")
        csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()

        response = page.request.post(
            sol_url,
            form={
                "csrfmiddlewaretoken": csrf_token,
                "title": sol_title,
                "description": "E2E test solicitation",
                "solicitation_type": "rfp",
                "status": "active",
                "is_public": "true",
                "questions_json": "[]",
                "fund_id": fund_id,
            },
            timeout=60_000,
        )
        assert response.ok or response.status == 302

        # Find the solicitation ID
        page.goto(f"{live_server_url}/solicitations_new/manage/?program_id={program_id}")
        page.wait_for_load_state("domcontentloaded")
        sol_link = page.locator(f"a:has-text('{sol_title}')").first
        sol_href = sol_link.get_attribute("href")
        sol_id = sol_href.strip("/").split("/")[-1]

        # --- Step 3: Submit a response ---
        respond_url = f"{live_server_url}/solicitations_new/{sol_id}/respond/?program_id={program_id}"
        page.goto(respond_url)
        page.wait_for_load_state("domcontentloaded")
        csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()

        response = page.request.post(
            respond_url,
            form={
                "csrfmiddlewaretoken": csrf_token,
                "submit": "true",
            },
            timeout=60_000,
        )
        assert response.ok or response.status == 302

        # --- Step 4: Find the response and award it ---
        page.goto(f"{live_server_url}/solicitations_new/{sol_id}/responses/?program_id={program_id}")
        page.wait_for_load_state("domcontentloaded")

        # Click the first View link in the responses table
        view_link = page.locator("a:has-text('View')").first
        view_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Click Award button
        award_link = page.locator("a:has-text('Award')").first
        award_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Fill award form
        page.fill("input[name='org_id']", org_id)
        page.fill("input[name='reward_budget']", "250000")

        csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()
        award_url = page.url
        response = page.request.post(
            award_url,
            form={
                "csrfmiddlewaretoken": csrf_token,
                "org_id": org_id,
                "reward_budget": "250000",
            },
            timeout=60_000,
        )
        assert response.ok or response.status == 302

        # --- Step 5: Verify fund allocation ---
        page.goto(f"{live_server_url}/funder/fund/{fund_id}/?organization_id={org_id}")
        page.wait_for_load_state("domcontentloaded")

        # Verify the auto-allocation appears
        assert page.get_by_text("Award").first.is_visible()
        assert page.get_by_text("250,000").is_visible()
        # KPIs should reflect the allocation
        assert page.get_by_text("Committed").is_visible()
```

**Step 2: Run the test**

Run: `pytest commcare_connect/funder_dashboard/tests/e2e/test_award_flow.py --ds=config.settings.local -o "addopts=" -v`

**Step 3: Commit**

```bash
git add commcare_connect/funder_dashboard/tests/e2e/test_award_flow.py
git commit -m "test: add e2e award flow with fund auto-allocation test"
```

---

### Task 10: Run full test suite and lint

**Step 1: Run all unit tests**

Run: `pytest commcare_connect/funder_dashboard/ commcare_connect/solicitations_new/ commcare_connect/labs/tests/test_token_manager_profiles.py -v`
Expected: All PASS

**Step 2: Run linting**

Run: `pre-commit run --all-files`
Expected: All PASS (fix any issues)

**Step 3: Run e2e tests**

Run: `pytest commcare_connect/funder_dashboard/tests/e2e/ --ds=config.settings.local -o "addopts=" -v`
Expected: All PASS

**Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "style: fix lint issues"
```
