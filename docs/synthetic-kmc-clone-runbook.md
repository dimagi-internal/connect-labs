# KMC Synthetic Clone Runbook

Clone the 11 production KMC opportunities into a single labs-only "KMC (Synthetic)" program
using the two-phase profile/generate workflow.

---

## KMC opportunity IDs

```
523  524  675  874  938  1234  1236  1487  1488  1739  1790
```

---

## Phase 1 — Profile (safe mode, prod-touching)

Reads real exports from production and writes **aggregate statistics only** — no
row-level beneficiary data persists to disk. Requires a valid Connect OAuth token
with access to each opportunity.

### Via MCP tool (one opp at a time)

```
synthetic_profile_opp(source_opportunity_id=523, out_dir="/tmp/kmc-bundles")
```

Repeat for each of the 11 IDs, or use the bulk variant:

```
synthetic_profile_opps_bulk(
    source_opportunity_ids=[523,524,675,874,938,1234,1236,1487,1488,1739,1790],
    out_dir="/tmp/kmc-bundles"
)
```

### Via management command

```bash
export CONNECT_OAUTH_TOKEN="<your-export-scope-token>"

python manage.py synthetic_profile_opps \
    --opps 523,524,675,874,938,1234,1236,1487,1488,1739,1790 \
    --out /tmp/kmc-bundles \
    --base-url https://connect.dimagi.com
```

### Verify each bundle

Each of the 11 subdirectories (`/tmp/kmc-bundles/<opp_id>/`) must contain:

| File | Contents |
|------|----------|
| `manifest.yaml` | `opportunity_name`, `n_users`, `n_visits`, `fields[]` with `categorical`/`null_rate`, `correlation` matrix, `temporal` (hour-of-day + weekday histograms + weekly volume multipliers), `flag_reasons` distribution |
| `app_structure.json` | Deliver-app form schema from the live HQ app |
| `opportunity.json` | Scrubbed program config (name, currency, dates, budget — no beneficiary data) |

Quick check:

```bash
for id in 523 524 675 874 938 1234 1236 1487 1488 1739 1790; do
    echo -n "opp $id: "
    ls /tmp/kmc-bundles/$id/
done
```

---

## Phase 2 — Generate (full/unsafe mode, offline — no prod calls)

Reads the 11 bundles from disk, generates synthetic fixture data using the copula
engine, uploads to GDrive, and registers all 11 as labs-only opportunities under
one shared "KMC (Synthetic)" program. **Zero production network calls occur.**

### Via MCP tool

```
synthetic_generate_opps_bulk(
    bundle_root="/tmp/kmc-bundles",
    program_name="KMC (Synthetic)",
    org_name="Dimagi-KMC (Synthetic)"
)
```

The tool allocates a single shared `program_id` for the cohort automatically.
Each opp gets a labs-only ID (`≥ 10_000`). Idempotent by default: passing
`fresh=True` forces regeneration even if a row already exists.

To generate a single opp (e.g. if one bundle failed in Phase 1):

```
synthetic_generate_opp(
    bundle_dir="/tmp/kmc-bundles/523",
    program_id=<shared_program_id>,
    program_name="KMC (Synthetic)",
    org_name="Dimagi-KMC (Synthetic)"
)
```

### Via management command

```bash
python manage.py synthetic_generate_opps \
    --bundles /tmp/kmc-bundles \
    --program "KMC (Synthetic)" \
    --org "Dimagi-KMC (Synthetic)"

# To force regeneration:
python manage.py synthetic_generate_opps \
    --bundles /tmp/kmc-bundles \
    --program "KMC (Synthetic)" \
    --org "Dimagi-KMC (Synthetic)" \
    --fresh
```

---

## Verification

### Labs picker

Open the labs synthetic UI or use the `synthetic_env_list` MCP tool. Confirm:

- One program named **"KMC (Synthetic)"** is visible.
- It contains exactly **11 opportunities** (one per source opp).
- Each opportunity shows `cloned_from_opportunity_id` set to its source ID.

### Spot-check an opp's export

```
synthetic_local_records_count(opportunity_id=<labs_opp_id>)
```

Then verify the fixture endpoints respond:

- `/export/<labs_opp_id>/app_structure/` — returns non-empty JSON
- `/export/<labs_opp_id>/user_visits/` — returns visit records

### Fidelity check

Run `synthetic_fidelity_report` on a couple of bundles **after** generation:

```
synthetic_fidelity_report(bundle_dir="/tmp/kmc-bundles/523")
synthetic_fidelity_report(bundle_dir="/tmp/kmc-bundles/1234")
```

A healthy report shows:

- **`correlation_frobenius`** — small (< 0.3 for well-correlated fields, near 0 for
  independent fields); large values indicate the copula sampler diverged from the target.
- **`marginal_deltas`** — per-field mean/std and TVD values close to 0; large TVD (> 0.1)
  on a categorical field indicates the frequency distribution shifted.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Phase 1 fails for one opp with "No user_visits" | Opp has no export data in prod | Skip that bundle; the rest succeed |
| Phase 2 `skipped=True` for all opps | Rows already exist and `fresh=False` | Pass `fresh=True` to regenerate |
| `app_structure_present=False` after generate | The opp's HQ app returned empty app_structure in Phase 1 | Re-run Phase 1 for that opp ID; check HQ app is published |
| High correlation Frobenius (> 0.5) | Very small visit count (< 50) means poor rank correlation estimates | Expected for small opps; not a bug |
| GDrive upload error | Service-account credentials not configured on the labs server | Check `GDRIVE_SERVICE_ACCOUNT_KEY` env var |
