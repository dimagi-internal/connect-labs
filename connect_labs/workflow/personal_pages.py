"""Personal pages: a page one person owns, shared with the people and scopes they choose.

A page (workflow/page_mode.py) otherwise lives in an organisation, a programme or an
opportunity, and who may open or change it is that scope's rule. A personal page has
no such scope: a launcher of links to other labs pages, say, belongs to the person who
made it. So:

* **Address.** ``/labs/p/user/<username>/<slug>/`` -- the one you share.
  ``/labs/p/me/`` lists your own and ``/labs/p/me/<slug>/`` goes to your page's address.
* **Where it lives.** A ``PersonalPage`` row (owner, slug, share list) is the tenant;
  the definition and its render code are ``LabsLocalRecord`` rows filed under
  ``personal_scope_id(row.pk)`` as the opportunity id, exactly as a CommCare project's
  workflow records are (hqdata/workflows.py), so the workflow engine reads and writes
  them unchanged. Nothing is asked of Connect.
* **Who may open it** is decided from the row alone, BEFORE the definition is read:
  its owner, Dimagi staff (hand-granted ``is_staff``, the rule every page uses for a
  page whose owning scope cannot be told), anyone named in ``shared_people`` (their
  username or email), and anyone who may use one of ``shared_scopes``
  (``scope_config.service.refusal``, the rule Settings and pages use for that scope).
  The scope ``dimagi`` (``DIMAGI_SHARE``) is everyone who signs in with a Dimagi
  address (``utils.dimagi_user.DIMAGI_DOMAINS``), the one rule labs uses for who is
  Dimagi, so a page can be opened by all of Dimagi without its owner being a member
  of any real organisation.
  Everyone else gets a 404, as for a page in a scope they are not in.
* **Who may change it** (its code, name, address, sharing): its owner and staff.
* **What it reads.** Nothing. It is drawn in the page sandbox with every data endpoint
  closed (``page_mode.closed_endpoints``), and its ``scope`` prop is the owner (type
  ``"user"``) and the viewer, with no organisation, programme or opportunity. The links
  in it go to other pages, which check their own access.

The local record store checks nothing itself (the scope is the tenant boundary), so a
personal scope is opened only through ``personal_dao`` here and re-checked where a data
access object is built (``BaseDataAccess._open_personal_scope``), which also makes the
records read-only for anyone who may not change the page. A personal scope is never a
labs context: the middleware refuses one in a URL or the session
(``labs.synthetic.access.labs_only_scope_denied_reason``).
"""

from __future__ import annotations

from urllib.parse import quote

from django.db import transaction

#: Personal scope ids are ``-(PERSONAL_SCOPE_FLOOR + pk)``: in (-10**9, -5 * 10**8], so they
#: meet no Connect or labs-only opportunity (positive), no programme-scoped Drive source
#: (``-program_id``, far smaller) and no CommCare project scope (``<= -10**9``).
PERSONAL_SCOPE_FLOOR = 5 * 10**8

MAX_SHARES = 200
MAX_NAME = 120

#: How a share names a scope, and the scope type it means.
SCOPE_WORDS = {
    "organization": "organization",
    "organisation": "organization",
    "org": "organization",
    "program": "program",
    "programme": "program",
    "opportunity": "opportunity",
}

#: The share that opens a page to everyone who signs in with a Dimagi address (`DIMAGI_DOMAINS`).
DIMAGI_SHARE = "dimagi"

NOT_SHARED = "this page is not shared with you"
OWNER_ONLY = "only the page's owner can change it"

STARTER_RENDER_CODE = r"""function WorkflowUI({ definition, scope }) {
  // A personal page (workflow/personal_pages.py): it reads no data of its own.
  // Link to other labs pages here; each checks its own access.
  var owner = (scope && scope.key) || '';
  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold text-brand-indigo">{(definition && definition.name) || 'Page'}</h1>
      <p className="text-sm text-gray-600 mt-1">A page of {owner}'s. Nothing here yet.</p>
    </div>
  );
}
"""


class PageNotFound(LookupError):
    """No such page, or not one the person may open: the same answer for both."""


class PageProblem(ValueError):
    """A change that cannot be made as asked; says why."""


# -- scope ids ---------------------------------------------------------------------------


def personal_scope_id(page_pk) -> int:
    return -(PERSONAL_SCOPE_FLOOR + int(page_pk))


