# Bulk Image Audit — Dynamic Image Types & Review Filter

**Date:** 2026-03-10
**Status:** Approved

## Overview

Update the Bulk Image Audit workflow template to:
1. Remove the redundant Opportunity selector (opportunity is already scoped from context)
2. Dynamically fetch image question types from CommCare HQ instead of using a hardcoded list
3. Support multi-select image types when creating a run
4. Add an image type filter dropdown on the bulk review page when multiple types are loaded

---

## Change 1: Remove Opportunity Selector

The Config phase currently shows a search + multi-select UI for opportunities. This is redundant — `instance.opportunity_id` is always available in the workflow context and already auto-populates `selectedOpps` via a `useEffect` on mount.

**What changes:**
- Remove the Opportunity selector section from `ConfigPhase` JSX
- Remove state: `searchQuery`, `searchResults`, `isSearching`
- Remove functions: `handleOppSearch`, `addOpp`, `removeOpp`
- Keep the silent auto-populate `useEffect` that sets `selectedOpps` from `instance.opportunity_id`
- Keep the `disabled={selectedOpps.length === 0}` guard on the Create button (defensive, since the opportunity must exist to proceed)

---

## Change 2: Dynamic Image Types from CommCare HQ

### New Django endpoint

**URL:** `GET /audit/api/opportunity/<opp_id>/image-questions/`

**Auth:** Requires Labs session login.

**Flow:**
1. Call `fetch_opportunity_metadata(access_token, opp_id)` (reuse existing function from `mbw_monitoring/data_fetchers.py`) to get `cc_domain` and `cc_app_id` (deliver app preferred, learn app fallback)
2. Call CommCare HQ app definition API: `GET {COMMCARE_HQ_URL}/a/{cc_domain}/api/v0.5/application/{cc_app_id}/` with `Authorization: ApiKey {COMMCARE_USERNAME}:{COMMCARE_API_KEY}`
3. Traverse all forms in the app, collecting `Image`-type questions
4. For each image question, filter out always-false questions:
   - Check the question's own `relevant` field
   - Walk up the ancestor chain by prefix-matching `/data/...` paths in the flat question list
   - Check each ancestor group's `relevant` field
5. Auto-detect `hq_url_path`: for each image question at path `/data/.../X`, look for a sibling `DataBindOnly` question whose `calculate` field contains that image path — this auto-discovers the `photo_link_ors` / `muac_photo_link` pattern
6. Return: `[{id, label, path, hq_url_path, form_name}]`

**Always-false relevant patterns** (whitespace-normalized before comparison):
- `1=2`
- `false()`
- `0=1`

**Response format:**
```json
[
  {
    "id": "ors_photo",
    "label": "Please capture a photo of dropping ORS/Zinc packets or the household's photo.",
    "path": "ors_group/ors_photo",
    "hq_url_path": "ors_group/photo_link_ors",
    "form_name": "Health Service Delivery"
  },
  ...
]
```

**URL/Routing:** Add to `audit/urls.py`:
```python
path("api/opportunity/<int:opp_id>/image-questions/",
     views.OpportunityImageQuestionsAPIView.as_view(),
     name="opportunity_image_questions")
```

### workflow template changes (bulk_image_audit.py)

**State changes:**
- Remove: `imageType` (single string)
- Add: `imageQuestions` (array of `{id, label, path, hq_url_path, form_name}`, loaded from API)
- Add: `imageQuestionsLoading` (bool)
- Add: `imageQuestionsError` (string or null)
- Add: `selectedImageTypeIds` (array of strings, default: all IDs selected after load)

**On mount:** Fetch `/audit/api/opportunity/{instance.opportunity_id}/image-questions/`. Default-select all returned types.

**Config UI — Image Types section:**
- Replace single-select button group with multi-select checkbox cards
- Each card shows the question label + form name
- Show loading spinner while fetching; show error message if fetch fails
- "Select All" / "Deselect All" convenience buttons

**`handleCreate` changes:**
- Build `related_fields` as an array — one entry per selected image type:
  ```js
  related_fields: selectedImageTypeIds.map(id => {
    const t = imageQuestions.find(q => q.id === id);
    return {
      image_path: t.path,
      hq_url_path: t.hq_url_path || null,
      filter_by_image: true
    };
  })
  ```
- Save selected image type metadata in workflow config state:
  ```js
  config.image_types = selectedImageTypeIds.map(id => {
    const t = imageQuestions.find(q => q.id === id);
    return { id, label: t.label };
  });
  ```

---

## Change 3: Image Type Dropdown on Review Page

**File:** `connect_labs/templates/audit/bulk_assessment.html`

**What changes:**
- Add a new `<select>` dropdown in the filters section (alongside the existing Status dropdown)
- Visible only when `questionIds.length > 1` (Alpine `x-show`)
- Options: "All Image Types" (value `""`) + one option per `questionId`
- Labels are auto-formatted client-side: `ors_photo` → `ORS Photo`, `vita_capsule_photo` → `Vita Capsule Photo` (split on `_`, title-case each word)
- Binding: `x-model="selectedQuestionId"` + `@change="applyFilters()"` — reuses the existing filtering mechanism with zero backend changes

**Alpine additions:**
```js
formatQuestionId(qid) {
    return qid.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}
```

---

## Architecture Notes

- No new models or migrations needed
- The new Django endpoint uses credentials already present in settings (`COMMCARE_API_KEY`, `COMMCARE_USERNAME`) and the existing `fetch_opportunity_metadata` utility
- The bulk review page filter is entirely client-side — `questionIds` is already populated from the existing `bulk_assessment_data` API response
- The `hq_url_path` auto-detection is a best-effort heuristic; if no matching `DataBindOnly` field is found, `hq_url_path` is `null` (graceful fallback — Connect blob images still work)

---

## Files Changed

| File | Change |
|---|---|
| `connect_labs/audit/views.py` | Add `OpportunityImageQuestionsAPIView` |
| `connect_labs/audit/urls.py` | Register new URL |
| `connect_labs/workflow/templates/bulk_image_audit.py` | Remove opp selector, add dynamic image types, multi-select |
| `connect_labs/templates/audit/bulk_assessment.html` | Add image type dropdown to filter bar |
