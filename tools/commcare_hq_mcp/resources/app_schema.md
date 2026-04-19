# CommCare Compact JSON Format Specification

This document defines the compact JSON format used to describe CommCare application structures. It covers the top-level schema, all field definitions, supported question types, reserved property names, type selection guidelines, and case lifecycle patterns.

## JSON Structure

```json
{
  "app_name": "App Name",
  "modules": [
    {
      "name": "Module Name",
      "case_type": "case_type_name",
      "forms": [
        {
          "name": "Form Name",
          "type": "registration | followup | survey",
          "case_name_field": "question_id_for_case_name",
          "case_properties": { "case_prop": "question_id" },
          "case_preload": { "question_id": "case_prop" },
          "close_case": true | {"question": "question_id", "answer": "value"},
          "child_cases": [{"case_type": "child_type", "case_name_field": "question_id", "case_properties": {"prop": "question_id"}, "relationship": "child"}],
          "questions": [
            { "id": "field_id", "type": "text", "label": "Label", "required": true },
            { "id": "field_id", "type": "select1", "label": "Label", "options": [{"value": "v", "label": "L"}] }
          ]
        }
      ],
      "case_list_columns": [{"field": "case_prop", "header": "Header"}]
    }
  ]
}
```

## Field Reference

### Module

- **name**: Display name for the module/menu.
- **case_type**: Required if any form is "registration" or "followup". Use a short snake_case name (e.g. "patient", "household_visit"). Only letters, digits, underscores, hyphens.
- **forms**: Array of forms in this module.
- **case_list_columns**: Columns shown in the case list. Each has "field" (case property name) and "header" (display text). Do NOT include "name" -- it is shown automatically. Do NOT use reserved property names.

### Form

- **name**: Display name.
- **type**: One of:
  - "registration" -- creates a new case. MUST have case_name_field.
  - "followup" -- updates an existing case. Should have case_preload to pre-fill fields with existing case data.
  - "survey" -- no case management (standalone data collection).
- **case_name_field**: (registration only) The question id whose value becomes the case name.
- **case_properties**: Map of case property name to question id. These question values get saved to the case.
  - Do NOT include the case_name_field here -- it is saved automatically.
  - NEVER use reserved property names as keys (see list below).
  - NEVER map media/binary questions (image, audio, video, signature) to case properties -- CommCare cannot store binary data in case properties. Only map text, numeric, date, and select values.
- **case_preload**: (followup only) Map of question id to case property name. Pre-fills form questions with existing case data when the form opens.
  - The question gets populated with the case property's current value.
  - To load the case name, use "case_name" as the value (reading reserved properties is fine).
  - If the user should be able to EDIT a preloaded value and save it back, include the same field in BOTH case_preload AND case_properties.
- **close_case**: (followup only, optional) Closes the parent case when this form is submitted.
  - Set to `true` for forms that ALWAYS close the case (e.g., dedicated "Close Case" or "Discharge" forms).
  - Set to `{"question": "question_id", "answer": "value"}` for CONDITIONAL close -- the case closes only when that question's answer matches the value.
  - Omit entirely if the form should NOT close the case (default).
  - Only valid on "followup" forms (you must select a case before you can close it).
- **child_cases**: (optional) Creates child/sub-cases linked to the current case. Array of objects:
  - "case_type": (required) The child case type in snake_case (e.g. "referral", "pregnancy", "household_member").
  - "case_name_field": (required) Question id whose value becomes the child case name.
  - "case_properties": (optional) Map of child case property name to question id.
  - "relationship": (optional) "child" (default) or "extension". Use "extension" when the child should prevent the parent from being closed.
  - "repeat_context": (optional) Question id of a repeat group -- creates one child case per repeat entry.
  - Valid on both "registration" and "followup" forms. The parent case index is set automatically.
- **questions**: Array of questions in the form.

### Question

- **id**: Unique identifier within the form. Use snake_case (e.g. "patient_name", "visit_date"). Must start with a letter.
- **type**: One of: "text", "phone", "secret", "int", "decimal", "long", "date", "time", "datetime", "select1", "select", "geopoint", "barcode", "image", "audio", "video", "signature", "trigger", "hidden", "group", "repeat"
  - "text" = free text input (also used for fields that get pre-filled from case data)
  - "phone" = phone number / numeric ID input (shows numeric keyboard, stores as string)
  - "secret" = password / PIN input (input is masked)
  - "int" = whole number, "decimal" = decimal number, "long" = large whole number
  - "date" = date picker, "time" = time picker, "datetime" = date and time picker
  - "select1" = single-select dropdown, "select" = multi-select checkboxes
  - "geopoint" = GPS location capture
  - "barcode" = barcode/QR code scanner
  - "image" = photo capture, "audio" = audio recording, "video" = video recording
  - "signature" = signature capture pad
  - "trigger" = acknowledgment button (OK button, no data entry -- do NOT use for displaying case data)
  - "hidden" = hidden calculated value (not shown to user -- use with "calculate" field)
  - "group" = question group displayed together on one screen (use "children" array for nested questions)
  - "repeat" = repeating group -- user can add multiple entries (use "children" array for nested questions)
