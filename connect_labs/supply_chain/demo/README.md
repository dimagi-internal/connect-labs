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
