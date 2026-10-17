# Organisations that run their own procurement (#2458)

`manage.py supply_seed_org_procurement {oes,llo} [--local-admin]` registers a
labs-only programme, **declares the organisation that runs it** before anything
else is written, and seeds its sourcing and orders through the ordinary
operations. Dimagi plays no part in either; its people see them read-only, and
platform support (Django staff) can act for them. `org_procurement.py` says why
each was chosen:

- **`oes`**: Operation End Starvation as a _pooled buyer_. It tenders RUTF to
  producers, compares landed cost, awards and orders as buyer of record, and
  delivers into the store its implementers draw from. This is the
  `clone_upstream` chain, bought by its running organisation.
- **`llo`**: an invented Kenyan community-health LLO, Kilele, buying its own
  chlorine tablets and ORS/zinc for a safe-water opportunity from local
  distributors. It is an implementer rather than a funder, buys small rather
  than pooled, from local distributors rather than international producers,
  in a different country and basket.

Each organisation is a labs-only row under a fixed key the seeder owns
(`labs-synthetic-demo-oes`, `labs-synthetic-demo-llo`). The seeder refuses an
opportunity with the same label that is filed under any other organisation.
Visibility is the organisation's own `.example` domain plus Dimagi's.

On labs, run it without `--local-admin`. The organisation's real admin is then
added by platform support (an `OrgMembership` granted for the organisation),
because a running organisation takes no invitations. Locally, `--local-admin`
also makes `dev-oes-admin` / `dev-kilele-admin` (a `.example` address with no
usable password; DEBUG only). `/supply/dev-login/?as=dev-oes-admin` signs in as
that admin, so you can walk Supply as someone who is not Dimagi.

# A phone for every adult (`supply_seed_phones`)

`manage.py supply_seed_phones [--reset]` registers a labs-only programme run by
an invented Kenyan LLO, Tumaini, that buys a smartphone for each of the 1,200
adults in three villages and hands each one over at a Connect visit. The LLO
runs its own procurement (#2458): a tender to three local distributors (two
quote, one is reminded and stays silent), an award to the quote delivered to
the office with the specification stated, an order paid half in advance, a
delivery received with six phones rejected at inspection, and an invoice that
still bills all 1,200. Six field workers, two per village, are issued phones in
two rounds; each handover visit (an invented `Phone handover` form) states one
phone given, the adult's village and age, and the handset's IMEI, and the real
visit reader takes it off that worker's stock week by week. One worker's app
counts one phone fewer than the ledger says they hold. Majengo's last six adults
wait for the replacements of the rejected phones.

Where to look: Overview, the tender, the order (receipt and invoice), Map,
Workers, Where it went. Walkthrough: `docs/walkthroughs/supply-phones-every-adult.yaml`.

# The RUTF clone that supply walkthroughs film

Supply walkthroughs (`docs/walkthroughs/supply-sophie-*`) film **program 10112 /
opportunity 10113**, a synthetic clone of the real RUTF opportunity 2230 made
2026-10-07 with GPS (#2296: each worker's area moved 3-8 km and jittered), so its
workers and stores appear on Supply → Map (#2293, #2295) and "Where it went"
(`/supply/flow/`, #2294) follows the RUTF out to the visits (#2298).

- The older clone, program 10110 / opportunity 10111, has no GPS and draws nothing
  on the map. 10110 also holds the nutrition demo's opportunities, so leave it alone.
- Its supply chain is seeded by `manage.py supply_seed_clone_supply` (#2254) and
  topped up every Monday by beat (#2270).
- **How much RUTF each worker _received_ is invented** (every Distribution's note
  says so). What each visit _gave out_ is the clone's. Narration must never present
  the deliveries as recorded.
- The walkthrough persona's session is scoped to its own program. A scene on
  another program needs that program to be reachable for the persona.
- **A labs deploy restarts the Celery worker and kills synthetic clone jobs in
  flight** (since #2265 the job reads "Labs restarted ... start it again"). Before
  deploying, check `/labs/synthetic/jobs/`.
