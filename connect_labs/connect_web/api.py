"""The JSON API the form player (``static/connect_web``) calls: a proxy to Formplayer, scoped on the server.

The browser names only the opportunity and the app kind (in the URL) and what the person did (a
menu path, an answer). Everything that decides *whose* data and *which* app — the HQ username, the
domain, the build id, the HQ token — comes from the checked-out worker's session through
`formplayer.target_for` (the worker's own opportunity list; a deliver app needs a claim). A
``username``, ``domain``, ``app_id`` or ``restoreAs`` in a request body is ignored.

A form session id is accepted only if labs opened it for this labs session, worker, opportunity
and app (recorded in the Django session when a navigation opens a form), so one opportunity's
player can't drive another's form.

Every endpoint is a CSRF-protected POST (the progress read is a GET), returns JSON, and answers a
failure as ``{"error": {"code", "message", "retryable"}}`` with an HTTP status that says who is
at fault — never a token, a body from HQ that labs didn't choose to show, or a stack trace.
"""

from __future__ import annotations

import json
import logging

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.views import View

from . import formplayer, service
from .access import can_use
from .errors import (
    ENGINE_AUTH_FAILED,
    ENGINE_BUSY,
    SESSION_EXPIRED,
    SESSION_UNKNOWN,
    ConnectWebError,
    FormplayerError,
    GuardRefused,
    InvalidInput,
    NotConfigured,
    ServiceError,
    WorkerUnavailable,
    user_message,
)

logger = logging.getLogger(__name__)

#: A JSON request body larger than this is refused before parsing (a form's answers are small).
MAX_JSON_BODY = 2 * 1024 * 1024
#: How many open form sessions labs remembers per labs session.
MAX_REMEMBERED_SESSIONS = 25
SESSIONS_KEY = "connect_web_form_sessions"


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


class _JSONView(View):
    """Signed in and allowed (as JSON, not a redirect), with the user's open worker session."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return error_response("SIGNED_OUT", "Sign in to labs again.", 401)
        if not can_use(request.user):
            return error_response("FORBIDDEN", "Connect Web isn't open to this account.", 403)
        self.worker_session = service.current_session(request.user)
        if self.worker_session is None:
            return error_response("NO_WORKER", "Pick a worker to act as first.", 409)
        try:
            return super().dispatch(request, *args, **kwargs)
        except PermissionDenied:
            return error_response("FORBIDDEN", "Connect Web isn't open to this account.", 403)
        except ConnectWebError as exc:
            status, retryable = _status_for(exc)
            logger.info("connect_web api %s failed: %s", type(self).__name__, exc)
            return error_response(exc.error_code or type(exc).__name__, user_message(exc), status, retryable=retryable)


class ProgressView(_JSONView):
    """Learn and delivery progress, polled by the opportunity page after a submission."""

    http_method_names = ["get"]

    def get(self, request, opportunity_id):
        detail = service.opportunity_detail(self.worker_session, opportunity_id)
        return JsonResponse(progress_of(detail))


def progress_of(detail: dict) -> dict:
    learn = detail["learn"]
    deliveries = detail.get("deliveries") or []
    statuses: dict[str, int] = {}
    for d in deliveries:
        key = str(d.get("status") or "pending")
        statuses[key] = statuses.get(key, 0) + 1
    latest = learn.get("latest_assessment") or {}
    return {
        "claimed": detail["claimed"],
        "learn": {
            "modules_done": learn["modules_done"],
            "modules_total": learn["modules_total"],
            "assessments": len(learn.get("assessments") or []),
            "latest_score": latest.get("score"),
            "passed": learn["passed"],
            "complete": learn["complete"],
        },
        "deliver": {
            "visits": len(deliveries),
            "statuses": statuses,
            "accrued": (detail.get("delivery") or {}).get("payment_accrued"),
        },
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
        target = formplayer.target_for(self.worker_session, opportunity_id, app_kind)
        fp = formplayer.client_for(self.worker_session, target, tz_offset_millis=tz_offset, tz_from_browser=tz_name)
        result = self.act(fp, data)
        self._remember(result, target)
        return JsonResponse(result)

    def act(self, fp: formplayer.Formplayer, data: dict) -> dict:
        raise NotImplementedError

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
    def _scope(self, target: formplayer.AppTarget) -> str:
        return f"{self.worker_session.pk}:{target.opportunity_id}:{target.app_kind}"

    def _remember(self, result: dict, target: formplayer.AppTarget) -> None:
        session_id = result.get("session_id") if isinstance(result, dict) else None
        if not (isinstance(session_id, str) and formplayer.SESSION_ID_RE.match(session_id)):
            return
        known = dict(self.request.session.get(SESSIONS_KEY) or {})
        if known.get(session_id) == self._scope(target):
            return
        known[session_id] = self._scope(target)
        while len(known) > MAX_REMEMBERED_SESSIONS:
            known.pop(next(iter(known)))
        self.request.session[SESSIONS_KEY] = known

    def form_session(self, fp: formplayer.Formplayer, data: dict) -> str:
        """The request's form session id, if labs opened it for this worker, opportunity and app."""
        session_id = formplayer.clean_session_id(data.get("session_id"))
        known = self.request.session.get(SESSIONS_KEY) or {}
        if known.get(session_id) != self._scope(fp.target):
            raise InvalidInput("Start the form again.", error_code=SESSION_UNKNOWN)
        return session_id


class NavigateView(_EngineView):
    def act(self, fp, data):
        selections = formplayer.clean_selections(data.get("selections"))
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
        return result


class SyncView(_EngineView):
    def act(self, fp, data):
        return formplayer.sync(fp)
