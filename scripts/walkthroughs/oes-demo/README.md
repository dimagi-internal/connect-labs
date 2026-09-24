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