def is_personal_scope(scope_id) -> bool:
    """True for a personal scope id. Takes whatever a caller holds: MCP and URL arguments arrive as strings."""
    from connect_labs.hqdata.scope import HQ_SCOPE_FLOOR

    try:
        value = int(scope_id)
    except (TypeError, ValueError):
        return False
    return -HQ_SCOPE_FLOOR < value <= -PERSONAL_SCOPE_FLOOR


def page_of_scope(scope_id):
    """The ``PersonalPage`` a personal scope id names, or None."""
    from connect_labs.workflow.models import PersonalPage

    if not is_personal_scope(scope_id):
        return None
    return PersonalPage.objects.select_related("owner").filter(pk=-int(scope_id) - PERSONAL_SCOPE_FLOOR).first()


# -- who may do what ---------------------------------------------------------------------------


def _signed_in(user) -> bool:
    return user is not None and getattr(user, "is_authenticated", False)


def edit_refusal(user, page) -> str | None:
    """Why ``user`` may not change ``page`` (code, name, address, sharing), or None."""
    if not _signed_in(user):
        return "sign in to change a page"
    if page.owner_id == user.pk or getattr(user, "is_staff", False):
        return None
    return OWNER_ONLY


def _names(user) -> set[str]:
    return {str(getattr(user, k, "") or "").strip().lower() for k in ("username", "email")} - {""}


def view_refusal(user, page, *, request=None, access_token=None) -> str | None:
    """Why ``user`` may not open ``page``, or None. Reads only the row, never the definition."""
    if not _signed_in(user):
        return "sign in to open a page"
    if edit_refusal(user, page) is None:
        return None
    return _shared_refusal(user, page, request=request, access_token=access_token)


def _shared_refusal(user, page, *, request=None, access_token=None) -> str | None:
    """None if ``page`` was shared with ``user``, by name or through a scope they may use."""
    if _names(user) & {str(p).lower() for p in page.shared_people or []}:
        return None
    if DIMAGI_SHARE in (page.shared_scopes or []):
        from connect_labs.utils.dimagi_user import is_dimagi_user

        if is_dimagi_user(user):
            return None
    if page.shared_scopes:
        from connect_labs.labs.access.scopes import Caller
        from connect_labs.scope_config import service

        caller = Caller(user=user, request=request, access_token=access_token)
        for token in page.shared_scopes:
            if token == DIMAGI_SHARE:
                continue
            try:
                scope = parse_scope(token)
            except PageProblem:
                continue
            if service.refusal(caller, scope) is None:
                return None
    return NOT_SHARED


def parse_scope(token):
    """``"program:25"`` (or ``programme:25``, ``opportunity:10133``, ``org:acme``) as a Scope."""
    from connect_labs.scope_config.scopes import Scope

    word, _, key = str(token or "").partition(":")
    kind = SCOPE_WORDS.get(word.strip().lower())
    if kind is None or not key.strip():
        raise PageProblem(
            f"{token!r} is not a scope: write program:<id>, opportunity:<id>, organization:<slug> or {DIMAGI_SHARE}"
        )
    try:
        return Scope.of(kind, key.strip())
    except ValueError as exc:
        raise PageProblem(str(exc)) from None


# -- reading ----------------------------------------------------------------------------------


def url(page) -> str:
    return f"/labs/p/user/{quote(page.owner.username, safe='@.+-_')}/{page.slug}/"


def find(username: str, slug: str):
    from connect_labs.workflow.models import PersonalPage

    return PersonalPage.objects.select_related("owner").filter(owner__username=username, slug=slug).first()


def owned_by(user) -> list:
    from connect_labs.workflow.models import PersonalPage

    if not _signed_in(user):
        return []
    return list(PersonalPage.objects.select_related("owner").filter(owner=user).order_by("slug"))


def shared_with(user, *, request=None, access_token=None) -> list:
    """Other people's pages ``user`` may open because they were shared with them (not staff's reach)."""
    from connect_labs.workflow.models import PersonalPage

    if not _signed_in(user):
        return []
    out = []
    candidates = PersonalPage.objects.select_related("owner").exclude(owner=user).order_by("owner__username", "slug")
    for page in candidates:
        if (page.shared_people or page.shared_scopes) and _shared_refusal(
            user, page, request=request, access_token=access_token
        ) is None:
            out.append(page)
    return out


def personal_dao(user, page, *, request=None, access_token=None):
    """A data access object over ``page``'s records, for someone who may open it; read-only unless they may change it.

    The front door: never build one from a labs context or a URL.
    """
    from connect_labs.workflow.data_access import WorkflowDataAccess

    if view_refusal(user, page, request=request, access_token=access_token):
        raise PageNotFound("No such page.")
    return WorkflowDataAccess(
        opportunity_id=personal_scope_id(page.pk), user=user, request=request, access_token=access_token
    )


