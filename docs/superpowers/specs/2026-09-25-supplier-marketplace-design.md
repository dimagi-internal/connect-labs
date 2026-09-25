# The supplier marketplace — design

**Status:** design agreed in conversation 2026-09-25; spec awaiting review.
**Reads against:** `2026-09-11-rutf-procurement-design.md` (the domain, and §7's
"a tokenized supplier form attaches to the same records later"),
`2026-09-24-oes-demo-environment-design.md` (the Dimagi-side demo this sits
beside), `docs/multi-site-auth.md` (why a login is not an authorisation).

## 0. Why

Every quote in `supply_chain` today was typed by the programme team from a
supplier's email. The domain was built so that it would not have to stay that
way: quotes are the supplier's stated facts, the comparison refuses to rank
what it cannot defend, and `questions.py` already knows exactly which facts to
ask each supplier for. What is missing is the supplier's side of the counter.

This builds it: a market anyone can browse, and a way for a supplier — one we
already know, or one we have never heard of — to sign in, describe itself once,
and bid on a round. The bid is the same `Quote` the programme team compares,
marked as the supplier's own entry rather than our transcription.

## 1. What a visitor can do, by who they are

| Visitor | Can |
|---|---|
| Anyone, no login | Browse `/supply/market/`: every open **public** round — products, quantities, delivery point, incoterm requested, deadline, minimum shelf life, the product's specification requirements and the notes to suppliers. |
| Signed in to labs, no organisation yet | The above, plus **Register your organisation** and fill in the supplier profile. |
| Member of an organisation with a supplier profile | The above, plus see open **private** rounds their organisation was invited to, **bid**, revise or withdraw their own bids, see **what the buyer still needs from them**, and invite colleagues. |
| Programme team | Unchanged screens, plus: mark a round private, see which quotes a supplier entered itself, see self-registered suppliers flagged, mark them reviewed, and invite an existing supplier to the market. |

Signing in is the ordinary labs sign-in through Connect. Any Connect user can
already complete it; the callback refuses nobody. A supplier therefore needs a
Connect account like everyone else, and nothing about the login is new.

## 2. Identity

### 2.1 Organisations have members

`LabsOrg` holds identity and nothing else (its docstring, and the marketplace
spec's one rule). Membership is a fact *about* an organisation, so it lives in
a table that points at one:

`marketplace.OrgMembership(org → LabsOrg, user → User, role: admin|member,
added_by → User, created_at)`, unique on (org, user).

**The organisations a user acts for** = the `LabsOrg`s whose
`connect_organization_id` is one of the user's Connect organisations
(`get_org_data(request)["organizations"]` — Connect is authoritative for linked
organisations, per `LabsOrg`), **plus** their `OrgMembership` rows. The second
set exists for the organisations that are "local for good" (RUTF spec §26) — a
manufacturer that will never have a Connect org — which is most suppliers.

One function, `marketplace.membership.orgs_for(request)`, answers this; nothing
else re-derives it.

A user acting for more than one organisation picks which one on the bid form.
Most act for one, and then there is no choice to show.

### 2.2 The supplier profile is cross-programme

`Supplier` is per-programme (`scope_key`): a register row in one programme's
catalogue. A supplier filling in its details once for the whole market needs a
home that is not per-programme:

`supply_chain.SupplierProfile(org → LabsOrg, 1:1)`: `type`
(manufacturer / distributor / trader), `country`, `city`, `website`,
`categories[]` (what it supplies, from `records` commodity categories),
`contacts[]` (same shape as `Supplier.contacts`), `description`, `created_by`.

It is `supply_chain`'s profile on the org, the way `OrgProfile` is the
marketplace's — the pattern `LabsOrg` was designed for.

### 2.3 A new supplier

Signs in → `/supply/market/register/` → name, country, type, website,
categories, one contact → creates the `LabsOrg` (slug derived from the name,
de-duplicated), the `SupplierProfile`, and an admin `OrgMembership`.

Registration **refuses an existing organisation's name** (case- and
punctuation-insensitive match against `LabsOrg.name` / `short_name`) and says
to ask for an invitation instead. Two rows for one organisation is exactly the
duplication `LabsOrg` exists to end, and a self-registration is the cheapest
way to create one.

### 2.4 An existing supplier

On a programme's supplier page: **Invite to the market**, with an email. This:

1. binds the `Supplier` to a `LabsOrg` if it is not already (`Supplier.org`),
   creating one from the supplier's name/country if needed;
