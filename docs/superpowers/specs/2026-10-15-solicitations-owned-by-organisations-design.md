# Solicitations owned by organisations

Status: approved for build 2026-10-15 ("fix everything"), following a first-use
review and code audit of `/solicitations/`.

## Why

Solicitations were stored as LabsRecords on prod Connect, scoped by the
programme that happened to be selected when one was created. That store cannot
express what a call for partners needs:

- **Responses are confidential to the soliciting organisation.** A respondent is
  by definition not a member of the owner's programme, so prod refuses a scoped
  write from them; the app therefore saved every response and review
  `public=True`, and any signed-in labs user could read every applicant's
  answers, firm name and email, and award any response, including their own.
- **The owner is an organisation, not a programme.** Most applicants, and some
  owners, have no Connect organisation at all (see `local_models.py`). Prod's
  `organization_id` scope is a Connect id, so an org-owned call was written with
  no scope.
- **Lookup by id fetched every public record and scanned it**, so a non-public
  call could never be opened, and edits replaced the whole `data` blob, wiping
  fields the form does not carry.

The labs database already holds `Solicitation` / `SolicitationResponse` tables
(marketplace EOI rounds) with a real `LabsOrg` foreign key and the same field
vocabulary, written deliberately so "a round can migrate between stores without
anything downstream being rewritten". This change does that migration.

## Decisions

1. **One store: the labs DB.** `local_models.Solicitation` becomes the record for
   every solicitation, live or historical. New: `owner_org` (FK `LabsOrg`,
   PROTECT, nullable only for imported historical rounds), `program_id`
   (optional int, for scoping, microplan links and fund allocation), `listed`
   (bool: appears in public directories), `created_by` (FK User, nullable),
   `program_name`, `fund_id`, `connect_opportunity_id`, `plans` (JSON),
   `source_program_id`, `source_group_id`, `source_plan_ids`,
   `criteria_locked_at`, `legacy_record_id` (unique, nullable: the prod
   LabsRecord id it was imported from). Status gains `awarded`.
2. **Responses** use `local_models.SolicitationResponse`, extended: `source_row`
   becomes nullable (web submissions have none; the unique constraint still
   holds for imports), `submitted_by` FK User, `llo_entity` = the respondent's
   organisation (a `LabsOrg`), award fields (`reward_budget`, `award_stage`,
   `awarded_at`, `awardee_notified`, `org_id`), `selected_plan_ids`,
   `legacy_record_id`. Status: `draft | submitted | under_review | awarded |
   rejected`. One draft per (solicitation, submitted_by), edited in place.
3. **Reviews** get a new table `SolicitationReview` (response FK, reviewer FK
   User, criteria scores JSON, overall score computed from the weighted criteria,
   recommendation, notes, `legacy_record_id`), unique per (response, reviewer).
4. **Who may do what** is decided in one place, `solicitations/access.py`,
   reusing Supply's grant-based membership (`supply_chain.runner.is_member`,
   `is_operator`):

   | Standing | Who | May |
   |---|---|---|
   | `owner` | a member of `owner_org` by grant | edit, publish, close, read responses, review, award |
   | `operator` | Django staff | everything an owner may, recorded as themselves |
   | `respondent` | the user who submitted a response | read and edit their own response (until submitted), see its status |
   | everyone else | signed in or not | read listed solicitations; respond as their own organisation |

   A respondent can never review or award their own response. Visibility of the
   org in a user's labs context is NOT membership (the same rule Supply uses).
   There is no Dimagi fallback for an unowned historical round: only staff
   manage those.
5. **URLs.**
   - `/solicitations/` — the generic directory: every *listed* solicitation that
     is open (status active and deadline today or later, or no deadline), each
     card naming its owner; a "Recently closed" section; search and owner/type
     filters. Readable without a labs login (public calls are meant to be sent to
     outside organisations; nothing private is on these pages).
   - `/solicitations/o/<org_slug>/` — one organisation's solicitations (open and
     past). `<org_slug>` is `LabsOrg.slug`; aliases and Connect slugs redirect.
   - `/solicitations/o/<org_slug>/manage/` — the owner's workspace: every call
     the org owns, with response counts, drafts included. Staff see a switcher.
     `/solicitations/manage/` lists the orgs the user may manage (one → redirect).
   - `/solicitations/<id>/` and `/solicitations/<id>/edit/` keep working (ACE
     emits them): an id matching `legacy_record_id` resolves to that row,
     otherwise the pk. The `Solicitation` id sequence is restarted above the
     largest prod record id so the two ranges never collide.
   - Create takes an owner org (picked from the orgs the user may manage), not
     the labs context selector. `?source_program_id=…` from microplans still
     works.
6. **Deadlines are enforced.** `can_accept_responses` is false after the
   deadline; the status chip reads "Closed — deadline passed"; respond refuses.
7. **"Notified" means notified.** Awarding sends the awardee an email through
   the existing outbound email path (`docs/OUTBOUND_EMAIL.md`) when enabled, and
   records `awardee_notified` only if it was sent. Submitting a response
   confirms to the respondent and tells the owner's contact.
8. **MCP and agents keep their contracts.** The 9 solicitation tools, 4 review
   tools and the three AI agents keep their names, argument schemas and output
   shapes; they call the new data access, which enforces standing as the
   caller. `create_solicitation` takes `org_slug` (preferred) or `program_id`
   (owner resolved from the programme's Supply runner, else refused with a
   message naming `org_slug`). Dead `solicitations/mcp_tools.py` is deleted;
   the csrf-exempt JSON API is deleted (no in-repo callers; MCP is the API).
9. **Migration of prod data** is a staff-only long-running MCP tool,
   `solicitations_import_records`, run once after deploy as a staff user: it
   reads prod `solicitation`, `solicitation_response` and `solicitation_review`
   records, upserts by `legacy_record_id`, sets `owner_org` from the programme's
   runner (or an explicit `default_owner_org_slug` argument), and leaves calls
   whose title or programme marks them as smoke/test unlisted. Re-running is
   safe. Prod records are left untouched.
10. **Demo.** `solicitations_seed_demo` (management command) creates a labs-only
    demo organisation with three open calls and a few responses, so external
    demos never show real applicants.

## Page fixes carried in the same change

Owner shown on every card and detail; `<title>` per page; markdown stripped from
card previews; long `estimated_scale` clipped to one line on cards; a key-facts
strip and an "On this page" section nav on long calls; one badge style per type;
the respond form asks for the organisation (picked from the user's orgs, or a
new one entered by name and email) instead of "Survey firm / organization"; a
"what happens next" note beside Submit; the award page in the call's currency
(no hard-coded ₦); blind review that is actually blind (the reviewer's list
links to the blind review, not the named detail); the AI review prompt no longer
shows internal tool names.

## Out of scope

Payments and Connect opportunity provisioning after award (still deferred, as
before). Invite-only (private) calls beyond `listed=False`.