def load(user, page, *, request=None, access_token=None):
    """``(definition, render_code)`` of ``page`` for someone who may open it, else None."""
    from connect_labs.workflow.page_mode import is_page

    if page.definition_id is None:
        return None
    dao = personal_dao(user, page, request=request, access_token=access_token)
    try:
        definition = dao.get_definition(page.definition_id)
        if definition is None or not is_page(definition):
            return None
        record = dao.get_render_code(definition.id)
        return definition, ((record.data or {}).get("component_code") if record else "") or ""
    finally:
        dao.close()


def summary(page, definition=None) -> dict:
    data = (definition.data or {}) if definition is not None else {}
    return {
        "owner": page.owner.username,
        "slug": page.slug,
        "url": url(page),
        "workflow_id": page.definition_id,
        "scope_id": personal_scope_id(page.pk),
        "name": data.get("name"),
        "shared_people": list(page.shared_people or []),
        "shared_scopes": list(page.shared_scopes or []),
    }


def workflow_data(request, page, definition, render_code: str) -> dict:
    """The ``workflow-data`` the runner reads to draw ``page``: sandboxed, every data endpoint closed."""
    from django.conf import settings

    from connect_labs.workflow.page_mode import blank_instance, closed_endpoints, scope_prop
    from connect_labs.workflow.sandbox import host_payload

    return {
        "definition": definition.data,
        "definition_id": definition.id,
        "opportunity_id": None,
        "program_id": None,
        "program_scoped": False,
        "opportunity_ids": [],
        "multi_opp": False,
        "render_code": render_code,
        "instance": blank_instance(definition.id, None, None, []),
        "is_edit_mode": False,
        "is_page": True,
        "workers": [],
        "pipeline_data": {},
        "flags": [],
        "audits": [],
        "tasks": [],
        "links": {"auditUrlBase": "", "taskUrlBase": ""},
        "apiEndpoints": closed_endpoints(),
        "viewer_is_staff": False,
        # Whose page it is, and who is looking: no organisation, programme or opportunity.
        "scope": scope_prop("user", page.owner.username, request.user),
        "config": {},
        "sandbox": {**host_payload(request), "readable_definitions": []},
        "mapboxToken": settings.MAPBOX_TOKEN or "",
    }


# -- changing ---------------------------------------------------------------------------------


def _require_edit(user, page) -> None:
    reason = edit_refusal(user, page)
    if reason:
        raise PermissionError(reason)


def _clean_name(name) -> str:
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > MAX_NAME:
        raise PageProblem(f"a page needs a title of at most {MAX_NAME} characters")
    return name


def _clean_slug(owner, slug, *, page=None) -> str:
    from connect_labs.workflow.models import PersonalPage
    from connect_labs.workflow.page_manager import slug_problem

    slug = slug.strip() if isinstance(slug, str) else slug
    problem = slug_problem(slug)
    if problem:
        raise PageProblem(problem.replace("/labs/p/<scope>/<key>/", "/labs/p/me/"))
    taken = PersonalPage.objects.filter(owner=owner, slug=slug)
    if page is not None:
        taken = taken.exclude(pk=page.pk)
    if taken.exists():
        raise PageProblem(f"you already have a page at /{slug}/: choose another address")
    return slug


def _check_render_code(render_code) -> str:
    from connect_labs.workflow.render_code_lint import render_code_problem

    problem = render_code_problem(render_code)
    if problem:
        raise PageProblem(problem)
    return render_code


def create(user, *, slug, name, description="", render_code=None, request=None, access_token=None):
    """A new personal page owned by ``user``: unshared, drawn by ``render_code`` (or a starter)."""
    from connect_labs.workflow.models import PersonalPage

    if not _signed_in(user):
        raise PermissionError("sign in to make a page")
    name = _clean_name(name)
    slug = _clean_slug(user, slug)
    code = _check_render_code(render_code) if render_code is not None else STARTER_RENDER_CODE
    with transaction.atomic():
        page = PersonalPage.objects.create(owner=user, slug=slug)
        dao = personal_dao(user, page, request=request, access_token=access_token)
        try:
            definition = dao.create_definition(
                name,
                str(description or "")[:2000],
                kind="page",
                page={"slug": slug},
                statuses=[],
                config={
                    "templateType": "page_blank",
                    "showFilters": False,
                    "showSummaryCards": False,
                    "renderWhileLoading": True,
                    "noPipelineStream": True,
                },
                pipeline_sources=[],
                opportunity_ids=[],
            )
            dao.save_render_code(definition.id, code)
        finally:
            dao.close()
        page.definition_id = definition.id
        page.save(update_fields=["definition_id", "updated_at"])
    return page


