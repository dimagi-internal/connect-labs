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
docker pull --platform linux/amd64 ghcr.io/emod-hub/emod-ubuntu-runtime@sha256:91933ca254ac9c0dd49deb6ba9b48c59b312c2baae2970e547b6a6f5b896fbbd   # the digest the grid ran on
cp /path/to/connect-labs/tools/pmc_emod/pmc_sweep.py tutorials/
cd tutorials   # pmc_sweep.py imports the tutorials' manifest.py
PMC_POP=5000 PMC_SEEDS=0,1,2,3,4,5 python pmc_sweep.py
```

The larval capacity defaults to 6e7 (`PMC_LARVAL`), which gives PfPR 2-5y of about 44%: the setting the
committed grid was run with. That setting is recorded in `setting.model_inputs` of `pmc_emod_sweep.json`, and
the live model (`emod/runner.py` `DEFAULT_SETTING`) must equal it, so a live run reuses the grid's burn-in and
compares with its rows; `test_emod_runner.py` fails if the two drift. If you re-run the grid in another setting,
update `model_inputs` and `DEFAULT_SETTING` in the same commit (the worker then builds a new burn-in on the
first live request, which makes that request slow). Pass a comma-separated list of scenario codes as
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
for the request/result shapes and `worker/bootstrap.sh` for the instance user-data. Each run reports under-5
outcomes (`cases_u5`, `kids_u5`) alongside the 3-24-month ones, and a schedule may give `"drug": "SPAQ"`
(SMC: the same SP drug entry plus a 3-day amodiaquine course) instead of the default SP.

A request with `"mode": "calibrate"` and a `target_pfpr` fits the setting's `larval_capacity` instead: 8
log-spaced burn-ins over 1e6-1e9 run side by side, then 4 around the interpolated crossing. The nearest is
returned with `fit` `ok` (within 0.03 of the target), `unreachable` or `loose`, and its burn-in is cached under
its setting hash, so a run request with that value starts warm. Burn-ins get slower as larval capacity rises
(about 7x at 1e10 against 6e7), which is why the grid stops at 1e9, where PfPR 2-5y has already plateaued.

Tests run locally under Docker (they skip when Docker is absent):

```bash
EMOD_TUTORIALS_DIR=/path/to/emodpy-malaria/tutorials /path/to/emodpy-venv/bin/python -m pytest tools/pmc_emod/worker -v
```

The worker bounds a whole request with `EMOD_REQUEST_TIMEOUT_S` (default 2100 s, burn-in plus pick-ups); labs
passes it explicitly and gives the SSM command 2400 s, so the worker's own timeout message is what gets
recorded. A change to `worker/run_scenarios.py` or `pmc_sweep.py` reaches the box only once both are re-staged
under the bucket's `worker/` prefix (bootstrap fetches them from `WORKER_SRC`).

## Live runs on labs: concurrency

Labs runs each live request as the Celery task `run_pmc_model` (`connect_labs/labs/indicators/emod/tasks.py`)
on the shared worker pool: one default queue, concurrency 6 (`docker/start_celery`). There is no dedicated
queue, because the deployed worker (`deploy/task-definitions/worker.json`) consumes only the default one and a
second worker would be new infrastructure. Instead:

- The instance runs one request at a time, serialised by a Redis lease (TTL 300 s, refreshed every 60 s).
- A task waiting for the lease polls without blocking every 5 s, for at most 900 s: cheap in CPU, but it holds
  a pool slot while it waits.
- At most `PMC_MAX_IN_FLIGHT` (3) runs are queued or running; a fourth request gets "busy" (429). So EMOD
  holds at most three of the six slots (one running, bounded by the worker's 2100 s request deadline plus instance start-up; two waiting), and the
  rest of labs keeps at least three.
- A run killed mid-flight (deploy, OOM) stops touching its row; after three missed 60 s heartbeats the next
  request for it re-queues it, and the new task takes over once the dead worker's lease expires (at most
  five minutes).
