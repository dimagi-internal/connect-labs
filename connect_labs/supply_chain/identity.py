"""Who is writing, derived from who is signed in.

Every provenance-bearing row records `recorded_by_org` and a `source`
(design doc section 17.3), because a fact we did not witness is a claim and a
claim is worth only as much as its claimant. Until now both arrived **in the
payload**, which means a caller could assert any of them: a partner could
record a receipt as though we had witnessed it, and nothing in the system
would know. A self-asserted assertion carries no weight, so provenance has to
be derived from the session rather than accepted from the request body.

The derivation is org membership, and it needs no new grant mechanism.
Connect already knows which organisations a user belongs to, labs already
carries that list, and `LabsOrg` already joins on both of Connect's keys
(design doc section 27). So "which organisation is this caller acting for" is a join,
not a new concept.

Three ways in, and each knows the user:

  - a web screen and the HTTP API sit behind `login_required`, so the orgs are
    on the session already and resolution costs nothing;
  - an MCP tool handler is passed `user`, and its Connect token fetches the
    same list -- cached with a TTL, so a write does not mean a round trip;
  - a management command has no user at all. It must be TOLD which
    organisation it acts as, because a caller that cannot say who it is has no business
    writing provenance. That is a refusal, not a fallback.
"""

import logging

from connect_labs.labs.models import LabsOrg
from connect_labs.utils.dimagi_user import is_dimagi_user

logger = logging.getLogger(__name__)

# How a fact reached us implies how much it is worth. `we_recorded` is not a
# default -- it is what is TRUE when the programme's own staff enter something,
# and it is only reachable by someone who belongs to the programme's own
# organisation. `agency` and `supplier` are deliberately absent: an agency's
# report is not a partner's, and SOURCES has no term for it, so those callers
# state their source rather than have one guessed.
# Dimagi, as one organisation across all of labs.
DIMAGI_ORG_SLUG = "dimagi"


def source_for(org) -> str | None:
    """How a fact from this organisation reached us.

    Derived from WHO is acting rather than from a field on the organisation.
    It used to read `Party.kind`, which made "is the programme" a property of
    the organisation -- so one body could be the programme everywhere, which
    is not true of any organisation operating in more than one programme.

    `we_recorded` is not a default. It is what is TRUE when Dimagi's own
    staff enter something, and it is only reachable by them. Everyone else
    is reporting, which is exactly what `partner_reported` says.
    """
    if org is None:
        return None
    return "we_recorded" if org.slug == DIMAGI_ORG_SLUG else "partner_reported"


class IdentityUnresolved(ValueError):
    """The caller cannot be attributed to an organisation.

    A `ValueError` because that is what it is: the request cannot be carried
    out as sent. It subclassed `Exception`, and the HTTP dispatch maps only
    `jsonschema.ValidationError` and `ValueError` to 400 -- so every refusal
    here was a 500, and the message naming the fix never reached anybody.
    Found by attaching a document on labs, before any organisation existed.

    Raised rather than returned so a write cannot proceed on a shrug. The
    message names what would fix it, because the two causes need different
    fixes: a user whose organisation is not on file, and a command that was
    not told which organisation it speaks for.
    """


