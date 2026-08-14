## Product Description

Refreshes the podcast episodes on the Connect **Insights** page to match the
ones tagged **Connect** on dimagi.com/podcast. No manual editing.

## Technical Summary

Monthly run of `automation/sync_connect_podcasts.py` — it scans
dimagi.com/podcast for `data-product="…connect…"` and rewrites the cards between
the `CONNECT-PODCASTS` markers in `home.html`.

## Safety Assurance

Only the marked podcast block changes. New episodes get an auto-filled excerpt,
so **please skim the copy before merging.** This monthly-PR flow is just a
starting point — if the team has a better way to review new podcasts, change it.
