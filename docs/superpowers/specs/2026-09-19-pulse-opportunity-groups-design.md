# Pulse opportunity groups: several Connect opportunities, one real engagement

**Status:** approved design, not yet implemented (2026-09-19)

## The problem

Connect Interviews was run as one engagement per partner, but Connect has no
way to say so: each interview cohort was created as its own opportunity.
COWACDI's interviews are 37 opportunities and eHealth Africa's are 35 — names
like `[01TRS] COWACDI Interviews`, `[2ABT2CB3] COWACDI Interviews` and
`INT - NG - COWACDI - EXT - Jul26 [1ECC1]`, most carrying between 20 and 400
interviews.

Pulse treats an opportunity as an engagement, so every surface repeats that
split:

* The partner window lists 37 rows for what a person would call one piece of
  work, and the three real CHC engagements sit among them.
* COWACDI reads as 40 opportunities; it ran four things.
* A cost belongs to an opportunity, so the interview organisation fee — paid
  outside Connect, and therefore typed into labs by hand — has to be entered 37
  times. The costs page raises all 72 cohorts as needing a person
  (`PulseCostEntry`, `costs.cost_issues`, shipped in #1941–#1943).

The interviews are finished, so this is a fixed set of opportunities to be
grouped once. The model must still be general: the same shape of engagement can
recur, and the fix should not be specific to interviews.

## What this is not

Not partner grouping. #1940 grouped Connect *organisations* — several
workspaces, one partner. This groups *opportunities* under one organisation.
They compose: COWACDI is one partner running four engagements, one of which is
an interview group inside its `cowacdi-interviews` workspace.

## Approach

A group record, resolved wherever an opportunity is listed, counted, filtered
or costed. The stored per-cohort rows are never rewritten, so the grouping is
presentational and can be corrected or undone, and the per-cohort record that
Connect's invoices and exports key on stays intact.

Two alternatives were rejected:

* **A stand-in opportunity row** for the group, with cohorts pointed at it, so
  every screen taking an opportunity id works unchanged. But services and
  payments still carry the cohorts' own ids, so every figure would need the
  member mapping regardless — and a fabricated row has to be defended against
  an ingest that rewrites (`refresh_opportunities`) and reclassifies
  (`reclassify_opportunities`) every opportunity it sees.
* **Rewriting the cohort id on the spines at ingest.** Simple to read
  afterwards, destroys the per-cohort record, and cannot be undone.

## The model

```python
class PulseOppGroup(models.Model):
    slug = models.SlugField(max_length=120, unique=True)
    name = models.CharField(max_length=300)
    org_slug = models.CharField(max_length=120, blank=True)
    why = models.TextField()
    created_at / updated_at
```

and on `PulseOpportunity`:

```python
group = models.ForeignKey(
    PulseOppGroup, null=True, blank=True, on_delete=models.SET_NULL, related_name="members"
)
```

**Membership is a link on the opportunity, not a list on the group**, so the
database guarantees an opportunity belongs to at most one group. A fact counted
under two groups would make a partner's totals disagree with the estate's, and
no amount of care in application code enforces that as well as the column does.

**`why` is required**, enforced in `save()` like `marketplace.OrgConnectSlug`.
A grouping states that several engagements were really one; unexplained, it is
a guess a later reader trusts.

**`org_slug` is the group's own**, copied from its members at creation and used
for listing and scoping. A group spanning two organisations is refused at
creation: it would have to appear under both partners or neither.

Ingest safety: `refresh_opportunities` writes an explicit field list through
`update_or_create` and never deletes opportunities, so a refresh cannot clear
membership. `reclassify_opportunities` rewrites only `is_test` and
`service_slug`. A test pins both.

## Resolution

One module, `connect_labs/pulse/groups.py`, holding the whole mapping — the
same shape as the partner resolution in `api._partner_keys` / `_Partner`:

* `key_for(opportunity_id) -> str | int` — the group's slug, or the id when
  ungrouped. The key every listing folds on.
* `members(key) -> list[int]` — the opportunity ids a key covers.
* `resolve(raw) -> group | opportunity | None` — what `?opportunity=` names.
* `collapse(rows, id_key="id") -> list` — fold per-opportunity rows into one
  row per group, summing counts and money, adding sparklines week by week, and
  recomputing rates from the summed numerator and denominator rather than
  averaging rates.

A cached slug→members map, invalidated on write, following `partner_names`:
resolution runs per request and must not cost a query per opportunity.

## Surfaces

Everywhere an opportunity is the unit, the group takes its place.

1. **`?opportunity=`** (`api._program_scope`) accepts a group slug and narrows
   events, works, opportunities and rollups to the members with
   `opportunity_id__in`, including the raw SQL replay sampler
   (`= ANY(%s)`, as the partner filter now does). **A member's own id resolves
   to its group**, so two links to the same work cannot report different
   figures. The per-cohort breakdown lives on the group page instead.
2. **The partner window roster** (`api._opportunity_roster`) returns one row
   per group, with `members` carried for the page to show.
3. **Counts** (`api._scope_for`, `views.PulseIndexView._dossier_opps`, the
   programs page) count a group as one opportunity.
4. **The group page** — `/labs/pulse/opp/g/<slug>/`, rendering the existing
   opportunity page against the group's combined figures, plus a table of its
   cohorts with each one's own figures. A request for a member's page
   redirects there, so a bookmarked cohort still lands somewhere truthful.
5. **Costs** (`PulseCostEntry`, `costs.opportunity_costs`, `costs.cost_issues`,
   `/labs/pulse/costs/`): an entry may name a group instead of an
   opportunity — exactly one of the two, enforced in `save()`. Costs key on the
   group key, so a per-service fee spreads over the group's approved work and
   the group's issues are raised once. The 72 "worker pay but no organisation
   pay" rows become two.

## Seeding

A management command, `pulse_group_opps`, following the repo's other bootstrap
commands:

```
make manage CMD="pulse_group_opps --org cowacdi-interviews --service interview \
    --name 'COWACDI Interviews' --why 'One interview engagement; Connect made a \
    cohort an opportunity' [--dry-run]"
```

It selects by organisation and delivery type, prints what it matched, and
**records the matched ids as explicit members**. The selection is how the
members were found, not a standing rule: a cohort created later does not join
by itself. That is the right trade for a finished engagement, and it means no
future opportunity is ever silently swept into a group.

Run once for COWACDI and once for EHA.

## Testing

* `groups.py`: key folding, member expansion, rate recomputation from summed
  parts, and a group with one member behaving exactly like an ungrouped
  opportunity.
* API: the roster returns one row for a group; `?opportunity=` with the group
  or with any member scopes to every member; the opportunity count falls to
  four for a grouped partner; an ungrouped partner is untouched.
* Ingest: a refresh and a reclassify leave membership intact.
* Costs: one group-level entry spreads across the group's approved work; a
  group's issues are raised once, not once per member.
* Model: a group spanning two organisations is refused; a group without a
  stated reason is refused; an opportunity cannot be in two groups.

The fixtures use an invented organisation with invented cohorts, as
`test_org_drilldown` does — the behaviour is what matters, and real partner
identity belongs in the directory rather than in a fixture of a public repo.

## Out of scope

* **Donor reports** (`PulseReport.opportunity_id`) can still name only a single
  opportunity. No report uses an interview cohort today; worth a follow-up.
* **Connect-side grouping.** Connect models an organisation's entities but
  exposes no grouping for opportunities, so this stays a labs reading of the
  data until it does.