- **label**: Human-readable question text. Write clear, natural labels like "Patient Name", "Date of Birth", "Blood Pressure". Never put technical notes like "(loaded from case)" in labels.
- **hint**: (optional) Help text shown below the question.
- **required**: (optional) true if the question must be answered.
- **readonly**: (optional) true if the field should be visible but not editable (use for display-only preloaded values).
- **constraint**: (optional) XPath constraint expression, e.g. ". > 0 and . < 150".
- **constraint_msg**: (optional) Error message when constraint fails.
- **relevant**: (optional) XPath expression -- question only shows when this is true, e.g. "/data/age > 18".
- **calculate**: (optional) XPath expression for auto-computed value (use with type "hidden" for background calculations).
- **options**: (required for select1/select) Array of {value, label} pairs.
- **children**: (required for group/repeat) Array of nested question objects.

## Reserved Case Property Names

The following names are reserved by CommCare and must NEVER be used in case_properties or case_preload:

case_id, case_name, case_type, closed, closed_by, closed_on, date, date_modified, date_opened, doc_type, domain, external_id, index, indices, modified_on, name, opened_by, opened_on, owner_id, server_modified_on, status, type, user_id, xform_id

Use descriptive alternatives instead: visit_date, patient_type, case_status, full_name, etc.

Do NOT try to preload case_name -- the case name is already shown when the user selects a case from the list.

## Smart Type Selection

Always use the most specific question type available:

- Phone numbers, mobile numbers, contact numbers, numeric IDs --> "phone" (NOT "text")
- Passwords, PINs, security codes --> "secret" (NOT "text")
- Dates (birth date, visit date, due date) --> "date" (NOT "text")
- Times (appointment time, shift start) --> "time" (NOT "text")
- Date + time together --> "datetime"
- Age, count, number of children, quantity --> "int" (NOT "text")
- Weight, height, temperature, BMI, price --> "decimal" (NOT "text")
- Yes/No, Male/Female, any fixed choices --> "select1" with options (NOT "text")
- Multiple selections (symptoms, services) --> "select" with options
- GPS/location capture --> "geopoint"
- Photos, ID photos, wound photos --> "image"
- Voice notes, recorded interviews --> "audio"
- Video evidence, demonstrations --> "video"
- Consent signatures, approval signatures --> "signature"
- Scan barcodes or QR codes --> "barcode"
- Calculated values (BMI, age from DOB, risk score, total) --> "hidden" with "calculate"
- Groups of related questions shown together --> "group" with "children"
- Repeating entries (multiple children, multiple visits) --> "repeat" with "children"
- ONLY use "text" for truly free-text fields: names, addresses, notes, descriptions, comments

## Case Lifecycle -- Closing Cases and Creating Child Cases

The compact JSON format supports the full lifecycle of a case, not just registration and updates. Cases can be closed and child cases can be created as part of form submissions.

### Closing Cases

Set `close_case` on followup forms to close a case. Common patterns:

- Dedicated close/end forms: "Close Case", "Discharge Patient", "Exit Program", "Archive Record", "End Treatment" --> close_case: true
- Death or final outcome forms: "Death Notification", "Final Assessment", "Case Resolution" --> close_case: true
- Forms with outcome questions where some answers mean the case is done:
  - "Outcome" with "discharged", "deceased", "transferred out", "completed" --> close_case: {"question": "outcome", "answer": "discharged"}
  - "Program Status" with "graduated", "dropped out", "completed" --> conditional close on the exit answer
- If a form ALWAYS closes the case (no conditional logic needed), use close_case: true
- If only SOME outcomes close the case, use close_case: {"question": "id", "answer": "closing_value"} -- pick the one closing answer, or use close_case: true with a required confirmation question

### Creating Child Cases

Set `child_cases` to create sub-cases linked to the current case. Common patterns:

- "Register X under Y" (e.g., "register a pregnancy under a mother") --> child case type "pregnancy" on the parent "mother" form
- "Add a referral" --> child case type "referral"
- "Register household members" --> child cases in a repeat group (set repeat_context)
- "Create a visit record / encounter" --> child case type "visit"
- "Log a pregnancy / immunization / service" --> child case type for the sub-entity
- Any time an entity belongs to or is owned by the parent case
- If multiple child entries are needed in one form submission, use a repeat group with repeat_context
- Use "extension" relationship only when the child must prevent the parent from closing (e.g., active pregnancy prevents closing the mother case)