2. creates the `SupplierProfile` from the `Supplier` row if the org has none;
3. issues a `marketplace.OrgInvite(org, email, token_hash, token_hint,
   expires_at, accepted_at, accepted_by, issued_by)` — one-time, 30-day,
   hashed exactly as update-link tokens are (`update_links/tokens.py`).

The raw link is **shown once** to the issuer to send themselves, as update links
are; email sending is out of scope. Opening it while signed in adds the user as
an admin member (if the org has no members) or a member. **An email address is
never trusted on its own** to join an organisation: a Connect account's email is
not proof of employment, and the token is.

An org admin can invite colleagues from their organisation page the same way.

## 3. Rounds: public by default, private by choice

`Round.visibility`: `public` | `private`. **Default `public`** for new rounds.

**Existing rounds migrate to `private`.** The domain already holds real
programmes' rounds; none was created in the knowledge that it would be
world-readable, and a migration must not publish them. New rounds are public
unless someone says otherwise; old ones are private until someone says
otherwise. The round form and round page show and change it.

What a round shows on the market is only ever **open** rounds. Draft, closed
and awarded rounds are not listed, and their pages 404 on the market (the
programme's own pages are unchanged).

A **private** open round is visible, and biddable, only to organisations that
were invited to it: an `Outreach` row on that round to a `Supplier` whose `org`
is one the user acts for. Anonymous visitors and uninvited organisations get
the same 404 as a round that does not exist, so a private round's existence is
not disclosed.

**What the public page shows about the buyer:** the round's label, lines,
delivery point and deadline. Not the programme id, not other invitees, not any
quote. The market URL uses the round id, which is not a secret.

## 4. Bidding

### 4.1 Sealed

A supplier sees its own quotes and nothing else: no other supplier's price,
count of bids, or name. The comparison stays a programme-team screen.

### 4.2 One bid is one quote per product line

`/supply/market/rounds/<id>/bid/?line=<commodity_slug>` asks for the fields
`quote_record` takes, in supplier language:

price, and what it is per (`per_base_unit` / `per_pack` / `per_lot_total` /
`per_metric_tonne`); currency; the quantity the price covers; pack size
(units per pack, grams per unit) — which sets `pack_spec_source=stated_on_quote`
when given and `not_stated` when blank; freight and duties each *included /
excluded / don't know*, with an amount when excluded; shelf life; lead time;
minimum order; valid until; incoterm; one answer per specification requirement
on the product (`stated_spec`); notes.

**Blank means "not stated", never zero** — the same rule `quote_record`'s own
summary gives: do not compute anything, leave unknowns unknown. The comparison
then asks for them, which is its job.

`received_on` is today. `supplier_id` is the programme's `Supplier` row for the
organisation (§4.4).

### 4.3 Revise and withdraw

- **Revise** writes a new version through `quote_correct`, reason
  "revised by the supplier", so a past award's frozen comparison stays
  reproducible (the reason the operation exists).
- **Withdraw** is `quote_void`, reason "withdrawn by the supplier".
- Both are refused once the round is no longer open.

### 4.4 The programme's `Supplier` row

A bid needs a `Supplier` in the round's programme. Resolution, in order:

1. a `Supplier` in that programme's scope whose `org` is the bidding org — an
   existing supplier, bound by invitation (§2.4) or by the team;
2. otherwise, a new one created from the `SupplierProfile`, with
   `Supplier.origin = "self_registered"` and `reviewed_on = null`.

`Supplier.origin` (`programme` | `self_registered`, default `programme`) and
`Supplier.reviewed_on` / `reviewed_by` are new. A self-registered supplier's
bids **count in the comparison immediately, visibly flagged "self-registered,
not yet reviewed"** beside the supplier's name. The supplier page gets **Mark
reviewed**, which clears the flag. Nothing is hidden from the comparison: a
hidden bid is a silent failure, and the team can void a quote it will not
consider, with a reason, as it can today.

### 4.5 Provenance on the quote

`Quote.entered_by` (`programme` | `supplier`, default `programme`) and
`Quote.entered_by_user`. The quote page reads "Entered by <org> through the
market" or "Recorded by the programme team"; the comparison shows a small mark
on supplier-entered quotes. This is the quote-level version of the
"who typed it, and how did they know" distinction the fulfilment tier already
makes — a supplier's own entry and our transcription of its email are
different evidence.

### 4.6 What the buyer still needs from you

`/supply/market/bids/` lists every one of the org's quotes across rounds —
round, product, price as stated, round status, and **awarded to you** / **not
awarded** once decided. Under each live quote: the `missing_facts` whose
`audience == "supplier"`, as questions. Answering is revising the bid. The
comparison's "unconfirmed — ask them" becomes a to-do list the supplier clears
itself, and nobody on the programme team retypes the answer.

## 5. Guards

The pattern is `update_links/service.py`'s: the forms offer only what is in
scope, and **the service re-reads scope from the database on every write**, so
a hand-crafted POST cannot cross it.

- Market reads use a `MarketService` that runs its queries directly against
  the models (no `SupplyDataAccess`: the supplier is not a programme member and
  must not become one). It returns only open, visible rounds and the caller's
  own quotes.
- Writes go through the ordinary operations (`quote_record`, `quote_correct`,
  `quote_void`, `supplier_create`) via `call_operation` with a `SupplyDataAccess`
  for the round's programme under the `SYSTEM` caller — **after** the service
  has checked: the user is signed in; acts for the org; the org has a profile;
  the round is open and visible to the org; the quote (for revise/withdraw)
  belongs to the org's `Supplier` in that round. The same schemas therefore
  validate a supplier's bid as the team's.
- The audit trail records the real user: the service sets the audit context
  actor before the write, as the update-link views do.
- CSRF on every form. Public pages carry no per-user state.
- No email-domain check on registration: the login is Connect's, not a new
  signup into the shared user table, so multi-site-auth checklist item 4
  (open signup minting privileged identities) does not arise.

`/supply/market/` is **not** skip-listed in `oauth_session.py`. Anonymous
visitors pass through that middleware untouched; signed-in bidders need a real
labs session, which is the point.

## 6. Screens

All server-rendered Django templates in `supply_chain` style (Tailwind; no
Bootstrap), under their own light shell — no programme picker, no programme
tabs, because the market is above programmes.

| URL | Who | What |
|---|---|---|
| `/supply/market/` | anyone | Open rounds as cards; filter by category and delivery country. Signed-in members also see their private invitations, labelled "Invited". Header: Sign in / Register / your organisation. |
| `/supply/market/rounds/<id>/` | anyone (public) / invited (private) | The round, per line: product, quantity, specification requirements, shelf life, deadline, delivery point, notes. Per line, "Bid" — or "Sign in to bid" / "Register to bid". Your own bids on it. |
| `/supply/market/rounds/<id>/bid/` | member with profile | The bid form (§4.2); pre-filled from the current bid when revising. |
| `/supply/market/quotes/<id>/withdraw/` | member | Confirm and withdraw. |
| `/supply/market/bids/` | member | My bids and what the buyer still needs (§4.6). |
| `/supply/market/register/` | signed in | Register an organisation and its supplier profile. |
| `/supply/market/organisation/` | member | Profile (editable by admins), members, invite a colleague. |
| `/supply/market/invites/<token>/` | signed in | Accept an invitation. |

Programme-side additions: visibility on the round form and a badge on the round
board and round page; "Entered by the supplier" on the quote page and a mark in
the comparison; the self-registered flag and **Mark reviewed** on the supplier
page and in the comparison; **Invite to the market** on the supplier page; a
**Market** link in the supply nav.

## 7. Out of scope

- Sending email. Invite links are shown once to copy, as update links are.
- Attachments on bids (certificates, spec sheets). `Document.quote` exists and
  can take them later.
- Bidding on anything but an open round's lines — no unsolicited offers.
- Seeding the OES demo. The other agent owns `scripts/walkthroughs/oes-demo/`;
  RUTF round 2, once seeded, is public by default and appears on the market with
  no further change.

## 8. Testing

TDD per unit. The tests that matter most assert refusals, and each is
mutation-checked (delete the guard, watch it go red):

- an anonymous visitor cannot see a private round, a draft/closed round, or any
  quote;
- a signed-in user cannot bid without acting for an org with a profile;
- an uninvited org cannot see or bid on a private round;
- one org cannot revise or withdraw another's quote, nor read it;
- a bid on a closed round is refused at the service even if the form is posted;
- the existing-round migration leaves every pre-existing round private;
- registration refuses an existing organisation's name;
- a self-registered supplier's quote appears in `round_compare` and carries the
  flag.

After merge and deploy: a browser pass on labs — browse anonymously, sign in,
register an organisation, bid on a public round, and see the bid, flagged, in
the programme team's comparison.
