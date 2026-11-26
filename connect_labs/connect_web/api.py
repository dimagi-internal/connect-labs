"""The JSON API the form player (``static/connect_web``) calls: a proxy to Formplayer, scoped on the server.

The browser names only the opportunity and the app kind (in the URL) and what the person did (a
menu path, an answer). Everything that decides *whose* data and *which* app — the HQ username, the
domain, the build id, the HQ token — comes from the checked-out worker's session through
`formplayer.target_for` (the worker's own opportunity list; a deliver app needs a claim). A
``username``, ``domain``, ``app_id`` or ``restoreAs`` in a request body is ignored.

A form session id is accepted only if labs opened it for this checkout (the labs user's
`WorkerSession`, so the same worker), opportunity and app — recorded in `models.FormSession` when
a navigation opens a form — so one opportunity's player can't drive another's form, and a second
tab can't lose the first tab's form (the Django session, where this used to live, is re-saved
whole by every request).

Every endpoint is a CSRF-protected POST (the progress read is a GET), returns JSON, and answers a
failure as ``{"error": {"code", "message", "retryable"}}`` with an HTTP status that says who is
at fault — never a token, a body from HQ that labs didn't choose to show, or a stack trace.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from . import formplayer, service
from .access import can_use
from .errors import (
    ENGINE_AUTH_FAILED,
    ENGINE_BUSY,
    ENGINE_ERROR,
    FAILED_USER_CREATE,
    NO_RELEASED_BUILD,
    SESSION_EXPIRED,
    SESSION_UNKNOWN,
    ConnectWebError,
    FormplayerError,
    GuardRefused,
    HQError,
    InvalidInput,
    NotConfigured,
    ServiceError,
    WorkerUnavailable,
    user_message,
)
from .models import FormSession

logger = logging.getLogger(__name__)

#: A JSON request body larger than this is refused before parsing (a form's answers are small).
MAX_JSON_BODY = 2 * 1024 * 1024
#: Room for a multipart upload's other parts and boundaries, on top of the photo itself.
MULTIPART_OVERHEAD = 64 * 1024
#: How long labs remembers that it opened a form session.
FORM_SESSION_TTL = timedelta(hours=12)


def error_response(code: str, message: str, status: int, *, retryable: bool = False) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message, "retryable": retryable}}, status=status)


def _status_for(exc: ConnectWebError) -> tuple[int, bool]:
    """(HTTP status, retryable) for a refused or failed call."""
    if exc.error_code in (SESSION_EXPIRED, SESSION_UNKNOWN):
        return 410, False
    if isinstance(exc, InvalidInput):
        return 400, False
    if isinstance(exc, NotConfigured):
        return 503, False
    if isinstance(exc, GuardRefused | WorkerUnavailable):
        return 409, False
    if exc.error_code == FAILED_USER_CREATE:
        return 502, False  # fails the same way until HQ is fixed
    if isinstance(exc, FormplayerError):
        if exc.error_code == ENGINE_BUSY:
            return 503, True
        if exc.error_code == ENGINE_AUTH_FAILED:
            return 502, False
        return 502, exc.status is None or exc.status >= 500
    if isinstance(exc, ServiceError):
        return 502, exc.status is None or exc.status >= 500
    if exc.error_code == "NO_OPPORTUNITY_ACCESS":
        return 403, False
    return 400, False


_csrf = CsrfViewMiddleware(lambda request: None)


class _JSONView(View):
    """Signed in and allowed (as JSON, not a redirect), with the user's open worker session.

    CSRF is checked here rather than by the middleware, so a request too large to accept is
    refused from its declared length *before* anything reads the body (the middleware reads a
    multipart POST — spooling its upload to disk — to look for a form token).
    """

    @classmethod
    def as_view(cls, **initkwargs):
        return csrf_exempt(super().as_view(**initkwargs))

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return error_response("SIGNED_OUT", "Sign in to labs again.", 401)
        if not can_use(request.user):
            return error_response("FORBIDDEN", "Connect Web isn't open to this account.", 403)
        if refused := self.refuse_early(request):
            return refused
        if request.method not in ("GET", "HEAD", "OPTIONS", "TRACE"):
            if _csrf.process_view(request, None, (), {}) is not None:
                return error_response("CSRF_FAILED", "Labs refused the request. Reload the page and try again.", 403)
        self.worker_session = service.current_session(request.user)
        if self.worker_session is None:
            return error_response("NO_WORKER", "Pick a worker to act as first.", 409)
        service.touch(self.worker_session)  # the player in use keeps the checkout from looking idle
        try:
            return super().dispatch(request, *args, **kwargs)
        except PermissionDenied:
            return error_response("FORBIDDEN", "Connect Web isn't open to this account.", 403)
        except ConnectWebError as exc:
            status, retryable = _status_for(exc)
            logger.info("connect_web api %s failed: %s", type(self).__name__, exc)
            return error_response(exc.error_code or type(exc).__name__, user_message(exc), status, retryable=retryable)

    def refuse_early(self, request):
        """A response that refuses the request before its body is read, or None."""
        return None


class ProgressView(_JSONView):
    """Learn and delivery progress, polled by the opportunity page after a submission.

    ``?kind=learn|deliver`` (what the poller is waiting for) reads only that kind, fresh: one
    Connect call, the list coming from the cache. The other kind is ``null``. Without ``kind``,
    both (as the opportunity page has them).
    """

    http_method_names = ["get"]

    def get(self, request, opportunity_id):
        kind = request.GET.get("kind")
        if kind in service.APP_KINDS:
            data = service.progress(self.worker_session, opportunity_id, kind)
            claimed = data["claimed"]
            return JsonResponse(
                {
                    "claimed": claimed,
                    "learn": _learn_progress(data["learn"]) if kind == "learn" else None,
                    "deliver": _deliver_progress(data["delivery"]) if kind == "deliver" else None,
                }
            )
        detail = service.opportunity_detail(self.worker_session, opportunity_id)
        return JsonResponse(progress_of(detail))


def _learn_progress(learn: dict | None) -> dict | None:
    if learn is None:
        return None
    latest = learn.get("latest_assessment") or {}
    return {
        "modules_done": learn["modules_done"],
        "modules_total": learn["modules_total"],
        "assessments": len(learn.get("assessments") or []),
        "latest_score": latest.get("score"),
        "passed": learn["passed"],
        "complete": learn["complete"],
    }


def _deliver_progress(delivery: dict | None) -> dict:
    deliveries = (delivery or {}).get("deliveries") or []
    statuses: dict[str, int] = {}
    for d in deliveries:
        key = str(d.get("status") or "pending")
        statuses[key] = statuses.get(key, 0) + 1
    return {
        "visits": len(deliveries),
        "statuses": statuses,
        "accrued": (delivery or {}).get("payment_accrued"),
    }


def progress_of(detail: dict) -> dict:
    return {
        "claimed": detail["claimed"],
        "learn": _learn_progress(detail["learn"]),
        "deliver": _deliver_progress(detail.get("delivery")),
    }


class _EngineView(_JSONView):
    """One Formplayer endpoint for the worker's opportunity and app kind."""

    http_method_names = ["post"]
    #: The client may send a body of this kind: "json" or "multipart".
    body_kind = "json"

    def post(self, request, opportunity_id, app_kind):
        if app_kind not in service.APP_KINDS:
            return error_response("NOT_FOUND", "Unknown app.", 404)
        formplayer.require_configured()
        data = self._body(request)
        tz_offset, tz_name = formplayer.clean_timezone(data.get("tz_offset_millis"), data.get("tz_from_browser"))
        try:
            target = formplayer.target_for(self.worker_session, opportunity_id, app_kind)
        except HQError as exc:
            if exc.error_code == NO_RELEASED_BUILD:  # nothing cached but the 30 s negative build entry
                formplayer.forget_target(self.worker_session, opportunity_id, app_kind)
            raise
        fp = formplayer.client_for(self.worker_session, target, tz_offset_millis=tz_offset, tz_from_browser=tz_name)
        try:
            result = self.act(fp, data)
        except FormplayerError as exc:
            if exc.error_code == ENGINE_ERROR:
                # The app may have a new release (or the target be stale): re-resolve next time.
                formplayer.forget_target(self.worker_session, opportunity_id, app_kind, target)
            raise
        self._remember(result, target)
        return JsonResponse(result)

    def act(self, fp: formplayer.Formplayer, data: dict) -> dict:
        raise NotImplementedError

    def _limit(self) -> int:
        if self.body_kind == "multipart":
            return formplayer.media_max_bytes() + MULTIPART_OVERHEAD
        return MAX_JSON_BODY

    def refuse_early(self, request):
        """413 from the declared length, before the body is read or an upload spooled to disk."""
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            return error_response("INVALID_INPUT", "The request's length isn't valid.", 400)
        if length > self._limit():
            what = "photo" if self.body_kind == "multipart" else "request"
            return error_response("TOO_LARGE", f"The {what} is too large.", 413)
        return None

    def _body(self, request) -> dict:
        if self.body_kind == "multipart":
            return request.POST.dict()
        if len(request.body) > MAX_JSON_BODY:
            raise InvalidInput("The request is too large.")
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            raise InvalidInput("The request isn't valid JSON.") from None
        if not isinstance(data, dict):
            raise InvalidInput("The request isn't valid JSON.")
        return data

    # -- form sessions labs opened ------------------------------------------------------------
    def _remember(self, result: dict, target: formplayer.AppTarget) -> None:
        """Record a form session a navigation opened (one row per id; the first scope wins)."""
        session_id = result.get("session_id") if isinstance(result, dict) else None
        if not (isinstance(session_id, str) and formplayer.SESSION_ID_RE.match(session_id)):
            return
        if not isinstance(self, NavigateView):
            return  # only a navigation opens a form; other answers just echo the id
        FormSession.objects.bulk_create(
            [
                FormSession(
                    worker_session=self.worker_session,
                    session_id=session_id,
                    opportunity_id=target.opportunity_id,
                    app_kind=target.app_kind,
                )
            ],
            ignore_conflicts=True,
        )
        FormSession.objects.filter(
            worker_session=self.worker_session, created_at__lt=timezone.now() - FORM_SESSION_TTL
        ).delete()

    def form_session(self, fp: formplayer.Formplayer, data: dict) -> str:
        """The request's form session id, if labs opened it for this checkout, opportunity and app."""
        session_id = formplayer.clean_session_id(data.get("session_id"))
        known = FormSession.objects.filter(
            worker_session=self.worker_session,
            session_id=session_id,
            opportunity_id=fp.target.opportunity_id,
            app_kind=fp.target.app_kind,
            created_at__gte=timezone.now() - FORM_SESSION_TTL,
        ).exists()
        if not known:
            raise InvalidInput("Start the form again.", error_code=SESSION_UNKNOWN)
        return session_id