def _as_org_id(value):
    """An organisation id as an integer, or None for a synthetic slug."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def caller_org_slugs(organizations) -> set[str]:
    """The organisation slugs the caller belongs to.

    Collected alongside the integer ids because `LabsOrg` matches on either,
    and a labs-only organisation has ONLY a slug. Reading just the integers
    is what made a synthetic organisation unmatchable and pushed an importer
    into inventing a local row for it.
    """
    return {org["slug"] for org in organizations or [] if org.get("slug")}


def _integer_org_ids(organizations) -> set[int]:
    """The organisation ids that could match a row.

    Labs folds labs-only synthetic opportunities into the user's organisation
    list with `"id"` set to a SLUG rather than an integer (see
    `labs/context.py`, `_merge_labs_only_opps`), so this list is not uniformly
    typed. `LabsOrg.connect_organization_id` is an integer, so a slug can
    never match one -- it is skipped rather than coerced, and coercing it is
    what made every provenance write by a user entitled to see synthetic
    opportunities a 500.

    A user whose organisations are ALL synthetic therefore resolves to the
    empty set, which is honest: they belong to nothing on file, and the
    stamping layer turns that into a refusal naming `org_upsert`.
    """
    ids = set()
    for org in organizations or []:
        raw = org.get("id")
        if isinstance(raw, bool) or raw is None:
            continue
        try:
            ids.add(int(raw))
        except (TypeError, ValueError):
            continue
    return ids


def _caller_organizations(access):
    """The caller's Connect organisations, or None if unknowable.

    None and the empty list mean different things and must not be conflated.
    None is "there is nobody to ask" -- a management command, or a fetch that
    failed. The empty list is "we asked, and this user belongs to nothing",
    which is a real answer and a refusal.

    Returns the raw entries rather than ids, because an organisation is
    matched on either key and a labs-only one has only a slug.
    """
    request = getattr(access, "request", None)
    if request is not None:
        # Session route: the orgs were fetched at login and include labs-only
        # synthetic orgs for users entitled to them, which is why this goes
        # through get_org_data rather than reading the session directly.
        from connect_labs.labs.context import get_org_data

        org_data = get_org_data(request) or {}
        if "organizations" in org_data:
            return org_data["organizations"]
        # A request whose session carries no organisation list at all is not
        # evidence that the user belongs to nothing -- an expired or
        # half-built session looks exactly like this. Fall through to the
        # token rather than reporting a permission fact we have not
        # established.

    user = getattr(access, "user", None)
    if user is None:
        return None

    from connect_labs.labs.connect_tokens import get_valid_access_token
    from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

    try:
        token = get_valid_access_token(user)
    except Exception:
        logger.info("supply identity: no usable Connect token for %s", getattr(user, "username", user))
        return []
    org_data = fetch_user_organization_data(token, owner=getattr(user, "username", None))
    if org_data is None:
        # A network blip is not a revoked permission, so say nothing is known
        # rather than reporting a permission fact we have not established.
        return None
    return org_data.get("organizations") or []


def caller_org_ids(access) -> set[int] | None:
    """The integer organisation ids the caller belongs to, or None."""
    organizations = _caller_organizations(access)
    return None if organizations is None else _integer_org_ids(organizations)


def dimagi_org():
    """Dimagi, as one organisation across all of labs.

    One row, not one per programme. The previous version created a
    `programme_org` row inside each programme -- so an importer invented a
    "Programme team" record for an organisation that plainly exists in
    Connect, which is the second-registry behaviour this work exists to
    remove. Dimagi is Dimagi in every programme it appears in.

    Created on first use rather than by a setup step, because an
    organisation labs already acts as is not something to wait for.
    """
    org, _ = LabsOrg.objects.get_or_create(slug=DIMAGI_ORG_SLUG, defaults={"name": "Dimagi", "short_name": "Dimagi"})
    return org


def resolve_org(access):
    """The organisation this caller acts for.

    Two rules, and no third for demo data:

      1. Dimagi staff act for Dimagi -- the ACL
         `SyntheticOpportunity.is_accessible_to` already grants them as
         platform operators, so their membership list is not the question.
      2. Anyone else acts for the organisation they belong to, matched on
         `LabsOrg`'s own rule: the Connect id where there is one, the slug
         where there is not.

    The slug half matters. A labs-only organisation has no integer id, so an
    earlier version could never match one and no number of local rows would
    have fixed it.
    """
    user = getattr(access, "user", None)
    if user is not None and is_dimagi_user(user):
        return dimagi_org()

    organizations = _caller_organizations(access)
    if not organizations:
        return None

    # Each entry's id and slug describe ONE organisation, so they are tested
    # together. Tested independently, a caller's organisation 999 could match
    # a linked row through some OTHER organisation's stale slug, and the
    # record would be attributed to a body the caller has nothing to do with.
    pairs = [(_as_org_id(entry.get("id")), entry.get("slug")) for entry in organizations]
    pairs = [(i, s) for i, s in pairs if i is not None or s]
    if not pairs:
        return None

    candidates = [
        org for org in LabsOrg.objects.all() if any(org.matches(organization_id=i, slug=s) for i, s in pairs)
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    raise IdentityUnresolved(
        "this caller belongs to more than one organisation acting here "
        f"({', '.join(sorted(o.slug for o in candidates))}), so one cannot be chosen "
        "for them. Pass recorded_by_org_id to say which."
    )


# Sources that assert first-hand knowledge. Only the programme's own staff and
# a document can carry them; a partner claiming `we_recorded` would be
# claiming that WE witnessed what they are telling us.
WITNESSED_SOURCES = {"we_recorded", "document"}


def takes_provenance(operation) -> bool:
    """Whether this operation writes a row that records who said so.

    Read off the schema rather than listed here, so an operation added later
    is covered without anyone remembering to add it. Provenance is compulsory
    below the contract (section 17.3), so procurement writes -- suppliers,
    tenders, quotes, outreach -- are untouched by any of this.
    """
    if not operation.is_write:
        return False
    data = (operation.input_schema.get("properties") or {}).get("data") or {}
    return "recorded_by_org_id" in (data.get("properties") or {})


def stamp_provenance(access, operation, payload: dict) -> dict:
    """Fill in who recorded this, and refuse a claim the caller cannot make.

    Returns the payload to dispatch. Three cases, and the distinction between
    the last two is the whole point:

      - the operation records no provenance: untouched.
      - the caller is UNKNOWABLE (a management command, no user, nobody to
        ask): untouched. Such a caller must declare its own organisation, and the
        commands that write provenance already do. Refusing here would turn a
        missing argument into a permission error.
      - the caller is KNOWABLE: the row is attributed to the organisation it
        acts for, and a claim it is not entitled to make is refused.

    The asymmetry between us and a partner is deliberate and is not a
    privilege: we record a partner's receipt on their behalf routinely -- that
    is what `partner_reported` is for -- so the programme's own staff may
    attribute a row to another organisation. A partner may not, because a partner
    attributing a row to us would make its own claim read as first-hand, which
    is the substitution provenance exists to prevent.
    """
    if not takes_provenance(operation):
        return payload

    # Only a caller with NO USER AT ALL is left to declare its own organisation --
    # a management command, run by an operator with a shell. A signed-in
    # caller whose organisations could not be read is refused.
    #
    # These were conflated: `caller_org_ids` returns None both for "nobody to
    # ask" and for "the org fetch failed", so a network blip made a signed-in
    # user unknowable and their payload-supplied `recorded_by_org_id` and
    # `source` were taken at face value. That is self-attribution, which is
    # the one thing this function exists to stop.
    if getattr(access, "user", None) is None and getattr(access, "request", None) is None:
        return payload

    data = payload.get("data")
    if not isinstance(data, dict):
        return payload

    org = resolve_org(access)
    if org is None:
        raise IdentityUnresolved(
            "this caller acts for no organisation in this programme, so a record "
            "cannot be attributed to anyone. Add the organisation with org_upsert and "
            "set its connect_organization_id, or call as a member of one."
        )

    claimed_org_id = data.get("recorded_by_org_id")
    claimed_source = data.get("source")
    # Ours, rather than "an organisation whose kind says programme". The
    # distinction is the whole correction: being the programme is a fact
    # about who is acting here, not a property the organisation carries into
    # every other programme it appears in.
    is_programme = org.slug == DIMAGI_ORG_SLUG

    if claimed_org_id is not None and int(claimed_org_id) != org.pk and not is_programme:
        raise IdentityUnresolved(
            f"{org.name} cannot record this against another organisation. Omit "
            "recorded_by_org_id and it will be attributed to you."
        )
    if claimed_source in WITNESSED_SOURCES and not is_programme:
        raise IdentityUnresolved(
            f"{org.name} cannot record {claimed_source!r}, which asserts first-hand "
            "knowledge. Use 'partner_reported', or attach a document."
        )

    data = dict(data)
    data.setdefault("recorded_by_org_id", org.pk)
    derived = source_for(org)
    if derived is not None:
        data.setdefault("source", derived)
    return {**payload, "data": data}


def find_or_mint_supplier_org(name: str, *, country: str = "", connect_organization_id=None):
    """The company a supplier's name and Connect id describe, minting one if none.

    Resolved the way `LabsOrg` says identity resolves: the Connect id first,
    because it is the identity; then the one organisation with exactly this
    name; then the organisation the name's slug already belongs to. Failing
    all three, a new organisation -- "local for good" in the procurement
    design's words: a manufacturer that quotes us has no reason to be a
    Connect org, and is a complete organisation without one. A minted
    organisation carries a Connect id only when the caller gave one.
    """
    from connect_labs.marketplace.identity import find_org, mint_org, slug_for

    if connect_organization_id:
        linked = LabsOrg.objects.filter(connect_organization_id=connect_organization_id).first()
        if linked is not None:
            return linked
    if not (name or "").strip():
        raise ValueError("a supplier needs a name, or the id of the organisation it is")
    # The same name exactly (ignoring case) is the same company -- an org
    # recorded as "Harmattan Health Supplies" under the slug `harmattan` is the
    # supplier of that name, not a second one. Only an exact name, and only
    # when it picks out one organisation: "Nutriset" and "Nutriset Nigeria"
    # stay two. When two organisations share the name, the one holding the
    # name's own slug is the one this name has always resolved to.
    named = list(LabsOrg.objects.filter(name__iexact=name.strip())[:2])
    org = named[0] if len(named) == 1 else find_org(name)
    if org is None:
        # A name somebody merged away. `org_merge` keeps the merged row's slug
        # as an alias on the survivor precisely so a lookup by that name still
        # finds it -- and a re-run of the tracker import is exactly that
        # lookup. Without this it minted the duplicate straight back.
        aliased = list(LabsOrg.objects.filter(aliases__contains=[slug_for(name)])[:2])
        if len(aliased) == 1:
            org = aliased[0]
    if org is None:
        org = mint_org(name, country=country)
    if connect_organization_id and org.connect_organization_id not in (None, connect_organization_id):
        # Same name, different Connect identity: two organisations, and
        # which one was meant is not ours to guess.
        raise ValueError(
            f"“{org.name}” is already Connect organisation {org.connect_organization_id}, "
            f"not {connect_organization_id} -- name the organisation by its id instead"
        )
    if connect_organization_id and org.connect_organization_id is None:
        org.connect_organization_id = connect_organization_id
        org.save(update_fields=["connect_organization_id", "updated_at"])
    return org
