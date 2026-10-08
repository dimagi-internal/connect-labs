# Task 3 report: design grid

Group per intervention year (offset + 365*y), reps per group; a group is cut to rounds starting before day 730 (the sim end). Month offsets duplicate live.month_offset, pinned by a drift test. Verbatim designs_for output (offsets are day-of-year; Jun = 152):

## Ondo: onset_month=6, rain_wettest_quarter=41.5
```
{"code": "pmc_m4_onset", "label": "4 monthly rounds, Jun\u2013Sep, 3\u201324 months", "kind": "pmc", "rounds": [[152, 30, 4, 0.25, 2.0, 0.85], [517, 30, 4, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_m6_onset", "label": "6 monthly rounds, Jun\u2013Nov, 3\u201324 months", "kind": "pmc", "rounds": [[152, 30, 6, 0.25, 2.0, 0.85], [517, 30, 6, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_m8_onset", "label": "8 monthly rounds, Jun\u2013Jan, 3\u201324 months", "kind": "pmc", "rounds": [[152, 30, 8, 0.25, 2.0, 0.85], [517, 30, 8, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_m12", "label": "Year-round monthly, 3\u201324 months", "kind": "pmc", "rounds": [[0, 30, 12, 0.25, 2.0, 0.85], [365, 30, 12, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_q4", "label": "Quarterly from Jun, 3\u201324 months", "kind": "pmc", "rounds": [[152, 91, 4, 0.25, 2.0, 0.85], [517, 91, 3, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_b6", "label": "Every two months from Jun, 3\u201324 months", "kind": "pmc", "rounds": [[152, 61, 6, 0.25, 2.0, 0.85], [517, 61, 4, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
```

## Kano: onset_month=6, rain_wettest_quarter=77.2
```
{"code": "pmc_m4_onset", "label": "4 monthly rounds, Jun\u2013Sep, 3\u201324 months", "kind": "pmc", "rounds": [[152, 30, 4, 0.25, 2.0, 0.85], [517, 30, 4, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_m6_onset", "label": "6 monthly rounds, Jun\u2013Nov, 3\u201324 months", "kind": "pmc", "rounds": [[152, 30, 6, 0.25, 2.0, 0.85], [517, 30, 6, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_m8_onset", "label": "8 monthly rounds, Jun\u2013Jan, 3\u201324 months", "kind": "pmc", "rounds": [[152, 30, 8, 0.25, 2.0, 0.85], [517, 30, 8, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_m12", "label": "Year-round monthly, 3\u201324 months", "kind": "pmc", "rounds": [[0, 30, 12, 0.25, 2.0, 0.85], [365, 30, 12, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_q4", "label": "Quarterly from Jun, 3\u201324 months", "kind": "pmc", "rounds": [[152, 91, 4, 0.25, 2.0, 0.85], [517, 91, 3, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_b6", "label": "Every two months from Jun, 3\u201324 months", "kind": "pmc", "rounds": [[152, 61, 6, 0.25, 2.0, 0.85], [517, 61, 4, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "smc_m4_onset", "label": "SMC: 4 monthly rounds, Jun\u2013Sep, 3\u201359 months", "kind": "smc", "rounds": [[152, 30, 4, 0.25, 4.916666666666667, 0.85], [517, 30, 4, 0.25, 4.916666666666667, 0.85]], "target_pop_fraction": 0.9333333333333333}
```

Tests: 9 passed (pytest from tools/pmc_emod/states and from repo root; worker validate_request and live.month_offset both loaded, none skipped).

## Fix round 1

Periodic schedule (rounds >= day 365 wrap to t-365; each year gets a tail group and a head group, identical across years), per-design drug (SP/SPAQ), SMC label with (SPAQ), hard KeyError on missing rain_wettest_quarter, label-drift note in the docstring. 10 tests pass. Ondo/Kano both start in Jun, where 8 rounds end at day 362 so nothing wraps; e.g. Jul 8 rounds = [27,30,1] + [182,30,7] each year.

