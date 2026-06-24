# Verified Monitoring (N1) — synthetic walkthrough recipe

This folder versions the **Verified Monitoring** dashboard demo: the curated
state config and the seeder that stands it up on a labs synthetic opp. It is
the durable home for the recipe — **do not seed from ad-hoc `/tmp` scripts.**

The demo is narrative **N1** of the rooftop-survey DDD ("the art of the
possible"): an independent, funder-facing dashboard showing verified vitamin-A
coverage in a treatment ward (Kaura) vs an adjacent match ward (Gedawa) over
six bi-monthly survey rounds, with a service-delivery + survey map overlay.

## What it produces

A single **workflow run** on synthetic opp `10008` (workflow def `3699`) whose
`instance.state` carries the entire dashboard payload:

- six bi-monthly coverage rounds per ward (R6 = the hero numbers: 68.1% vs 8.9%),
- the verification strip (GPS / evidence / back-check / anomaly flags),
- the self-report-vs-independent premium (88.0% claimed → 68.1% verified = 19.9 pp),
- service-delivery counts (Kaura 2,300 · Gedawa 0), and
- the **two-ward map overlay GeoJSON**: ward boundaries, a deterministic sample
  of service-delivery points inside Kaura only, and independent survey pins in
  **both** wards (purple = vitamin-A confirmed, pink = absent).

The render — `commcare_connect/workflow/templates/verified_monitoring_render.js`
— reads this state and never fetches. It is "show-don't-tell": results are
presented neutrally, with no causal claims, so the viewer draws the conclusion.

## Why a synthetic opp (no prod, no permissions)

Opp `10008` is a labs-only `SyntheticOpportunity` (`id ≥ 10_000`). The
`connect_labs` MCP `workflow_create_run` call routes **in-process** to
`labs/synthetic/local_records_backend.py` — plain Django ORM CRUD in the labs
DB. There is no production data, no HTTP round-trip to `connect.dimagi.com`, and
no permission check. See the root `CLAUDE.md` § "Synthetic / labs-only
opportunities".

## How to run

```bash
# From a connect-labs checkout, with the labs venv active.
# Needs an MCP token (see docs/MCP_SETUP.md or run /labs-token-setup):
export LABS_MCP_TOKEN=...          # or it is read from ~/.claude/mcp.json
python scripts/walkthroughs/verified-monitoring/regenerate.py
```

This writes `.run_ids.json` (git-ignored) with the new `run_id` and the full
`runner_url`. Open that URL to view the dashboard. The seed is deterministic
(`rng_seed` in `demo_config.json`), so the map geometry is identical run to run.

## Files

| File               | Purpose                                                                                                                        |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| `demo_config.json` | All the curated numbers + ward polygon coords + sample sizes + RNG seed. Edit this to change the demo.                         |
| `regenerate.py`    | Builds the full state payload (incl. overlay GeoJSON) from the config and creates the workflow run via the `connect_labs` MCP. |
| `.run_ids.json`    | Generated. The latest `run_id` + `runner_url`. Git-ignored.                                                                    |

## Editing the demo

- **Change a number** (coverage, verification, premium): edit `demo_config.json`
  and re-run. The render picks it up from the new run's state.
- **Change the map** (ward shape, point density): edit `ward_polygons` /
  `service_delivery_sample` / `survey_pins` and re-run.
- **Change the dashboard layout/visuals**: edit the render
  (`verified_monitoring_render.js`) and push it live with
  `workflow_update_render_code` (no redeploy) — that is independent of this
  seeder.
