# OES demo environment

Seeds the environment shown on the Operation End Starvation call: the
program team's CHC, RUTF and chlorine supply chains, the four partner
organisations reachable through their own links, and a second organisation
that uses supply without Connect's verified delivery.

Design: `docs/superpowers/specs/2026-09-24-oes-demo-environment-design.md`.

## The data is not in this repository

Partner names, volumes and prices live in a Drive document read at seed time.
Set `LABS_SYNTHETIC_GDRIVE_SA_KEY` and pass the folder id:

    python scripts/walkthroughs/oes-demo/ensure_demo.py --drive-folder <folder-id>

## Programs: three chains, three of them, and why

`SupplyDataAccess.scope_key` is "the program, always", and it governs the
**catalogue** as well as the ledger -- commodities, items and suppliers are
per-program. CHC, RUTF and chlorine are three different real things, so they
get three programs. One scope holding all three would put chlorine in the CHC
catalogue and RUTF's supplier register in with ORS: a program on screen that
corresponds to nothing real. Design section 1a.

| Program | Scope         | Document section   | Behind it in Connect                            |
| ------- | ------------- | ------------------ | ----------------------------------------------- |
| 10610   | `chc`         | `chc_chain`        | program 217, org `dimagi-chc-rct`               |
| 10672   | `rutf`        | `rutf_rounds`      | program 263, org `dimagi-ng-rutf`               |
| 10673   | `chlorine`    | `chlorine_blocked` | **nothing -- it is not an opportunity yet**     |
| 10671   | `supply_only` | `supply_only`      | nothing: no opportunity binding, no user points |

The first three scope names are the ones the document's
`portfolio.program_slugs` uses, so the portfolio resolves through `SCOPES`
rather than through a second map that could disagree with it. The supply-only
organisation is a different organisation's program and is deliberately not in
that portfolio.

All four ids confirmed free two ways before use: the `connect_labs` MCP
`synthetic_env_list()` tool (none appears in any registered synthetic
environment) and `grep -rhoE "PROGRAM[A-Z_]* *= *10[0-9]{3}"
connect_labs/supply_chain/tests/*.py`, because the supply test suite holds raw
program ids that no registry knows about -- `10611`, which an earlier draft
used, is one of them.

## Each scope gets only its own products

`seed_scopes` seeds the organisations once -- `upsert_org` is the one
reference write here that is not program-scoped, because "an organisation is
the same organisation in every program it appears in" -- and then seeds each
scope's **catalogue separately**.

Which products a scope gets is derived from its own section by
`commodities_for`, not listed per scope in the document, so the split cannot
drift from the chain it describes: a round that gains a line gains its product
in the same edit. A kit brings its components with it, because
`_kit_components` refuses a component that is not a product in the same
catalogue -- a co-pack seeded without its ORS sachet is one whose
specification can never be checked, and "no requirement to fail" reads as a
pass.

`commodity_slugs` is a **required keyword argument** on both `seed_reference`
and `seed_catalogue`, and `None` is refused rather than read as "all of them".
There is no default because a default made the wrong call the shortest one:
seeding a single scope without saying which products puts every chain's
products in one program's pickers. Seeding one scope by hand is still fine --
it takes one call to `commodities_for`.

`seed_scopes` seeds reference data only. The CHC chain is seeded against the
`chc` scope by `seed_chc_chain`; RUTF and chlorine have their own sections and
their own tasks, so until those land their scopes hold a catalogue and no
chain. That is the right intermediate state, not an omission.

## Organisations

Organisations are upserted by slug, one row per partner, and carry
`connect_organization_id` where the partner's Connect organisation is known
-- today, only the program's own org (`dimagi-chc-rct`). The four
implementing/distributing partners are not bound: Connect's export only
returns organisations the polling account belongs to, and these partners are
not among them.

`connect_organization_id` is the organisation's identity and does not change
once set (`connect_labs/labs/models.py`), so `org_upsert` refuses an explicit
`null` for it. An org with no known Connect id therefore omits the key
entirely rather than sending it as `null` -- the seed carries the partner's
`connect_organization_slug` instead, which is the designed route for
"organisation known, numeric id not yet": it is a plain finding-aid field
that only matches a row with no id yet, and is passed through independent of
whether an id is also known.

## Partner links, and the third kind of truth

`seed_partner_links` mints one login-free update link per partner named in
the document's `partner_links`, and then records that document's
`chc_chain.partner_entered` rows **through the distributor's link** -- by
POSTing to the public page, the way a partner records anything.

That is not ceremony. Tiers 1 and 2 differ only in `source` and are both
recorded by us, so the seeder can stamp them. Tier 3 differs in
`recorded_by_org`, which `update_links/service.py` derives from the link a
submission came through and from nothing the caller sends. A row written any
other way would carry our organisation and read as ours -- the substitution
section 5a of the design exists to make impossible. The page answers a
refusal with a 200 and form errors rather than an exception, so the seeder
raises on anything but the redirect: a silent no-op here would seed two of
the three tiers and look like it had seeded all three. The errors it raises
with are read out of the rendered page, not out of the test client's
`response.context` -- that is filled from a signal only
`setup_test_environment()` installs, so it is empty in a `manage.py shell` and
the explanation would have gone missing in the one place it is needed.

The tokens are returned to the operator and never written anywhere. They are
shown once; if one is lost, revoke it and issue another.

**Coverage is not a free choice.** A link that covers _everything involving
the organisation_ resolves its stores as the ones that organisation runs, so
it cannot name a collecting partner's store -- and a distributor releasing
stock into one therefore needs a link that _lists_ what it covers, which is
what `update_links/forms.py` tells an issuer ("include the collecting
partner's store if they hand stock over"). The document says which kind each
link is; the seeder works out which order and stores a listed one names from
the chain it was minted for, and refuses rather than widening a link that
cannot reach its own rows.

## The three readings, on one order

After a seed, the order page carries all three and they must not read alike:

| Row            | Reads                                           |
| -------------- | ----------------------------------------------- |
| the order      | the program's name alone -- we placed it        |
| goods received | "⟨program⟩, for ⟨distributor⟩ (they told us)"   |
| the dispatch   | the distributor's name alone -- they entered it |

`test_oes_demo_provenance.py` renders that page and asserts the three are
present and distinct. If they collapse into one reading the seed has failed
even though it ran.