class NavigateView(_EngineView):
    def act(self, fp, data):
        selections = formplayer.clean_selections(data.get("selections"))
        if data.get("start") is True:
            # The player's first navigation after a page load: resync stale case data first.
            formplayer.ensure_fresh(fp, self.worker_session)
        return formplayer.navigate(
            fp,
            selections,
            offset=formplayer.clean_int(data.get("offset"), low=0, high=formplayer.MAX_OFFSET, name="The page"),
            search_text=formplayer.clean_search(data.get("search_text")),
            cases_per_page=formplayer.clean_int(
                data.get("cases_per_page"), low=1, high=formplayer.MAX_CASES_PER_PAGE, name="The page size"
            ),
        )


class DetailsView(_EngineView):
    def act(self, fp, data):
        selections = formplayer.clean_selections(data.get("selections"))
        if not selections:
            raise InvalidInput("Pick a case first.")
        return formplayer.details(fp, selections)


class AnswerView(_EngineView):
    def act(self, fp, data):
        session_id = self.form_session(fp, data)
        ix = formplayer.clean_ix(data.get("ix"))
        return formplayer.answer(fp, session_id, ix, formplayer.clean_answer(data.get("answer")))


class AnswerMediaView(_EngineView):
    body_kind = "multipart"

    def act(self, fp, data):
        session_id = self.form_session(fp, data)
        ix = formplayer.clean_ix(data.get("ix"))
        upload = self.request.FILES.get("file")
        if upload is None:
            raise InvalidInput("Choose a photo first.")
        if upload.size > formplayer.media_max_bytes():
            raise InvalidInput(
                f"The photo is too large (the limit is {formplayer.media_max_bytes() // (1024 * 1024)} MB)."
            )
        content = upload.read(formplayer.media_max_bytes() + 1)
        return formplayer.answer_media(fp, session_id, ix, content=content)


