"""As-of requests: a program page as it stood at the end of a past day.

`?as_of=YYYY-MM-DD` on any wrapped supply page renders that page inside a
transaction that is always rolled back, after `rewind` has undone every
revision the program recorded since the end of that day (design doc §3.5,
§4.4). Every existing view, operation and aggregate then reads the past
unmodified.

`as_of_view` is applied to the program-scoped routes by post-processing
`urlpatterns` in supply_chain/urls.py; the market, the login-free update link
and the API are left live.
"""

import datetime
from functools import wraps

from django.db import OperationalError, connection, transaction
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed
from django.utils import timezone

from connect_labs.supply_chain.history.rewind import rewind

READ_ONLY = "Viewing a past date is read-only."
NO_PAST_DOWNLOAD = "This download is not available for a past date."
BUSY = "This past view is busy — try again in a moment."

# A rewind takes row locks on everything the program changed since the date,
# and holds them while the page renders. Bounded, so a past view waits a
# moment for a live write rather than stalling it, and never runs long.
LOCK_TIMEOUT = "2s"
STATEMENT_TIMEOUT = "15s"
# lock_not_available, query_canceled (what statement_timeout raises).
_TIMEOUT_CODES = {"55P03", "57014"}


def parse_as_of(value: str | None) -> datetime.date | None:
    """`YYYY-MM-DD` or `15 Sep 2026` to a date; empty or missing means "today, live".

    Strict on shape: the date control always sends YYYY-MM-DD, so anything
    else is a hand-edited URL and a 400 says so rather than guessing.
    Raises ValueError on a malformed value.
    """
    if not value:
        return None
    value = " ".join(value.split())
    # The control speaks the page's own format ("15 Sep 2026"); links and older
    # bookmarks carry YYYY-MM-DD. Both are exact shapes, so neither is a guess.
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"not a day: {value!r}")


def end_of_day(d: datetime.date) -> datetime.datetime:
    """The last instant of `d` in the server timezone: "as of 20 Aug" includes all of 20 Aug."""
    return timezone.make_aware(datetime.datetime.combine(d, datetime.time.max))


def as_of_view(view_func):
    """Serve `view_func` as of `?as_of=`, or live without it.

    Sets `request.supply_as_of` (a date, or None) on every request it wraps,
    so a view or template can read it unconditionally.

    - No `as_of`: the view runs live.
    - Malformed, or after today: 400.
    - Anything but GET/HEAD: 405. A past date is read-only; the controls are
      hidden too, but this is the refusal that counts.
    - No program in the labs context: the view runs live and renders its own
      "choose a program" state.
    - Not signed in: the view runs live, with no past date, and does its own
      refusing (a login redirect). Nothing is rewound for an anonymous caller.
    - Signed in: the pages' own data access is built for the request first, so
      a caller who may not use this program gets the view's 403 BEFORE
      anything is rewound -- a rewind is writes, and takes row locks.
    - Otherwise: rewind, run the view, render the response -- all inside one
      atomic block that is always rolled back, including when the view raises.
      With ATOMIC_REQUESTS on, that block is a savepoint inside the request's
      transaction, and rolling it back undoes exactly the rewind. On Postgres
      the block sets a lock and a statement timeout (rolled back with it); a
      rewind or render that hits either is answered 503 "busy".

    Rendering happens inside the block because a TemplateResponse renders
    lazily; left to Django it would render after the rollback, against live
    rows. A streaming response (a file, a stream) cannot be rendered early,
    so under as_of it is refused with a 400 instead.

    Only the program's own records rewind: a reference row whose scope_key
    does not resolve to a program is never rewound and shows live.
    """

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        try:
            as_of = parse_as_of(request.GET.get("as_of"))
        except ValueError:
            return HttpResponseBadRequest("as_of must be a date written YYYY-MM-DD.")
        request.supply_as_of = as_of
        if as_of is None:
            return view_func(request, *args, **kwargs)
        if as_of > timezone.localdate():
            return HttpResponseBadRequest("as_of cannot be after today.")
        if request.method not in ("GET", "HEAD"):
            return HttpResponseNotAllowed(["GET", "HEAD"], content=READ_ONLY)

        program_id = (getattr(request, "labs_context", None) or {}).get("program_id")
        if not program_id:
            return view_func(request, *args, **kwargs)

        if not getattr(getattr(request, "user", None), "is_authenticated", False):
            request.supply_as_of = None
            return view_func(request, *args, **kwargs)
        authorise(request)

        try:
            return _rewound(request, view_func, int(program_id), as_of, args, kwargs)
        except OperationalError as error:
            if _code(error) not in _TIMEOUT_CODES:
                raise
            return HttpResponse(BUSY, status=503, content_type="text/plain; charset=utf-8")

    wrapped.supply_as_of_wrapped = True
    return wrapped


def authorise(request):
    """Build the supply pages' own data access for this request.

    Raises PermissionDenied (a 403, exactly as the page itself would answer)
    when the signed-in caller may not use the program in the labs context.
    """
    from connect_labs.supply_chain.api_views import _access

    _access(request)


def _code(error) -> str | None:
    cause = error.__cause__
    return getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)


def _rewound(request, view_func, program_id, as_of, args, kwargs):
    """Rewind, run the view and render it, inside a block that is always rolled back."""
    with transaction.atomic():
        try:
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                    cursor.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
            rewind(program_id, end_of_day(as_of))
            response = view_func(request, *args, **kwargs)
            if getattr(response, "streaming", False):
                # Its body is produced after we return -- after the
                # rollback, against live rows. Refuse rather than serve
                # today's data under a past date's banner. Not close()d:
                # that fires request_finished, which closes the DB
                # connection mid-request.
                response = HttpResponseBadRequest(NO_PAST_DOWNLOAD)
            elif hasattr(response, "render") and not response.is_rendered:
                response.render()
        finally:
            transaction.set_rollback(True)
    return response


def as_of_context(request):
    """Context processor: `supply_as_of` and `today` on wrapped supply requests only.

    Keyed off the attribute `as_of_view` sets, so every other page in labs
    gets nothing from this, and a supply page that is not wrapped (the
    market) shows no date control.
    """
    if not hasattr(request, "supply_as_of"):
        return {}
    return {
        "supply_as_of": request.supply_as_of,
        "supply_as_of_available": True,
        "today": timezone.localdate().isoformat(),
        "today_label": f"{timezone.localdate():%-d %b %Y}",
    }
