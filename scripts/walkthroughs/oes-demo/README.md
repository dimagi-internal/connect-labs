# OES demo environment

Seeds the environment shown on the Operation End Starvation call: the
programme team's CHC and RUTF supply chains, the four partner organisations
reachable through their own links, and a second organisation that uses supply
without Connect's verified delivery.

Design: `docs/superpowers/specs/2026-09-24-oes-demo-environment-design.md`.

## The data is not in this repository

Partner names, volumes and prices live in a Drive document read at seed time.
Set `LABS_SYNTHETIC_GDRIVE_SA_KEY` and pass the folder id:

    python scripts/walkthroughs/oes-demo/ensure_demo.py --drive-folder <folder-id>

## Programmes

| Programme | What                                                                      |
| --------- | ------------------------------------------------------------------------- |
| 10610     | The programme team's own: CHC and RUTF, with verified delivery            |
| 10671     | The supply-only organisation: no opportunity binding, no user-held points |

Confirmed free via the `connect_labs` MCP `synthetic_env_list()` tool before
use (neither id appears in any registered synthetic environment).

## Organisations

Organisations are upserted by slug, one row per partner, and carry
`connect_organization_id` where the partner's Connect organisation is known
-- today, only the programme's own org (`dimagi-chc-rct`). The four
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
| the order      | the programme's name alone -- we placed it      |
| goods received | "⟨programme⟩, for ⟨distributor⟩ (they told us)" |
| the dispatch   | the distributor's name alone -- they entered it |

`test_oes_demo_provenance.py` renders that page and asserts the three are
present and distinct. If they collapse into one reading the seed has failed
even though it ran.
