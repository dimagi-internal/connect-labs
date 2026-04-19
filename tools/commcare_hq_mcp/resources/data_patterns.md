# CommCare Form Data Patterns

How CommCare form submissions look as JSON when retrieved via API — the operational
reality needed for mapping pipeline schema field paths.

## Form Submission JSON Structure

When a CommCare form is submitted, the API returns:

```json
{
  "id": "form-uuid",
  "form": {
    "@xmlns": "http://openrosa.org/formdesigner/FORM-UUID",
    "@name": "Visit Form",
    "question_id": "value",
    "group_name": {
      "nested_question": "value"
    },
    "repeat_group": [
      { "item_question": "value1" },
      { "item_question": "value2" }
    ],
    "case": {
      "@case_id": "case-uuid",
      "update": {
        "property_name": "value"
      }
    },
    "meta": {
      "userID": "user-uuid",
      "timeStart": "2026-01-15T10:30:00Z",
      "timeEnd": "2026-01-15T10:35:00Z"
    }
  },
  "received_on": "2026-01-15T10:35:01Z",
  "app_id": "app-uuid"
}
```

## Question Path → JSON Path Mapping Rules

CommCare question IDs map to form submission JSON paths as follows:

| Question path in app definition                 | JSON path in form submission   |
| ----------------------------------------------- | ------------------------------ |
| `/data/weight`                                  | `form.weight`                  |
| `/data/child_info/birth_weight` (inside group)  | `form.child_info.birth_weight` |
| `/data/visits/visit_date` (inside repeat)       | `form.visits[].visit_date`     |
| `/data/case/update/last_weight` (case property) | `form.case.update.last_weight` |
| `/data/case/@case_id` (case reference)          | `form.case.@case_id`           |
| `/data/meta/userID` (form metadata)             | `form.meta.userID`             |

**Rules:**

1. Strip the `/data/` prefix and replace with `form.`
2. Groups create nested objects: `/data/group/question` → `form.group.question`
3. Repeat groups create arrays: `/data/repeat/question` → `form.repeat[].question`
4. Case blocks appear at `form.case` (or deeper: `form.group.case`)
5. The `@` prefix on attributes is preserved: `@case_id`, `@xmlns`, `@name`
6. The `meta` block is always at `form.meta` with `userID`, `timeStart`, `timeEnd`, etc.

## Case Block Nesting

Case blocks can appear at ANY depth in the form JSON. They are identified by the
presence of `@case_id` in a dict:

```json
// Top-level case
"form": { "case": { "@case_id": "abc", "update": { "weight": "2500" } } }

// Nested in a group
"form": { "child_group": { "case": { "@case_id": "def", "create": { ... } } } }

// Inside a repeat group (one case per repeat entry)
"form": { "household_members": [
  { "case": { "@case_id": "ghi", "update": { ... } } },
  { "case": { "@case_id": "jkl", "update": { ... } } }
] }
```

## Common Field Patterns

### Weight/measurements

```
form.weight              → current weight (usually grams as string)
form.birth_weight        → birth weight
form.child_weight_visit  → weight at visit (alternative naming)
```

### GPS/Location

```
form.gps                 → "lat lon altitude accuracy" (space-separated string)
```

### Dates

```
form.visit_date          → "2026-01-15" (date string)
form.meta.timeStart      → "2026-01-15T10:30:00Z" (form open time)
form.meta.timeEnd        → "2026-01-15T10:35:00Z" (form submit time)
```

### Case identification

```
form.case.@case_id       → the case being updated
form.subcase_0.case.@case_id → child case (when creating sub-cases)
```

### Beneficiary/entity linking

```
form.case.@case_id          → the beneficiary case ID (most common)
form.case.index.parent      → parent case reference
```

## Common Pitfalls

1. **Field names are case-sensitive** — `form.Weight` ≠ `form.weight`
2. **Repeat groups become arrays** — even if there's only one entry
3. **Empty fields may be omitted** — check for key existence, don't assume all fields present
4. **Select multiple values are space-separated** — `"option1 option2 option3"`
5. **Numbers are strings** — weights, ages, etc. come as `"2500"` not `2500`
6. **GPS is a space-separated string** — `"0.3456 32.1234 1200 10"` (lat, lon, alt, accuracy)
7. **form_json from Connect visits** — may be Python repr format (`{'key': 'value'}` with single quotes) instead of valid JSON. Use `ast.literal_eval` as fallback.
8. **@-prefixed attributes** — `@case_id`, `@xmlns`, `@name` are XML attribute artifacts preserved in JSON