## Validation Rules

1. Every module with registration or followup forms MUST have a case_type.
2. Every registration form MUST have case_name_field pointing to a valid question id.
3. Every form MUST have at least one question.
4. Question ids must be unique within a form and use snake_case starting with a letter.
5. select1/select questions MUST have at least 2 options.
6. case_properties keys must NOT be reserved words. case_properties values must reference valid question ids.
7. case_preload keys must reference valid question ids. case_preload values must NOT be reserved words.
8. For followup forms, use real input fields (text, date, select1, etc.) for preloaded values -- NOT triggers. Use readonly: true for display-only fields.
9. Every question should serve a purpose. Labels should be clear and professional.

## Example: Patient Tracking App

```json
{
  "app_name": "Patient Tracker",
  "modules": [
    {
      "name": "Patients",
      "case_type": "patient",
      "forms": [
        {
          "name": "Register Patient",
          "type": "registration",
          "case_name_field": "patient_name",
          "case_properties": {
            "age": "age",
            "gender": "gender",
            "phone": "phone"
          },
          "questions": [
            {
              "id": "patient_name",
              "type": "text",
              "label": "Patient Name",
              "required": true
            },
            {
              "id": "age",
              "type": "int",
              "label": "Age",
              "required": true,
              "constraint": ". > 0 and . < 150",
              "constraint_msg": "Age must be between 1 and 149"
            },
            {
              "id": "gender",
              "type": "select1",
              "label": "Gender",
              "required": true,
              "options": [
                { "value": "male", "label": "Male" },
                { "value": "female", "label": "Female" },
                { "value": "other", "label": "Other" }
              ]
            },
            { "id": "phone", "type": "phone", "label": "Phone Number" }
          ]
        },
        {
          "name": "Follow-up Visit",
          "type": "followup",
          "case_preload": {
            "current_status": "visit_status",
            "current_phone": "phone"
          },
          "case_properties": {
            "visit_status": "visit_status",
            "last_visit_date": "visit_date",
            "visit_notes": "notes"
          },
          "questions": [
            {
              "id": "current_status",
              "type": "text",
              "label": "Current Status",
              "readonly": true
            },
            {
              "id": "current_phone",
              "type": "text",
              "label": "Phone on File",
              "readonly": true
            },
            {
              "id": "visit_date",
              "type": "date",
              "label": "Visit Date",
              "required": true
            },
            {
              "id": "visit_status",
              "type": "select1",
              "label": "Updated Status",
              "options": [
                { "value": "active", "label": "Active" },
                { "value": "recovered", "label": "Recovered" },
                { "value": "referred", "label": "Referred" }
              ]
            },
            { "id": "notes", "type": "text", "label": "Visit Notes" }
          ]
        },
        {
          "name": "Discharge Patient",
          "type": "followup",
          "close_case": { "question": "confirm_discharge", "answer": "yes" },
          "case_properties": {
            "discharge_reason": "reason",
            "discharge_date": "discharge_date"
          },
          "questions": [
            {
              "id": "reason",
              "type": "select1",
              "label": "Reason for Discharge",
              "required": true,
              "options": [
                { "value": "recovered", "label": "Recovered" },
                { "value": "transferred", "label": "Transferred" },
                { "value": "deceased", "label": "Deceased" },
                { "value": "other", "label": "Other" }
              ]
            },
            {
              "id": "discharge_date",
              "type": "date",
              "label": "Discharge Date",
              "required": true
            },
            {
              "id": "confirm_discharge",
              "type": "select1",
              "label": "Confirm Discharge?",
              "required": true,
              "options": [
                { "value": "yes", "label": "Yes, close this case" },
                { "value": "no", "label": "No, keep case open" }
              ]
            }
          ]
        }
      ],
      "case_list_columns": [
        { "field": "age", "header": "Age" },
        { "field": "gender", "header": "Gender" },
        { "field": "visit_status", "header": "Status" }
      ]
    }
  ]
}
```

### Example Notes

- The registration form creates a patient case. `patient_name` becomes the case name. `age`, `gender`, `phone` are saved as case properties.
- The follow-up form PRELOADS the current status and phone from the case into readonly fields. The worker sees existing data and fills in new visit details. `visit_status` and `last_visit_date` get saved back to the case.
- The discharge form CONDITIONALLY closes the case -- only when the user confirms "Yes". The discharge reason and date are saved to the case before closing.
- The case name is NOT preloaded -- it is already shown when the user selects the case from the list.
- The case list shows age, gender, and status columns (name is shown automatically by CommCare HQ).
