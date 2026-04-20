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

1. Obtain the `LABS_SYNTHETIC_GDRIVE_SA_KEY` env var value from the team
   (service account JSON). Confirm it is set in the target environment.
2. Confirm the labs-synthetic parent folder has been shared with the
   service account email (Viewer is enough).

## Creating a synthetic opp

**Step 1 — dump real data from a similar opp on prod.** Using a dev OAuth
token with `export` scope:

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

Each file should end up being either a JSON list (`user_visits.json` etc.)
or a single JSON object (`opportunity.json`).

**Step 2 — edit to match the demo storyline.** Anonymize names, flip visit
statuses, change dates to create the demo you want.

**Step 3 — upload to Drive.** Create a folder under the labs-synthetic
parent (e.g. `opp-999-baobab-demo/`), upload all five files, and copy the
folder ID from the URL.

**Step 4 — register in Labs.** Go to `/labs/synthetic/`, click
"+ New synthetic opp", enter the Connect opp ID, paste the folder ID, and
click "Test access" to verify the service account can see your files. Save.

**Step 5 — use it.** Any labs visualization that loads export data for that
opp ID now sees your fixture data. The registry cache takes up to 60s to pick
up new registrations across workers; click "Refresh registry cache" if you
can't wait.

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
