# Supply: the organisation that runs a programme — design

**Status:** shipped (#2462, #2491; deployed 2026-10-13), amended after adversarial review (§8 supersedes §1–§3 where they differ). §8's follow-ups are closed out in §9.
**Date:** 2026-10-12. **Issue:** #2458.
**Reads against:** `connect_labs/supply_chain/identity.py` (who is writing),
`connect_labs/labs/access/scopes.py` (who may use a scope),
`2026-09-25-supplier-marketplace-design.md` §9 (an organisation's own tender listing, #2041).

## 0. What is wrong

The product owner wants every organisation to have the procurement toolset
Dimagi has, with Dimagi as one organisation using it. Re-checked against
`origin/main` 930fb36ed:

- **Access is already generic.** `SupplyDataAccess.__init__` → `may_use(caller,
  program_id=…)`: the programme must be one the caller holds. For a real Connect
  programme that means being a member of the organisation that owns it
  (Connect's `opp_org_program_list`: `Program.objects.filter(organization__in=
  user's orgs)`). For a labs-only programme it means `SyntheticOpportunity
  .is_visible_to`. No check names Dimagi.
- **Standing is not.** Everything that turns on "is this caller the programme
  itself?" reads `org.slug == "dimagi"`, in one module:

  | `identity.py` | effect |
  |---|---|
  | `source_for` | only Dimagi's rows default to `we_recorded`; everyone else's are `partner_reported` |
  | `resolve_org` | a Dimagi email address acts for Dimagi in **every** programme |
  | `stamp_provenance` (`is_programme`) | only Dimagi may attribute a row to another organisation, or claim `we_recorded` / `document` |
  | `acting_org_name` | drafted emails from Dimagi staff are signed "Dimagi" |

  And downstream of `resolve_org`: the overview heading, the banner's "buyer of
  record" fallback, `standing_rows(own_org_id=…)` (whose moves are "ours"),
  the provenance forms' `initial="we_recorded"` (which an OES member would
  have refused on submit), and `demo/clone_upstream.py`'s `BUYER_SLUG = "dimagi"`.

So an OES member who runs their own programme can already reach every screen.
What they get is the standing of a **partner in their own programme**: their
receipts read as hearsay, they cannot record a document, cannot enter a
supplier's or partner's word on that party's behalf, and the page heading names
the wrong buyer.

A second, separate finding: **holding a programme is the only write check.**
For a real programme that is fine (holders are the owning org's members). For a
labs-only programme, holding is visibility, which is domain-based and
fail-open (`allowed_domains=[]` means anyone with the synthetic toggle). So a
member of organisation B who can see A's demo programme can create tenders,
awards and payments in it today. Generalising "the programme" without closing
this would make it a real cross-organisation hole the moment two
organisations run real procurement side by side.

## 1. The concept: a programme's running organisation

**Every programme has exactly one running organisation**: the `LabsOrg` whose
people run its supply chain — tender, compare, award, contract, order, pay,
receive. It is what `records.BUYER_OF_RECORD`'s `programme_org` already means
("the programme's own organisation"); it just had no row saying which one.

```
ProgrammeRunner(program_id unique, org → LabsOrg PROTECT, declared_by → User null,
                declared_at, note)
```

`running_org(program_id)`:

1. the declared `ProgrammeRunner`, if there is one;
2. otherwise **none**: only staff and the system act as the programme there
   (§8 H5 — an earlier draft defaulted to Dimagi, which the review showed
   fails open).

Not derived from anything a caller can influence. In particular **not** from a
labs-only programme's `SyntheticOpportunity.labs_org`: labs-only organisation
rows are a bucket per organisation *name* across every team that registers a
synthetic opp (CLAUDE.md, Settings), and registering one is open to any Dimagi
address, so deriving authority from it would let anyone who can register a
synthetic opp make themselves the buyer of someone else's programme. Not from
Connect's `program.organization` either, in this version: Dimagi's real
programmes are owned by several Connect organisations (`dimagi-ng-rutf`,
`dimagi-chc-rct`, …) while labs records Dimagi as one row, so deriving would
silently re-attribute Dimagi's own records. A real programme run by another
organisation is declared by staff when that organisation is onboarded — which is
a staff step anyway (its `LabsOrg` must exist). Connect derivation can come
later, behind the same function.

**Declaring is staff-only.** A new operation `programme_runner_set` (MCP
`supply_chain_programme_runner_set`) refuses anyone who is not Django staff or
the system caller — the same bar `org_access` uses for labs-only organisation
rows. A programme member may not reassign their own programme: changing the
runner is changing who is "us", which is exactly the authority being protected.
`programme_runner_get` is a read any holder may make. Every declaration is an
`OperationCall` like any write.

**Migration:** a data migration declares Dimagi for every programme that holds
supply rows today, so Dimagi's programmes stay Dimagi's. A programme created
later is declared when it is seeded or onboarded.

## 2. Three standings in a programme

`programme_standing(access) → (role, acting_org)`, computed once per
`SupplyDataAccess` (cached on it), replaces every `slug == "dimagi"`:

| role | who | acts for | may |
|---|---|---|---|
| `programme` | a **member of the running organisation** | the running org | everything; attribute a row to another organisation (recording a partner's or supplier's word on their behalf); record `we_recorded` / `document`; default source `we_recorded` |
| `operator` | Django **staff** (or superuser) who is not a member | the running org | the same as `programme` — platform support helping the programme — and the person is recorded on the `OperationCall` |
| `partner` | anyone else who holds the programme | their own organisation (`resolve_org`, as today) | read only (§3, §8 H1); they write through update links and the marketplace |

**Membership of the running organisation**, strictest first, never by visibility:

- an `OrgMembership` *granted for* that organisation (`granted_for_org_id ==
  org.pk` — a membership a merge carried across is not a grant, the rule
  `org_access.administers` and Settings already use);
- for an organisation Connect knows (`not labs_only`, with a
  `connect_organization_id`): the caller's Connect organisations include it
  (`caller_org_ids`). A labs-only slug in the merged org list is visibility,
  not membership, and does not count;
- for the Dimagi organisation only: a Dimagi email address
  (`is_dimagi_user`). This is the one Dimagi-specific line left, and it is a
  *membership* rule, not a privilege: Dimagi's people are spread across many
  Connect organisations and labs has always recognised them by domain
  (`SyntheticOpportunity.is_accessible_to`). It makes no Dimagi address the
  programme anywhere Dimagi does not run.

**Operator, not buyer.** The issue asks that "Dimagi staff can see/help" stop
being conflated with "Dimagi is the buyer". The operator role is that
separation: it is the Django staff bit (as `org_access` already does — "the
domain is exactly who an attacker in this case would be"), not the email
domain; it makes no organisation the buyer; it acts *for* the running
organisation, so OES's records stay OES's, and the `OperationCall` keeps who
actually typed it. A Dimagi address without the staff bit is, in OES's
programme, a partner like anyone else.

## 3. Writes in a programme belong to its running organisation

Enforced at the one choke point every surface passes through
(`operations.call_operation`, beside `stamp_provenance`), for every write:

- system caller (seeders, update links, the market's `_as_manager`): unchanged;
- `programme` / `operator`: allowed;
- `partner`: allowed **only** for operations that take provenance
  (`identity.takes_provenance` — receipts, consignments, stock, network,
  contracts they buy themselves), and those stay attributed to the partner and
  `partner_reported`. Every other write — tenders, quotes, awards, approvals,
  payments, suppliers, catalogue — is refused with a message naming the
  running organisation.

Real programmes see no change (their holders are the owning org's members, and
the runner is declared/defaulted to match). The change bites only where holding
is visibility: a labs-only programme seen by someone outside its running
organisation. That is the hole in §0, closed.

Reads are unchanged: a partner who holds the programme can still read it.

## 4. What follows from the standing

- `stamp_provenance`: `is_programme = role in {programme, operator}`;
  attribution defaults to `acting_org`; the derived source is `we_recorded` for
  those roles and `partner_reported` for a partner.
- `acting_org_name` (email signatures): the running org for programme/operator,
  the caller's own for a partner.
- Overview heading, banner fallback, `standing_rows(own_org_id=)`: **the running
  organisation**, whoever is viewing — "ours" is the programme's, not the
  viewer's.
- Provenance forms: `initial` source follows the viewer's role, so a partner's
  form is not pre-filled with a value submit would refuse.
- `resolve_org` multi-org refusal: a partner who belongs to two organisations
  here may name one with `recorded_by_org_id`; it is honoured when it is one of
  theirs (today the refusal fires before the payload is read).
- The heading says who runs the programme, and an operator sees that they are
  acting as platform support for it.
- `clone_upstream.BUYER_SLUG` becomes a parameter, defaulting to the programme's
  running organisation.

## 5. Security properties (what the review must hold us to)

1. An organisation never acts as the programme in a programme it does not run.
   Programme standing requires membership of the declared runner, and
   membership is a grant (OrgMembership for it, Connect membership of a
   Connect-linked org, or Dimagi's domain for Dimagi only) — never visibility.
2. Nobody reassigns the runner except staff/system.
3. A partner cannot make its word read as first-hand, nor attribute a record to
   another organisation (unchanged).
4. A partner cannot perform procurement writes in a programme it does not run
   (new).
5. Dimagi's programmes: every Dimagi address is a member of Dimagi, Dimagi is
   declared for every programme with data, so behaviour there is identical.
6. Fails closed: an unresolvable standing (membership lookup fails, no user)
   is `partner`, never `programme`; a declared runner row cannot be created
   through any partner-reachable path.

## 6. The example organisations

Both labs-only (synthetic), invented data, no real person or price; seeded by a
management command (`supply_seed_org_procurement`) through the same operations
a person uses, with the runner declared first.

- **OES — a pooled buyer.** Operation End Starvation does not deliver; it
  contracts implementers and says its procurement principle is
  cost-effectiveness and transparency across a diverse range of producers
  (`2026-09-24-oes-demo-environment-design.md` §0). So its own programme is a
  **pooled RUTF procurement**: OES tenders to producers, compares landed cost,
  awards, contracts as buyer of record and delivers into its implementers'
  stores. This is the shape OES itself would run, and it is a programme Dimagi
  plays no part in.
- **A local implementing organisation (LLO).** The product owner's own words
  (2026-09-24): "the orgs themselves could be using all the same functionality
  as dimagi to get bids for opps they are doing". An LLO delivering a Connect
  opportunity — here an invented Kenyan community-health LLO running a
  diarrhoea and safe-water opportunity — buys its own ORS/zinc and chlorine
  tablets from local distributors. Different in kind on every axis that
  matters: an implementer not a funder, small not pooled, local distributors
  not international producers, a different country and basket.

Each gets an admin seat: an `OrgMembership` (admin) for a named local user
when the command is given one; on labs the real admin joins by an org
invitation, the existing mechanism.

## 7. Build

1. **Runner + standing** — `ProgrammeRunner`, data migration, `programme_standing`,
   identity/provenance generalised, write gate, runner operations, views/forms/
   banner, `clone_upstream` parameter. Tests: the security properties in §5 as
   tests, plus Dimagi-unchanged regression tests.
2. **The example organisations** — `supply_seed_org_procurement` (OES pooled
   RUTF; LLO safe-water), DEBUG-only dev sign-in as a named seeded user for the
   walk.

## 8. Adversarial review, and what changed (2026-10-12)

An adversarial security review of §1–§7 (before building) found the
overriding property did not hold. Its findings, by class, and the change each
forced:

| # | Finding (class) | Change |
|---|---|---|
| C1 | **Membership takeover of an unclaimed organisation.** A programme team may link any `LabsOrg` as a supplier and send it a marketplace invitation; the first person to accept an invitation to an organisation with no members becomes its admin. Aimed at the Dimagi row (which no one joins by membership) or a freshly declared runner, that is programme standing in its programmes. | A **running organisation** (named by any `ProgrammeRunner`, or the Dimagi row) is a protected row: `org_access.require_change` / `require_merge` are staff/system only for it, `supplier_market_invite` refuses it, and Dimagi's membership is the email rule **only** — no `OrgMembership` or Connect link counts for it. |
| C2 | **Unauthorised re-keying.** `update_supplier` set an organisation's Connect id with no permission check, which hands it to that Connect organisation's members. Exploitable against the existing Dimagi rule today. | `update_supplier` calls `org_access.require_change` before changing the company's Connect id, name or country. |
| H1 | **"Takes provenance" is not an authorisation boundary.** It covers contract edits, payments, invoices and documents on any contract in the programme. | **Partners are read-only** in a programme. Every write needs programme or operator standing (or the system caller). Partners keep their own channels: update links and the marketplace, both of which run as the system caller and are scoped to the partner's own rows. Only `org_upsert` / `org_merge` are exempt, because they are governed by `org_access` and name no programme. |
| H2 | The tender-listing manage page used holding the programme (visibility) and any admin membership. | `market.can_manage` requires programme/operator standing for the tender's programme, or an admin membership *granted for* the owner organisation. |
| H4 | `org_merge` would repoint `ProgrammeRunner.org`. | Covered by C1: merging a running organisation is staff-only. |
| H5 | Defaulting an undeclared programme to Dimagi makes every Dimagi address the programme in another tenant's new labs-only programme. | **No default.** An undeclared programme has no running organisation: only staff (operator) and the system act as the programme there. The migration declares Dimagi for every programme with data today; Dimagi's seeders (`supply_dev_seed`, the clone seeders) declare Dimagi for what they create. |
| M1 | The seeder trusted a label and the registry's name-bucket filing. | The example organisations are fixed-key rows the seeder owns; it refuses an existing opportunity filed under any other organisation. |
| M2 | An operator's write defaulted to `we_recorded`, asserting the programme's own staff saw it. | An operator's provenance write must state its source; nothing is defaulted. `supply_dev_seed` (which makes a staff `dev` user) refuses to run without `DEBUG`. |
| M3 | An update link issued to the running organisation lets its holder write as the programme. | Issuing an update link to a programme's running organisation is refused. Revoking links issued before this change is left as a follow-up. |

Left as follow-ups (recorded, not built here): reads by a partner of a
labs-only programme are still governed by visibility, so quotes and prices are
readable by anyone who can see the programme (H3: give every declared
programme a non-empty `allowed_domains` — the seeded examples do); `resolve_org`
ignores `OrgMembership` for a partner's own organisation (M4; no longer used to
authorise anything).

## 9. Follow-ups closed (2026-10-14)

- **H3.** `programme_runner_set` refuses to declare any organisation but Dimagi on a
  programme with a labs-only registration that is open to every email domain (no
  `allowed_domains`, no workspace). Support sets the domains first. Dimagi's invented
  demo programmes stay open, as they were; the fail-open default itself is the
  security audit's (#1032 item F), not this design's.
- **M3.** Declaring a running organisation revokes every live update link issued to it
  in that programme (`runner.revoke_links_to_runner`), and migration 0057 revokes any
  issued before #2458.
- **M4.** Left as is, deliberately: `resolve_org` names who a partner is for
  attribution and no longer authorises anything (§5's `standing` does).
- **OES signup.** Moot: the OES satellite and its signup were removed on 2026-10-14.
- **A screen for support.** `/supply/runner/` (`runner/views.py`): staff declare or
  change the running organisation; the Overview links to it for staff.
- **A team, and how it grows (2026-10-14).** A running organisation takes no
  invitations (C1), so its people are kept at `/supply/team/`
  (`programme_team_list` / `_add` / `_remove`). Platform support, or an admin
  granted for that organisation, adds people by the email they sign in with.
  Someone who has never signed in is refused, and so is someone the program's
  allowed domains would not let open it. The last admin cannot be removed.
  Support adds the FIRST admin with `manage.py supply_team_add <program> <email>
  --role admin`, which runs the same operation as the system caller. Dimagi has
  no team list: its people are recognised by their address.

## 10. A programme's managers declare it (2026-10-18)

Staff-only declaration left every new programme with no runner until someone asked
support, and its own managers partners in it (measured: program 353, `org_id: null`,
`your_standing: "partner"` for a member of the organisation that manages it). §1's
"declaring is staff-only" is replaced by `runner/declaring.py`:

- **First declaration:** a member of the organisation that manages the programme in
  Connect. That is read from Connect's own `opp_org_program_list` for the caller's
  token (Connect lists a programme only to members of the organisation that owns it),
  with every labs-only entry removed, so a synthetic registration's name-bucket never
  counts. A programme made since the cached list is fetched again before refusing.
- **Changing it:** an admin granted for the current running organisation, or a Connect
  member of it when Connect knows it (labs cannot see Connect's admin role). A
  Dimagi-run programme changes only by support: Dimagi's members are a domain.
- **Whom they may name:** the managing organisation (by Connect id, never slug);
  Dimagi, when the caller is Dimagi and manages it; an organisation delivering an
  opportunity in the programme as Connect lists it (by Connect slug, refused when two
  rows claim it); an organisation awarded a call for the programme by its manager or
  current runner; the organisation whose call awarded the current runner (handing it
  back). Never a labs-only row. Support declares anything, as before.

Unchanged: `declared_by` (now any declarer, not only an operator), revoking the new
runner's update links, the open-to-any-domain refusal, the cached standing reset, and
a running organisation's row being support-only to change (§8 C1).

Two consequences, both needed for an awarded implementer to run a programme:

- **A declared runner's members hold the programme** (`SupplyDataAccess`), even
  though Connect lists it only to its manager. Not for Dimagi: a Dimagi address that
  Connect does not list a Dimagi-run programme to still does not hold it.
- **A Connect member of a Connect-known runner changes its team**, as its admins do;
  otherwise nobody but support could add the first person to such a team.
