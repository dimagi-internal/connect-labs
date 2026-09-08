Profile the 11 production KMC opportunities so the synthetic clone can reproduce
their real `over_limit` rate.

## Why this needs running by a human

`over_limit` is approved-equivalent work that a budget-cap accounting glitch
mislabels. Connect's metrics spec defines valid data as
`status IN ('approved','over_limit')`, and excluding it undercounts visits,
started cases and weight series by 40-130% depending on the programme.

The profiler learned to measure it on 2026-09-05 (#1444). Every KMC profile
bundle on Drive predates that — the newest is 2026-08-24 — so every bundle we
generate from carries no `over_limit_rate`, it defaults to 0.0, and every clone
collapses `over_limit` into `approved`. For scale: the measured platform
baseline is 14.3% of all visits. We are generating 0%.

Re-profiling fixes it, but it reads production, and the ACE agent's labs PAT
cannot see these opportunities (they are outside its orgs — which is correct).
So it is run here, once, under your own credentials.

## What to do

Call `synthetic_clone_profile` with the cohort spec at
`connect_labs/labs/synthetic/cohorts/kmc.yaml` (read it first; do not retype the
opportunity ids from memory).

That is Phase 1 only. It reads the real opportunities server-side and writes
aggregate bundles to a fresh Google Drive run folder. Do NOT run Phase 2
(`synthetic_clone_generate`) — generation is offline, needs no production
access, and will be run separately.

## What to report back

1. The resolved `bundle_root` (`gdrive:<folder_id>`). This is the handoff — it
   is what the offline phase and the follow-up analysis both consume.
2. Per source opportunity, the `over_limit_rate` the profiler measured.
3. Anything that looks off: an opportunity that failed to profile, a rate of
   exactly 0.0 across every opp (which would suggest the deployed code is still
   the pre-#1444 profiler rather than a real absence of over_limit), or a rate
   far from the 14.3% platform baseline.

## What NOT to do

Do not paste beneficiary-level rows, names, phone numbers, GPS coordinates or
form values into your reply. The bundles are aggregate by construction —
distributions, histograms, a correlation matrix, program config — and that is
the only level anything downstream needs. Report rates and counts, not records.