class NewRepeatView(_EngineView):
    def act(self, fp, data):
        return formplayer.new_repeat(fp, self.form_session(fp, data), formplayer.clean_ix(data.get("ix")))


class DeleteRepeatView(_EngineView):
    def act(self, fp, data):
        return formplayer.delete_repeat(fp, self.form_session(fp, data), formplayer.clean_ix(data.get("ix")))


class ChangeLocaleView(_EngineView):
    def act(self, fp, data):
        return formplayer.change_locale(fp, self.form_session(fp, data), formplayer.clean_locale(data.get("locale")))


class SubmitView(_EngineView):
    def act(self, fp, data):
        session_id = self.form_session(fp, data)
        answers = formplayer.clean_answers(data.get("answers") or {})
        meta_location = formplayer.clean_meta_location(data.get("meta_location"))
        result = formplayer.submit(fp, session_id, answers, meta_location)
        if result.get("status") == "success":
            # Connect hears about the form from HQ later; progress is read back from Connect.
            logger.info(
                "connect_web: form submitted for opportunity %s (%s)", fp.target.opportunity_id, fp.target.app_kind
            )
            service.form_submitted(self.worker_session, fp.target.opportunity_id, fp.target.app_kind)
        return result


class SyncView(_EngineView):
    def act(self, fp, data):
        return formplayer.sync(fp, self.worker_session)