def update(user, page, *, name=None, description=None, slug=None, render_code=None, request=None, access_token=None):
    """Change a page's title, description, address or render code. ``None`` leaves a field alone."""
    _require_edit(user, page)
    name = _clean_name(name) if name is not None else None
    slug = _clean_slug(page.owner, slug, page=page) if slug is not None else None
    code = _check_render_code(render_code) if render_code is not None else None
    if page.definition_id is None:
        raise PageNotFound("This page has no definition.")
    with transaction.atomic():
        dao = personal_dao(user, page, request=request, access_token=access_token)
        try:
            definition = dao.get_definition(page.definition_id)
            if definition is None:
                raise PageNotFound("This page has no definition.")
            data = dict(definition.data or {})
            if name is not None:
                data["name"] = name
            if description is not None:
                data["description"] = str(description)[:2000]
            if slug is not None:
                data["page"] = {**(data.get("page") or {}), "slug": slug}
            if name is not None or description is not None or slug is not None:
                dao.update_definition(definition.id, data)
            if code is not None:
                stored = dao.get_render_code(definition.id)
                dao.save_render_code(definition.id, code, version=(stored.version + 1) if stored else 1)
        finally:
            dao.close()
        if slug is not None:
            page.slug = slug
            page.save(update_fields=["slug", "updated_at"])
    return page


def _clean_people(people) -> list[str]:
    if not isinstance(people, list | tuple):
        raise PageProblem("people is a list of usernames or emails")
    out = []
    for entry in people:
        value = entry.strip().lower() if isinstance(entry, str) else ""
        if not value or len(value) > 254 or any(c.isspace() for c in value):
            raise PageProblem(f"{entry!r} is not a username or an email")
        if value not in out:
            out.append(value)
    return out


def set_sharing(user, page, *, people=None, scopes=None, request=None, access_token=None):
    """Replace who ``page`` is shared with. ``None`` leaves that list alone; ``[]`` empties it.

    A scope newly shared with must be one the person sharing may use (the rule Settings
    uses for the scopes a change newly points at): nobody files their page into a
    programme or organisation they are not in. ``dimagi`` (``DIMAGI_SHARE``) needs no
    membership: it is everyone with a Dimagi sign-in, whoever shares with it.
    """
    from connect_labs.labs.access.scopes import Caller
    from connect_labs.scope_config import service

    _require_edit(user, page)
    new_people = _clean_people(people) if people is not None else None
    new_scopes = None
    if scopes is not None:
        if not isinstance(scopes, list | tuple):
            raise PageProblem("scopes is a list like ['program:25', 'opportunity:10133', 'organization:acme']")
        new_scopes = []
        for token in scopes:
            if isinstance(token, str) and token.strip().lower() == DIMAGI_SHARE:
                canonical = DIMAGI_SHARE
            else:
                canonical = str(parse_scope(token))
            if canonical not in new_scopes:
                new_scopes.append(canonical)
        caller = Caller(user=user, request=request, access_token=access_token)
        for canonical in set(new_scopes) - set(page.shared_scopes or []) - {DIMAGI_SHARE}:
            reason = service.refusal(caller, parse_scope(canonical))
            if reason:
                raise PermissionError(f"cannot share with {canonical}: {reason}")
    if (
        len(new_people if new_people is not None else page.shared_people or [])
        + len(new_scopes if new_scopes is not None else page.shared_scopes or [])
        > MAX_SHARES
    ):
        raise PageProblem(f"a page is shared with at most {MAX_SHARES} people and scopes")
    if new_people is not None:
        page.shared_people = new_people
    if new_scopes is not None:
        page.shared_scopes = new_scopes
    page.save(update_fields=["shared_people", "shared_scopes", "updated_at"])
    return page


def delete(user, page, *, request=None, access_token=None) -> None:
    """Delete a page and its records (a backup of the definition and code is kept, as for any workflow)."""
    _require_edit(user, page)
    with transaction.atomic():
        if page.definition_id is not None:
            dao = personal_dao(user, page, request=request, access_token=access_token)
            try:
                if dao.get_definition(page.definition_id) is not None:
                    dao.delete_definition(page.definition_id)
            finally:
                dao.close()
        page.delete()