### Ondo
```
{"code": "pmc_m4_onset", "label": "4 monthly rounds, Jun\u2013Sep, 3\u201324 months", "kind": "pmc", "drug": "SP", "rounds": [[152, 30, 4, 0.25, 2.0, 0.85], [517, 30, 4, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_m6_onset", "label": "6 monthly rounds, Jun\u2013Nov, 3\u201324 months", "kind": "pmc", "drug": "SP", "rounds": [[152, 30, 6, 0.25, 2.0, 0.85], [517, 30, 6, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_m8_onset", "label": "8 monthly rounds, Jun\u2013Jan, 3\u201324 months", "kind": "pmc", "drug": "SP", "rounds": [[152, 30, 8, 0.25, 2.0, 0.85], [517, 30, 8, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_m12", "label": "Year-round monthly, 3\u201324 months", "kind": "pmc", "drug": "SP", "rounds": [[0, 30, 12, 0.25, 2.0, 0.85], [365, 30, 12, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_q4", "label": "Quarterly from Jun, 3\u201324 months", "kind": "pmc", "drug": "SP", "rounds": [[60, 91, 1, 0.25, 2.0, 0.85], [152, 91, 3, 0.25, 2.0, 0.85], [425, 91, 1, 0.25, 2.0, 0.85], [517, 91, 3, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_b6", "label": "Every two months from Jun, 3\u201324 months", "kind": "pmc", "drug": "SP", "rounds": [[31, 61, 2, 0.25, 2.0, 0.85], [152, 61, 4, 0.25, 2.0, 0.85], [396, 61, 2, 0.25, 2.0, 0.85], [517, 61, 4, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
```

### Kano
```
{"code": "pmc_m4_onset", "label": "4 monthly rounds, Jun\u2013Sep, 3\u201324 months", "kind": "pmc", "drug": "SP", "rounds": [[152, 30, 4, 0.25, 2.0, 0.85], [517, 30, 4, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_m6_onset", "label": "6 monthly rounds, Jun\u2013Nov, 3\u201324 months", "kind": "pmc", "drug": "SP", "rounds": [[152, 30, 6, 0.25, 2.0, 0.85], [517, 30, 6, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_m8_onset", "label": "8 monthly rounds, Jun\u2013Jan, 3\u201324 months", "kind": "pmc", "drug": "SP", "rounds": [[152, 30, 8, 0.25, 2.0, 0.85], [517, 30, 8, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_m12", "label": "Year-round monthly, 3\u201324 months", "kind": "pmc", "drug": "SP", "rounds": [[0, 30, 12, 0.25, 2.0, 0.85], [365, 30, 12, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_q4", "label": "Quarterly from Jun, 3\u201324 months", "kind": "pmc", "drug": "SP", "rounds": [[60, 91, 1, 0.25, 2.0, 0.85], [152, 91, 3, 0.25, 2.0, 0.85], [425, 91, 1, 0.25, 2.0, 0.85], [517, 91, 3, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "pmc_b6", "label": "Every two months from Jun, 3\u201324 months", "kind": "pmc", "drug": "SP", "rounds": [[31, 61, 2, 0.25, 2.0, 0.85], [152, 61, 4, 0.25, 2.0, 0.85], [396, 61, 2, 0.25, 2.0, 0.85], [517, 61, 4, 0.25, 2.0, 0.85]], "target_pop_fraction": 0.35}
{"code": "smc_m4_onset", "label": "SMC (SPAQ): 4 monthly rounds, Jun\u2013Sep, 3\u201359 months", "kind": "smc", "drug": "SPAQ", "rounds": [[152, 30, 4, 0.25, 4.916666666666667, 0.85], [517, 30, 4, 0.25, 4.916666666666667, 0.85]], "target_pop_fraction": 0.9333333333333333}
```
