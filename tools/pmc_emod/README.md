# PMC schedule sweep on IDM's EMOD

Produces `connect_labs/labs/indicators/data/pmc_emod_sweep.json`, the model results behind the
PMC schedule explorer (`/labs/targeting/pmc/`). EMOD does not run on labs: a sweep takes about
twenty minutes in Docker, so it is run here and its summary is committed.

## What it compares

Six ways of delivering perennial malaria chemoprevention (SP) to young children, in one
southern-Nigeria-like setting (year-round transmission, long-rains peak April to October,
background case management and bed nets held equal):

| code                             | schedule                                                                        |
| -------------------------------- | ------------------------------------------------------------------------------- |
| `none`                           | no PMC                                                                          |
| `epi_linked`                     | SP at the 10-week, 14-week, 9-month and 15-month vaccine contacts, 25% coverage |
| `connect_quarterly_3_24`         | a Connect visit every 91 days, 3-24 months, 85% coverage                        |
| `connect_bimonthly_3_24`         | every 61 days, 3-24 months                                                      |
| `connect_monthly_in_season_3_24` | six monthly rounds from about 1 April, 3-24 months                              |
| `connect_quarterly_12_24`        | quarterly, 12-24 months only                                                    |

Outcome: clinical cases in children 3-24 months over two intervention years after a two-year
burn-in, and SP doses given.

**The setting is not calibrated to any state.** Making it so (fitting larval capacity and the
seasonal habitat curve to a state's DHS prevalence and CHIRPS rainfall) is the next step before
any figure is quoted as a state estimate.

## Running it

Needs Docker (Docker Desktop running) and Python 3. The script installs the EMOD binary and schema the
tutorials' manifest points at (`emod_malaria.bootstrap`) on first run.

```bash
git clone --depth 1 https://github.com/EMOD-Hub/emodpy-malaria.git
cd emodpy-malaria && python3 -m venv .venv && . .venv/bin/activate && pip install -e .
docker pull --platform linux/amd64 ghcr.io/emod-hub/emod-ubuntu-runtime:latest
cp /path/to/connect-labs/tools/pmc_emod/pmc_sweep.py tutorials/
cd tutorials   # pmc_sweep.py imports the tutorials' manifest.py
PMC_POP=5000 PMC_SEEDS=0,1,2,3,4,5 PMC_LARVAL=6e7 python pmc_sweep.py
```

`PMC_LARVAL=6e7` gives PfPR 2-5y of about 44%. Pass a comma-separated list of scenario codes as
the first argument to run only those. Results land in `tutorials/pmc_results/summary.json`, one
row per simulation (scenario, seed, cases, children 3-24 months, PfPR 2-5y, doses).

## Updating the page

Average each scenario's six seeds into the `schedules` entries of `pmc_emod_sweep.json`:
`averted_pct` against `none`, `averted_ci` as the 95% range across seeds, and mean `doses`. Update
`setting.baseline_cases`, `setting.children_3_24m`, `setting.pfpr_2_5y` and `run_date`.
`connect_labs/labs/indicators/tests/test_pmc.py` pins the reported cost per case averted, so
update those figures in the same commit.

## Live worker (`worker/`)

`worker/run_scenarios.py` is the on-box runner behind the live model: one request (a setting, schedules,
seeds) in, one result out, with the 2-year burn-in serialized once per setting and reused. See its docstring
for the request/result shapes and `worker/bootstrap.sh` for the instance user-data. Tests run locally under
Docker (they skip when Docker is absent):

```bash
EMOD_TUTORIALS_DIR=/path/to/emodpy-malaria/tutorials /path/to/emodpy-venv/bin/python -m pytest tools/pmc_emod/worker -v
```
