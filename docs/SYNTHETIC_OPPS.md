# Synthetic Opportunities — Operator Guide

Labs can serve fake `/export/opportunity/<id>/...` data for opportunities
registered as "synthetic". Use this for demos, grant prototyping, and
visualization iteration before real FLW data is collected.

## How it works (one paragraph)

Every call that would otherwise hit Connect's export API goes through
`get_export_client(opp_id, access_token)`. If the opp is registered in
`/labs/synthetic/`, the factory returns a `SyntheticExportClient` that reads
fixture JSON from the opp's Google Drive folder. Writes (`LabsRecord` updates,
audit reviews, workflow state changes) are unaffected — they still land in
prod. Clean up by deleting the demo opp's `LabsRecord`s manually.

## One-time setup

1. Obtain the `LABS_SYNTHETIC_GDRIVE_SA_KEY` env var value (service account JSON
   from 1Password item `connect-labs GCP service account key (connect-labs-sa)`).
   Confirm it is set in the target environment.
2. Obtain a Drive folder to host synthetic opps ("labs-synthetic parent") and
   share it with the service account email as **Editor**.
3. Set `LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID` env var to that folder's ID.

## Creating a synthetic opp

**Recommended flow: dump from prod via the UI.**

1. Pick an existing opportunity in the labs context selector (top nav).
2. Go to `/labs/synthetic/` and click "+ New synthetic opp".
3. Select "Dump fresh data from prod → new GDrive folder" and click **Start dump**.
4. Watch the stream: the service account creates a timestamped folder under the
   labs-synthetic parent, pulls the five export endpoints one at a time, and
   uploads them as JSON. On completion the Drive folder ID is auto-populated.
5. Fill in a label (e.g. "Baobab demo starter"), hit **Save**.
6. Edit the JSON files in Drive to anonymize names, flip statuses, etc.
7. Back in `/labs/synthetic/`, click **Reload fixtures** on the row so the in-process
   fixture cache picks up your edits.

**Fallback: manual dump via `curl`.** Use when the UI dump isn't available (no
SA configured, no labs access for the opp, etc.):

```bash
TOKEN=<your prod token>
OPP=<reference opp id>
BASE="https://connect.dimagi.com"

for EP in "" user_visits user_data completed_works completed_module; do
  FILE="${EP:-opportunity}.json"
  curl -s -H "Authorization: Bearer $TOKEN" \
       -H "Accept: application/json; version=2.0" \
       "$BASE/export/opportunity/$OPP/$EP${EP:+/}" \
    | jq '.results // .' > "$FILE"
done
```

Upload the resulting files to a folder under the labs-synthetic parent, copy
the folder ID, select "Use existing folder ID" in the create form, and paste it in.

## Updating fixtures

Edit the JSON files in Drive, then click "Reload fixtures" on that opp's row
in `/labs/synthetic/` so the in-process fixture cache picks up the changes.

## Limitations

- Image endpoints (`/export/opportunity/<id>/image/`) still hit prod. Image
  IDs in synthetic data will typically 404 and render as broken images in
  audit/KMC/RUTF views. Not a blocker for the dashboards you're likely demoing.
- Pagination, `last_id` cursors, and `?images=true`-style filters are ignored —
  fixtures are returned whole in one page.
- No writes are intercepted. If a reviewer flags a synthetic visit, that
  `LabsRecord` goes to prod. Delete the demo opp's records from prod when done.
