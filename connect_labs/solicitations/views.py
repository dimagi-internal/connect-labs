"""Solicitations pages.

Who may see and do what is decided in `access.py` and enforced by
`SolicitationsDataAccess`; these views only ask it and render the answer. A
refusal (`PermissionDenied`) becomes a plain 403 page that says why, and a call
or response the viewer may not see at all is a 404.

The directory, a call's page and an organisation's page are readable without a
labs login: calls are meant to be sent to organisations outside labs, and
nothing private is on them. Responding, managing, reviewing and awarding need a
login.
"""

import json
import logging

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from connect_labs.microplans.core.solicitation_snapshot import build_plan_snapshot
from connect_labs.solicitations import access, demo, evidence, past_calls, vocabulary
from connect_labs.solicitations.data_access import AlreadyAwarded, SolicitationsDataAccess, by_public_id
from connect_labs.solicitations.forms import NEW_ORG, ReviewForm, SolicitationForm, SolicitationResponseForm
from connect_labs.solicitations.local_models import REVIEW_AI, SOURCE_DIRECTORY, Solicitation, SolicitationResponse

logger = logging.getLogger(__name__)

# How many recently closed calls the directory shows under the open ones.
RECENTLY_CLOSED_LIMIT = 6
# A call this long (description + scope, in characters) or with this many
# sections gets an "On this page" index.
LONG_CALL_CHARS = 2500


def _get_data_access(request):
    return SolicitationsDataAccess(request=request)


# -- Base classes -------------------------------------------------------------


class SolicitationsPage(TemplateView):
    """A solicitations page: refusals render as a plain 403, missing records as 404."""

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except PermissionDenied as exc:
            return forbidden(request, str(exc) or None)
        except (Solicitation.DoesNotExist, SolicitationResponse.DoesNotExist):
            raise Http404("Not found")

    def da(self):
        if not hasattr(self, "_da"):
            self._da = _get_data_access(self.request)
        return self._da


class LabsLoginRequiredMixin(LoginRequiredMixin):
    """Redirect to the labs login, coming back here afterwards."""

    login_url = "/labs/login/"


class SignedInPage(LabsLoginRequiredMixin, SolicitationsPage):
    pass


def forbidden(request, message=None):
    from django.template.response import TemplateResponse

    response = TemplateResponse(
        request,
        "solicitations/forbidden.html",
        {"message": message or "You don't have access to this page."},
        status=403,
    )
    return response.render()


# -- Helpers --------------------------------------------------------------------


def _solicitation_or_404(da, pk):
    solicitation = da.get_solicitation_by_id(pk)
    if solicitation is None:
        raise Http404("Solicitation not found")
    return solicitation


def _round_reading_context(solicitation, response) -> dict:
    """What a page needs to show a round's response for reviewing.

    The contact person's details are folded away rather than leading the page:
    judging an application never needs them. Previous and next follow the round
    page's own order, so a reviewer can work through the list without going back.
    """
    from connect_labs.labs.chrome import PageChrome
    from connect_labs.marketplace import queries
    from connect_labs.marketplace.responses import personal_question_ids

    questions = past_calls.form_questions(solicitation)
    personal = personal_question_ids(questions, solicitation.column_map)
    stored = response.responses or {}
    rows = [{"id": q["id"], "question": q.get("text", ""), "answer": stored.get(q["id"], "")} for q in questions]
    applicants = queries.round_applicants(solicitation, {})
    queries.review_status(solicitation, applicants)  # the round page's order
    order = [a["response"].pk for a in applicants]
    at = order.index(response.pk) if response.pk in order else None
    return {
        "answers": [r for r in rows if r["id"] not in personal],
        "contact": [r for r in rows if r["id"] in personal and r["answer"]],
        "position": (at + 1) if at is not None else None,
        "applicant_count": len(order),
        "previous_pk": order[at - 1] if at else None,
        "next_pk": order[at + 1] if at is not None and at + 1 < len(order) else None,
        # A marketplace page, whatever URL it sits at: no labs context picker.
        "page_chrome": PageChrome(labs_context=False, pulse_widget=False),
    }


def _is_round(solicitation) -> bool:
    """A marketplace EOI round: answered on its Google Form, never here."""
    return solicitation.source == SOURCE_DIRECTORY


def _managed_solicitation(da, pk):
    solicitation = _solicitation_or_404(da, pk)
    access.require(
        da.can_manage(solicitation),
        "Only the organisation that owns this call (or platform support) can manage it.",
    )
    return solicitation


def _response_or_404(da, pk):
    """The response, or a 404 -- the same for one that is not the viewer's as for
    one that does not exist, so ids cannot be probed for. (A refusal on a call is
    still a 403: its existence is no secret when it is public anyway.)"""
    response = da.get_response_by_id(pk)
    if response is None:
        raise Http404("Response not found")
    return response


def _org_or_404(slug):
    org = access.resolve_org(slug)
    if org is None:
        raise Http404("Organisation not found")
    return org


def _split_open_closed(calls):
    open_calls = [c for c in calls if c.can_accept_responses]
    closed = [c for c in calls if not c.can_accept_responses]
    closed.sort(key=lambda c: (c.application_deadline or c.updated_at.date()), reverse=True)
    return open_calls, closed


def _search(calls, query):
    words = [w for w in (query or "").lower().split() if w]
    if not words:
        return calls

    def haystack(c):
        return " ".join([c.title, c.description, c.scope_of_work, c.owner_name, c.program_name]).lower()

    return [c for c in calls if all(w in haystack(c) for w in words)]


def _email_enabled() -> bool:
    from connect_labs.utils.email import email_enabled

    return bool(email_enabled())


def _with_response_counts(calls):
    """Annotate each call with `response_count`: submitted (non-draft) responses."""
    counts = dict(
        Solicitation.objects.filter(pk__in=[c.pk for c in calls])
        .annotate(n=Count("responses", filter=~Q(responses__status="draft")))
        .values_list("pk", "n")
    )
    for call in calls:
        call.response_count = counts.get(call.pk, 0)
    return calls


def _sections(solicitation, show_scope):
    """The detail page's sections, for the "On this page" index."""
    sections = [("description", "Description")]
    if show_scope:
        sections.append(("scope", "Scope of work"))
    if solicitation.plans:
        sections.append(("coverage", "Coverage areas"))
    if solicitation.questions:
        sections.append(("questions", "Application questions"))
    if solicitation.evaluation_criteria:
        sections.append(("criteria", "How responses are scored"))
    return sections


def _scope_differs(solicitation):
    """True when scope_of_work adds content beyond the description.

    Synthetic calls sometimes carry a scope that is the description again;
    rendering both reads as a bug.
    """
    scope = (solicitation.scope_of_work or "").strip()
    if not scope:
        return False
    desc = (solicitation.description or "").strip()
    if not desc:
        return True
    norm_scope = " ".join(scope.lower().split())
    norm_desc = " ".join(desc.lower().split())
    return norm_scope not in norm_desc and norm_desc not in norm_scope


# -- AI Criteria Generation ------------------------------------------------


def _extract_file_text(uploaded_file, max_chars: int = 15000) -> str:
    """Extract text content from an uploaded file."""
    name = uploaded_file.name.lower()

    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            reader = PdfReader(uploaded_file)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            return text[:max_chars]
        except Exception as e:
            return f"[Failed to read PDF: {e}]"

    # Plain text / markdown / csv / etc.
    try:
        content = uploaded_file.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        return content[:max_chars]
    except Exception as e:
        return f"[Failed to read file: {e}]"


def validate_url_safe(url: str) -> str | None:
    """Validate that a URL does not point to a private/internal address.

    Returns None if the URL is safe, or an error message string if blocked.
    Resolves the hostname before checking to prevent DNS rebinding attacks.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return "[Blocked: URL points to internal/private address]"

        # Block loopback hostnames
        if hostname in ("localhost", "localhost.localdomain", "127.0.0.1", "::1", "0.0.0.0"):
            return "[Blocked: URL points to internal/private address]"

        # Resolve hostname to IP addresses to prevent DNS rebinding
        try:
            addr_infos = socket.getaddrinfo(hostname, parsed.port or 80, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            return "[Blocked: URL points to internal/private address]"

        for addr_info in addr_infos:
            ip_str = addr_info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                return "[Blocked: URL points to internal/private address]"

            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return "[Blocked: URL points to internal/private address]"

    except Exception:
        return "[Blocked: URL points to internal/private address]"

    return None


def _fetch_url_content(url: str, max_chars: int = 10000) -> str:
    """Fetch a URL and return its text content, truncated."""
    import re
    from urllib.parse import urlparse

    import httpx

    # Validate the URL is not pointing to an internal/private address
    block_reason = validate_url_safe(url)
    if block_reason:
        return block_reason

    try:
        # Follow redirects manually and re-validate EVERY hop. httpx's built-in
        # follow_redirects resolves and fetches redirect targets on its own, so a
        # public URL that 302s to http://169.254.169.254/ (IMDS) or the ECS
        # task-role creds endpoint would bypass the initial validate_url_safe check.
        # Re-running validation per hop (which re-resolves the host each time) also
        # closes the DNS-rebinding TOCTOU window. Only http/https are followed.
        resp = None
        current = url
        for _ in range(5):  # cap redirect chain length
            resp = httpx.get(current, follow_redirects=False, timeout=15.0)
            if resp.is_redirect:
                location = resp.headers.get("location", "")
                if not location:
                    break
                current = str(resp.url.join(location))
                if urlparse(current).scheme not in ("http", "https"):
                    return "[Blocked: URL points to internal/private address]"
                block_reason = validate_url_safe(current)
                if block_reason:
                    return block_reason
                continue
            break
        else:
            return f"[Failed to fetch {url}: too many redirects]"
        resp.raise_for_status()
        html = resp.text
        # Strip HTML tags for a rough text extraction
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        return f"[Failed to fetch {url}: {e}]"


def _extract_and_fetch_urls(text: str) -> str:
    """Find URLs in text, fetch their content, and return as context."""
    import re

    url_pattern = re.compile(r"https?://[^\s<>\"']+")
    urls = url_pattern.findall(text)
    if not urls:
        return ""

    fetched = []
    for url in urls[:3]:  # Max 3 URLs to avoid abuse
        content = _fetch_url_content(url)
        fetched.append(f"CONTENT FROM {url}:\n{content}")

    return "\n\n".join(fetched)


@require_POST
def generate_criteria_api(request):
    """Generate evaluation criteria from solicitation description and questions using AI.

    Accepts JSON body or multipart form data (for file uploads).
    - JSON: {"description": "...", "scope_of_work": "...", "questions": [...], "urls": [...]}
    - Multipart: description, scope_of_work, questions_json fields + 'files' file uploads
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)

    # Handle both JSON and multipart form data
    if request.content_type and "multipart" in request.content_type:
        description = request.POST.get("description", "")
        scope_of_work = request.POST.get("scope_of_work", "")
        questions_raw = request.POST.get("questions_json", "[]")
        try:
            questions = json.loads(questions_raw)
        except json.JSONDecodeError:
            questions = []
        urls = []
    else:
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        description = body.get("description", "")
        scope_of_work = body.get("scope_of_work", "")
        questions = body.get("questions", [])
        urls = body.get("urls", [])

    if not description:
        return JsonResponse({"error": "Description is required"}, status=400)

    # Extract content from uploaded files
    file_context = ""
    if request.FILES:
        file_texts = []
        for uploaded_file in list(request.FILES.values())[:3]:
            text = _extract_file_text(uploaded_file)
            if text and not text.startswith("[Failed"):
                file_texts.append(f"CONTENT FROM FILE '{uploaded_file.name}':\n{text}")
        file_context = "\n\n".join(file_texts)

    # Fetch URL content — from explicit urls list or detected in description/scope
    url_context = ""
    if urls:
        fetched = [_fetch_url_content(u) for u in urls[:3]]
        url_context = "\n\n".join(f"REFERENCE CONTENT:\n{c}" for c in fetched if c)
    else:
        url_context = _extract_and_fetch_urls(f"{description} {scope_of_work}")

    # Build prompt
    questions_text = "\n".join(f"Q{i+1} (id: q_{i+1}): {q.get('text', '')}" for i, q in enumerate(questions))

    prompt = f"""Based on this solicitation, generate 3-5 evaluation criteria for scoring responses.

SOLICITATION DESCRIPTION:
{description}

SCOPE OF WORK:
{scope_of_work}

APPLICATION QUESTIONS:
{questions_text}
"""

    reference_material = "\n\n".join(filter(None, [file_context, url_context]))
    if reference_material:
        prompt += f"""
REFERENCE MATERIAL (from uploaded files and/or linked URLs — use this to inform criteria):
{reference_material}
"""

    prompt += """
Return a JSON array where each criterion has:
- id: unique identifier like "ec_1", "ec_2", etc.
- name: short name (2-4 words)
- weight: percentage weight (all weights should sum to 100)
- description: one sentence describing what this criterion evaluates
- scoring_guide: what makes a strong vs weak response (be specific, reference the solicitation content)
- linked_questions: array of question IDs (q_1, q_2, etc.) this criterion evaluates

Return ONLY the JSON array, no other text."""

    try:
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        # Parse the response text as JSON
        response_text = response.content[0].text.strip()
        # Handle markdown code blocks
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        criteria = json.loads(response_text)
        return JsonResponse({"criteria": criteria})
    except json.JSONDecodeError:
        logger.exception("Failed to parse AI response as JSON")
        return JsonResponse({"error": "Failed to parse AI response"}, status=500)
    except Exception:
        logger.exception("Failed to generate criteria via AI")
        return JsonResponse({"error": "Failed to generate criteria. Please try again."}, status=500)


# -- Public pages (no login) ------------------------------------------------------


class PublicSolicitationListView(SolicitationsPage):
    """Every listed call that is open, the recently closed ones, and the past
    calls (the marketplace's historical rounds), grouped by year."""

    template_name = "solicitations/public_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        q = self.request.GET
        solicitation_type = q.get("type") if q.get("type") in ("eoi", "rfp") else ""
        owner_slug = (q.get("owner") or "").strip()
        program = (q.get("program") or "").strip()
        query = (q.get("q") or "").strip()

        everything = self.da().get_public_solicitations()
        past = self.da().get_past_calls()
        ran_by = past_calls.owner()
        dimagi_slug = past_calls.owner_slug()

        owners = {c.owner_org.slug: c.owner_org for c in everything if c.owner_org_id}
        if past and dimagi_slug not in owners:
            owners[dimagi_slug] = past_calls.Owner(name=ran_by.name, slug=dimagi_slug)
        owners = sorted(owners.values(), key=lambda o: o.name.lower())
        programs = sorted(
            {(c.delivery_type, c.program_label) for c in [*everything, *past] if c.delivery_type and c.program_label},
            key=lambda p: p[1].lower(),
        )

        calls = everything
        if solicitation_type:
            calls = [c for c in calls if c.solicitation_type == solicitation_type]
            past = [c for c in past if c.solicitation_type == solicitation_type]
        if owner_slug:
            calls = [c for c in calls if c.owner_org_id and c.owner_org.slug == owner_slug]
            if owner_slug != dimagi_slug:
                past = []
        if program:
            calls = [c for c in calls if c.delivery_type == program]
            past = [c for c in past if c.delivery_type == program]
        calls = _search(calls, query)
        past = _search_past(past, query)
        open_calls, closed = _split_open_closed(calls)

        ctx.update(
            {
                "solicitations": open_calls,
                "recently_closed": closed[:RECENTLY_CLOSED_LIMIT],
                "past_years": past_calls.by_year(past),
                "past_count": len(past),
                "past_owner": ran_by,
                "owners": owners,
                "programs": [{"slug": slug, "label": label} for slug, label in programs],
                "selected_type": solicitation_type,
                "selected_owner": owner_slug,
                "selected_program": program,
                "query": query,
                "filtered": bool(solicitation_type or owner_slug or program or query),
                "can_manage_any": bool(self.request.user.is_authenticated and self.da().manageable_orgs()),
            }
        )
        return ctx


class PublicSolicitationDetailView(SolicitationsPage):
    """One call: what it asks for, how it is scored, and how to respond."""

    template_name = "solicitations/public_detail.html"

    def get(self, request, *args, **kwargs):
        # A past call (a marketplace directory round) has its own read-only page;
        # its id here leads there. Only when no call answers to this id at all.
        pk = kwargs["pk"]
        if by_public_id(Solicitation.objects.calls(), pk) is None:
            past = self.da().get_past_call(pk)
            if past is not None:
                return redirect("solicitations:past_detail", slug=past.slug)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        da = self.da()
        solicitation = _solicitation_or_404(da, kwargs["pk"])
        my_response = da.get_my_response(solicitation.pk)
        show_scope = _scope_differs(solicitation)
        sections = _sections(solicitation, show_scope)
        length = len(solicitation.description or "") + (len(solicitation.scope_of_work or "") if show_scope else 0)
        ctx.update(
            {
                "solicitation": solicitation,
                "mapbox_token": settings.MAPBOX_TOKEN or "",
                "my_response": my_response,
                "already_responded": my_response is not None and my_response.status != "draft",
                "has_draft": my_response is not None and my_response.status == "draft",
                "just_submitted": self.request.GET.get("submitted") == "1",
                "show_scope": show_scope,
                "sections": sections,
                "show_section_nav": len(sections) >= 3 or length > LONG_CALL_CHARS,
                "can_manage": da.can_manage(solicitation),
                "is_demo": demo.is_demo_org(solicitation.owner_org),
            }
        )
        return ctx


class OrgSolicitationsView(SolicitationsPage):
    """One organisation's calls: open, then past."""

    template_name = "solicitations/org_page.html"

    def get(self, request, *args, **kwargs):
        org = _org_or_404(kwargs["org_slug"])
        if org.slug != kwargs["org_slug"]:
            return redirect("solicitations:org_page", org_slug=org.slug)
        self.org = org
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        da = self.da()
        can_manage = access.can_manage_org(da.caller, self.org)
        calls = [c for c in da.get_solicitations(org=self.org) if access.is_publicly_visible(c)]
        # Dimagi ran every marketplace directory round, so its page lists them
        # as past calls -- display only: they stay unowned and unmanaged.
        archive = da.get_past_calls() if self.org.slug == past_calls.owner_slug() else []
        if not calls and not archive and not can_manage:
            raise Http404("This organisation has no published calls")
        open_calls, past = _split_open_closed(calls)
        ctx.update(
            {
                "org": self.org,
                "profile": getattr(self.org, "marketplace_profile", None) if _has_profile(self.org) else None,
                "open_calls": open_calls,
                "past_calls": past,
                "archive_years": past_calls.by_year(archive),
                "archive_count": len(archive),
                "can_manage": can_manage,
            }
        )
        return ctx


class PastCallDetailView(SolicitationsPage):
    """A past call: one of the marketplace's historical rounds, read-only.

    Addressed by slug, or by id (which redirects to the slug). Shows the call
    and its announcement; never the application form, the response sheet, the
    import's notes, or who applied. Someone signed in sees how many
    organisations applied and a link to the marketplace's round page (itself
    behind the login); anonymous visitors see neither.
    """

    template_name = "solicitations/past_detail.html"

    def get(self, request, *args, **kwargs):
        call = self.da().get_past_call(kwargs["slug"])
        if call is None:
            raise Http404("Past call not found")
        if call.slug != kwargs["slug"]:
            return redirect("solicitations:past_detail", slug=call.slug)
        self.call = call
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        call = self.call
        signed_in = self.request.user.is_authenticated
        ctx.update(
            {
                "solicitation": call,
                "past_owner": past_calls.owner(),
                "show_scope": _scope_differs(call),
                "applicant_count": self.da().past_call_applicant_count(call),
                "marketplace_url": reverse("marketplace:round", args=[call.slug]) if signed_in else "",
            }
        )
        return ctx


def _search_past(calls, query):
    words = [w for w in (query or "").lower().split() if w]
    if not words:
        return calls
    return [c for c in calls if all(w in past_calls.haystack(c) for w in words)]


def _has_profile(org) -> bool:
    try:
        return org.marketplace_profile is not None
    except Exception:  # noqa: BLE001 -- RelatedObjectDoesNotExist: no profile
        return False


# -- Owners' pages (login) ------------------------------------------------------------


class ManageSolicitationsView(SignedInPage):
    """The organisations whose calls you manage. One goes straight to its workspace."""

    template_name = "solicitations/manage_list.html"

    def get(self, request, *args, **kwargs):
        self.orgs = self.da().manageable_orgs()
        if len(self.orgs) == 1:
            return redirect("solicitations:org_manage", org_slug=self.orgs[0].slug)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        counts = dict(
            Solicitation.objects.calls()
            .filter(owner_org__in=self.orgs)
            .values("owner_org")
            .annotate(n=Count("pk"))
            .values_list("owner_org", "n")
        )
        for org in self.orgs:
            org.call_count = counts.get(org.pk, 0)
        ctx["orgs"] = self.orgs
        return ctx


class OrgManageView(SignedInPage):
    """An organisation's workspace: every call it owns, drafts and unlisted ones included."""

    template_name = "solicitations/org_manage.html"

    def get(self, request, *args, **kwargs):
        org = _org_or_404(kwargs["org_slug"])
        if org.slug != kwargs["org_slug"]:
            return redirect("solicitations:org_manage", org_slug=org.slug)
        access.require(
            access.can_manage_org(self.da().caller, org),
            f"Only members of {org.name} (or platform support) can manage its calls.",
        )
        self.org = org
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        da = self.da()
        calls = _with_response_counts(da.get_solicitations(org=self.org))
        ctx.update(
            {
                "org": self.org,
                "solicitations": calls,
                "other_orgs": [o for o in da.manageable_orgs() if o.pk != self.org.pk],
                "is_operator": da.caller.is_operator and not da.caller.is_member(self.org),
            }
        )
        return ctx


class SolicitationCreateView(SignedInPage):
    """Create a call, owned by an organisation the user manages."""

    template_name = "solicitations/solicitation_form.html"

    def _owner_choices(self):
        user = getattr(self.request, "user", None)
        if user is None or not user.is_authenticated:
            return []
        return self.da().manageable_orgs()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        orgs = self._owner_choices()
        wanted = self.request.POST.get("owner_org") or self.request.GET.get("owner") or ""
        selected = next((o for o in orgs if o.slug == wanted), orgs[0] if len(orgs) == 1 else None)
        ctx.update(
            {
                "is_create": True,
                "owner_orgs": orgs,
                "selected_owner": selected.slug if selected else "",
                "existing_questions": [],
                "existing_criteria": [],
                "mapbox_token": settings.MAPBOX_TOKEN or "",
            }
        )

        snapshot = self._snapshot_from_query()
        if snapshot:
            ctx["form"] = SolicitationForm(
                initial={
                    "title": snapshot["suggested_title"],
                    "scope_of_work": snapshot["suggested_scope"],
                    "plans_json": json.dumps(snapshot["plans"]),
                    "source_program_id": snapshot["source_program_id"],
                    "source_group_id": snapshot["source_group_id"],
                    "source_plan_ids_json": json.dumps(snapshot["source_plan_ids"]),
                }
            )
            ctx["snapshot_plans"] = snapshot["plans"]
        else:
            ctx.setdefault("form", SolicitationForm())
            ctx.setdefault("snapshot_plans", [])
        return ctx

    def _snapshot_from_query(self):
        """A plan snapshot from ?source_program_id=&source_group_id|source_plan_id=, or None."""
        q = self.request.GET
        program_id = q.get("source_program_id")
        group_id = q.get("source_group_id")
        plan_id = q.get("source_plan_id")
        if not program_id or not (group_id or plan_id):
            return None
        try:
            # Imported here (not at module top) so tests can patch it and to avoid import-time geo-stack cost.
            from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

            da = ProgramPlanDataAccess(int(program_id), request=self.request)
            if group_id:
                return build_plan_snapshot(da, group_id=int(group_id))
            return build_plan_snapshot(da, plan_id=int(plan_id))
        except Exception:
            logger.exception("create-from-microplan snapshot failed (program=%s)", program_id)
            return None

    def post(self, request, *args, **kwargs):
        form = SolicitationForm(request.POST)
        orgs = self._owner_choices()
        owner = next((o for o in orgs if o.slug == request.POST.get("owner_org")), None)
        if owner is None:
            form.is_valid()
            form.add_error(None, "Choose the organisation this call is from.")
        if not form.is_valid():
            return self._rerender(form, **kwargs)

        data = form.to_data_dict()
        if request.POST.get("criteria_locked") in ("1", "true", "on", "True"):
            # Lock-before-publish: the weighted criteria are fixed before any
            # organisation responds, so the call's promise cannot change mid-window.
            data["criteria_locked"] = True
        if data.get("source_program_id") and not data.get("program_id"):
            data["program_id"] = data["source_program_id"]
        try:
            created = self.da().create_solicitation(data, owner_org=owner)
        except ValidationError as e:
            _attach_errors(form, e)
            return self._rerender(form, **kwargs)
        responses_url = reverse("solicitations:responses_list", kwargs={"pk": created.public_id})
        return redirect(f"{responses_url}?created=1")

    def _rerender(self, form, **kwargs):
        ctx = self.get_context_data(**kwargs)
        ctx["form"] = form
        return self.render_to_response(ctx)


def _attach_errors(form, error: ValidationError) -> None:
    """Put a data-access ValidationError on the matching form field (or the form)."""
    errors = error.message_dict if hasattr(error, "message_dict") else {"__all__": list(error.messages)}
    for field, msgs in errors.items():
        for msg in msgs if isinstance(msgs, list) else [msgs]:
            target = field if field in form.fields else None
            form.add_error(target, msg if target else f"{field}: {msg}")


class SolicitationEditView(SignedInPage):
    """Edit a call. Only what the form sends is changed (edits merge)."""

    template_name = "solicitations/solicitation_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        solicitation = _managed_solicitation(self.da(), kwargs["pk"])
        ctx.update(
            {
                "solicitation": solicitation,
                "is_create": False,
                "existing_questions": solicitation.questions or [],
                "existing_criteria": solicitation.evaluation_criteria or [],
                "mapbox_token": settings.MAPBOX_TOKEN or "",
                "snapshot_plans": [],
            }
        )
        ctx.setdefault(
            "form",
            SolicitationForm(
                initial={
                    "title": solicitation.title,
                    "description": solicitation.description,
                    "scope_of_work": solicitation.scope_of_work,
                    "solicitation_type": solicitation.solicitation_type,
                    "status": solicitation.status,
                    "is_public": solicitation.is_public,
                    "application_deadline": solicitation.application_deadline,
                    "expected_start_date": solicitation.expected_start_date,
                    "expected_end_date": solicitation.expected_end_date,
                    "estimated_scale": solicitation.estimated_scale,
                    "contact_email": solicitation.contact_email,
                }
            ),
        )
        return ctx

    def post(self, request, *args, **kwargs):
        solicitation = _managed_solicitation(self.da(), kwargs["pk"])
        form = SolicitationForm(request.POST)
        if form.is_valid():
            try:
                self.da().update_solicitation(solicitation.public_id, form.to_data_dict())
            except ValidationError as e:
                _attach_errors(form, e)
            else:
                if solicitation.owner_org_id:
                    return redirect("solicitations:org_manage", org_slug=solicitation.owner_org.slug)
                return redirect("solicitations:manage_list")
        ctx = self.get_context_data(**kwargs)
        ctx["form"] = form
        return self.render_to_response(ctx)


class ResponsesListView(SignedInPage):
    """The owner's inbox for one call. Responses are shown by number, not name: scoring is blind."""

    template_name = "solicitations/responses_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        da = self.da()
        solicitation = _managed_solicitation(da, kwargs["pk"])
        responses = [r for r in da.get_responses_for_solicitation(solicitation.public_id) if r.status != "draft"]
        for r in responses:
            reviews = da.get_reviews_for_response(r.pk)
            r.latest_review = reviews[-1] if reviews else None
            r.can_review = access.can_review(da.caller, r)
        ctx.update(
            {
                "solicitation": solicitation,
                "responses": responses,
                "has_awarded": solicitation.status == "awarded" or any(r.status == "awarded" for r in responses),
                "just_created": self.request.GET.get("created") == "1",
                "share_url": self.request.build_absolute_uri(
                    reverse("solicitations:public_detail", kwargs={"pk": solicitation.public_id})
                ),
            }
        )
        return ctx


# -- Responding (login) -------------------------------------------------------------


class RespondView(SignedInPage):
    """Answer a call, as an organisation. One draft per person, edited in place."""

    template_name = "solicitations/respond.html"

    def _load(self, pk):
        da = self.da()
        # A marketplace round has no call here, but it does have a form: its
        # questions, shown read-only (`_is_round`).
        solicitation = da.get_solicitation_by_id(pk) or da.get_round_form(pk)
        if solicitation is None:
            raise Http404("Solicitation not found")
        my_response = da.get_my_response(solicitation.public_id)
        orgs = access.member_orgs(da.caller)
        return solicitation, my_response, orgs

    def _form(self, solicitation, orgs, my_response=None, data=None):
        initial = {}
        if my_response is not None and my_response.status == "draft":
            initial = {f"question_{k}": v for k, v in (my_response.responses or {}).items()}
            if my_response.llo_entity_id and any(o.pk == my_response.llo_entity_id for o in orgs):
                initial["org"] = my_response.llo_entity.slug
            elif my_response.org_name:
                initial.update({"org": NEW_ORG, "new_org_name": my_response.org_name})
            if my_response.selected_plan_ids:
                initial["select_plans"] = [str(i) for i in my_response.selected_plan_ids]
        return SolicitationResponseForm(
            questions=solicitation.questions,
            plans=solicitation.plans,
            org_choices=[(o.slug, o.name) for o in orgs],
            data=data,
            initial=initial,
        )

    def _context(self, solicitation, my_response, form, **extra):
        criteria_by_question = {}
        for criterion in solicitation.evaluation_criteria or []:
            for q_id in criterion.get("linked_questions", []):
                criteria_by_question.setdefault(q_id, []).append(criterion)
        question_fields = []
        is_round = _is_round(solicitation)
        if is_round or not solicitation.can_accept_responses:
            for field in form.fields.values():
                field.widget.attrs["disabled"] = True
        for question in past_calls.form_questions(solicitation) if is_round else solicitation.questions or []:
            field_name = f"question_{question.get('id', '')}"
            question_fields.append(
                {
                    "question": question,
                    "field": form[field_name] if field_name in form.fields else None,
                    "field_name": field_name,
                    "criteria": criteria_by_question.get(question.get("id", ""), []),
                }
            )
        ctx = self.get_context_data(
            solicitation=solicitation,
            my_response=my_response,
            already_submitted=my_response is not None and my_response.status != "draft",
            not_accepting=is_round or not solicitation.can_accept_responses,
            is_directory_round=is_round,
            form=form,
            question_fields=question_fields,
            mapbox_token=settings.MAPBOX_TOKEN or "",
            is_demo=demo.is_demo_org(solicitation.owner_org),
            **extra,
        )
        return ctx

    def get(self, request, *args, **kwargs):
        solicitation, my_response, orgs = self._load(kwargs["pk"])
        form = self._form(solicitation, orgs, my_response)
        return self.render_to_response(self._context(solicitation, my_response, form))

    def post(self, request, *args, **kwargs):
        solicitation, my_response, orgs = self._load(kwargs["pk"])
        form = self._form(solicitation, orgs, data=request.POST)
        if _is_round(solicitation) or not solicitation.can_accept_responses:
            ctx = self._context(solicitation, my_response, form)
            response = self.render_to_response(ctx)
            response.status_code = 403
            return response
        if my_response is not None and my_response.status != "draft":
            return redirect("solicitations:public_detail", pk=solicitation.public_id)

        status = "draft" if "save_draft" in request.POST else "submitted"
        if form.is_valid():
            org_slug, new_name, new_email = form.responding_org()
            selected_ids, selected_names = form.get_selected_plans(solicitation.plans or [])
            if status == "submitted":
                if not org_slug and not new_name:
                    form.add_error("new_org_name", "Name the organisation you are responding for.")
                if solicitation.plans and not selected_ids:
                    form.add_error(None, "Select at least one coverage area to submit.")
        if form.is_valid():
            data = {"responses": form.get_responses_dict(), "status": status}
            if new_name:
                data["org_name"] = new_name
                data["llo_entity_name"] = new_name
            if new_email:
                data["submitted_by_email"] = new_email
            if solicitation.plans:
                data["selected_plan_ids"] = selected_ids
                data["selected_plan_names"] = selected_names
            try:
                self.da().create_response(solicitation.public_id, llo_entity_id=org_slug, data=data)
            except ValidationError as e:
                _attach_errors(form, e)
            else:
                detail_url = reverse("solicitations:public_detail", kwargs={"pk": solicitation.public_id})
                if status == "submitted":
                    return redirect(f"{detail_url}?submitted=1")
                return redirect(f"{reverse('solicitations:respond', kwargs={'pk': solicitation.public_id})}?saved=1")
        return self.render_to_response(self._context(solicitation, my_response, form))


class ResponseDetailView(SignedInPage):
    """One response, named. The owner sees it with its reviews; the respondent sees their own.

    A marketplace round's response is read beside its reviews: the answers on
    one side, a review on the other, and every statement in the review linked
    to the passages it rests on (`evidence.py`, `round_response.html`).
    """

    template_name = "solicitations/response_detail.html"

    def get_template_names(self):
        if self._is_round:
            return ["solicitations/round_response.html"]
        return [self.template_name]

    def get(self, request, *args, **kwargs):
        self._is_round = False
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        da = self.da()
        response = _response_or_404(da, kwargs["pk"])
        solicitation = response.solicitation
        reviews = da.get_reviews_for_response(response.pk)
        criteria_by_id = {c.get("id"): c for c in (solicitation.evaluation_criteria or [])}
        for rev in reviews:
            rev.criteria_score_rows = [
                {
                    "name": (criteria_by_id.get(c_id) or {}).get("name", c_id),
                    "weight": (criteria_by_id.get(c_id) or {}).get("weight"),
                    "score": c_score,
                    "note": (rev.criteria_notes or {}).get(c_id, ""),
                }
                for c_id, c_score in (rev.criteria_scores or {}).items()
            ]
        is_round = access.is_directory_round(solicitation)
        self._is_round = is_round
        my_review = next((r for r in reviews if r.kind != REVIEW_AI and r.reviewer_id == da.caller.user_pk), None)
        if is_round:
            ctx.update(_round_reading_context(solicitation, response))
            ctx["evidence"] = {
                "legend": evidence.legend(solicitation.evaluation_criteria),
                "reviews": [
                    {
                        "id": r.pk,
                        "label": "AI first pass" if r.kind == REVIEW_AI else r.reviewer_username,
                        "claims": evidence.shown_claims(r),
                    }
                    for r in reviews
                ],
            }
            for r in reviews:
                r.panel = evidence.panel(r, solicitation.evaluation_criteria, ctx["answers"])
        ctx.update(
            {
                "response": response,
                "solicitation": solicitation,
                "reviews": reviews,
                "is_round": is_round,
                "my_review": my_review,
                "ai_reviews": [r for r in reviews if r.kind == REVIEW_AI],
                "is_owner": da.can_manage(solicitation)
                or (is_round and access.can_read_reviews(da.caller, solicitation)),
                "is_respondent": access.is_respondent(da.caller, response),
                "can_review": access.can_review(da.caller, response),
                "can_edit": access.can_edit_response(da.caller, response) and bool(solicitation.can_accept_responses),
                "qa_pairs": [
                    {"question": q.get("text", ""), "answer": (response.responses or {}).get(q.get("id", ""), "")}
                    for q in (past_calls.form_questions(solicitation) if is_round else solicitation.questions or [])
                ],
            }
        )
        return ctx


# -- Award and review (owner or platform support, never your own side) ------------------


class AwardView(SignedInPage):
    """Award a response: record the decision and email the awardee if mail is on."""

    template_name = "solicitations/award.html"

    def _summary(self, da, response, solicitation):
        reviews = da.get_reviews_for_response(response.pk)
        scores = [r.score for r in reviews if r.score is not None]
        coverage = list(response.selected_plan_names or [])
        if not coverage and solicitation.plans:
            coverage = [p.get("name", "") for p in solicitation.plans if p.get("name")]
        return {
            "org_display": response.display_name or "the selected organisation",
            "unverified_org": response.llo_entity_id is None,
            "contact_name": response.submitted_by_name,
            "contact_email": response.submitted_by_email,
            # Where the award email goes: the respondent's account, never the
            # address typed on the form.
            "notify_email": response.account_email,
            "coverage_areas": [c for c in coverage if c],
            "score": max(scores) if scores else None,
        }

    def _load(self, pk):
        da = self.da()
        response = _response_or_404(da, pk)
        access.require(
            access.can_award(da.caller, response),
            "Only the organisation that owns this call (or platform support) can award it, "
            "and never a response from your own side.",
        )
        return da, response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        da, response = self._load(kwargs["pk"])
        solicitation = response.solicitation
        ctx.update(
            {
                "response": response,
                "solicitation": solicitation,
                "summary": self._summary(da, response, solicitation),
                "awarded": response.is_awarded,
                "currency": solicitation.currency,
                "is_operator": da.caller.is_operator,
                "email_on": _email_enabled(),
            }
        )
        return ctx

    def post(self, request, *args, **kwargs):
        da, response = self._load(kwargs["pk"])
        try:
            reward_budget = int(request.POST.get("reward_budget") or 0) or None
        except ValueError:
            ctx = self.get_context_data(**kwargs)
            ctx["error"] = "The award budget must be a whole number."
            return self.render_to_response(ctx)
        award_stage = request.POST.get("award_stage", "verification")
        if response.is_awarded and award_stage == "full" and reward_budget is None:
            # The upgrade allocates the new budget: without one there is nothing to award.
            ctx = self.get_context_data(**kwargs)
            ctx["error"] = "Enter the full engagement budget to award the full engagement."
            return self.render_to_response(ctx)
        # The upgrade form sends no org id: keep the one the first award recorded.
        org_id = (request.POST.get("org_id") or response.org_id) if da.caller.is_operator else response.org_id
        try:
            da.award_response(
                response.pk,
                reward_budget=reward_budget,
                org_id=org_id,
                award_stage=award_stage,
            )
        except AlreadyAwarded as exc:
            ctx = self.get_context_data(**kwargs)
            ctx["error"] = str(exc)
            page = self.render_to_response(ctx)
            page.status_code = 409
            return page
        return redirect("solicitations:award", pk=response.pk)


class ReviewView(SignedInPage):
    """Score a response: by number, with no name, email or organisation shown.

    Three ways in. Plain, it is your own review, written blind: an AI review's
    per-criterion scores stay hidden until you enter yours, so they cannot
    anchor you. `?from=<review>` starts your review as a copy of another one --
    usually the AI's -- to edit as your own. `?from=<review>&stance=agrees` is
    the same copy, already marked as agreeing, for when you only want to say it
    is right. Whichever way, the review you started from is unchanged.
    """

    template_name = "solicitations/review_form.html"

    def get_template_names(self):
        if getattr(self, "_is_round", False):
            return ["solicitations/round_review_form.html"]
        return [self.template_name]

    def _load(self, pk):
        da = self.da()
        response = _response_or_404(da, pk)
        access.require(
            access.can_review(da.caller, response),
            "Only the organisation that owns this call (or platform support) can review it, "
            "and never a response from your own side.",
        )
        self._is_round = access.is_directory_round(response.solicitation)
        return da, response

    def _source(self, da, response, key):
        """The review this one starts from: another review of the same response."""
        review = da.get_review_by_id(int(key)) if str(key or "").isdigit() else None
        return review if review is not None and review.response_id == response.pk else None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        da, response = self._load(kwargs["pk"])
        solicitation = response.solicitation
        is_round = access.is_directory_round(solicitation)
        eval_criteria = solicitation.evaluation_criteria or []
        criteria_by_question = {}
        for criterion in eval_criteria:
            for q_id in criterion.get("linked_questions", []):
                criteria_by_question.setdefault(q_id, []).append(criterion)
        linked_ids = {c.get("id") for pairs in criteria_by_question.values() for c in pairs}
        existing = da.get_my_review(response.pk)
        source = self._source(da, response, self.request.GET.get("from") or self.request.POST.get("based_on"))
        ai_review = next((r for r in da.get_reviews_for_response(response.pk) if r.kind == REVIEW_AI), None)
        # What the reveal-after-you-score boxes show: the AI review's scores and
        # reasons when there is one, else the older per-response proposal. A
        # copy already shows everything, so it hides nothing.
        if source is not None:
            hidden = {}
        elif ai_review is not None:
            hidden = {
                cid: {"score": score, "rationale": (ai_review.criteria_notes or {}).get(cid, "")}
                for cid, score in (ai_review.criteria_scores or {}).items()
            }
        else:
            hidden = response.ai_proposed_scores
        questions = past_calls.form_questions(solicitation) if is_round else solicitation.questions or []
        ctx.update(
            {
                "response": response,
                "solicitation": solicitation,
                "is_round": is_round,
                "blind_label": response.blind_label,
                "ai_proposed_scores": hidden,
                "source": source,
                "qa_pairs": [
                    {
                        "question": q.get("text", ""),
                        "answer": (response.responses or {}).get(q.get("id", ""), ""),
                        "question_id": q.get("id", ""),
                        "criteria": criteria_by_question.get(q.get("id", ""), []),
                    }
                    for q in questions
                ],
                "unlinked_criteria": [c for c in eval_criteria if c.get("id") not in linked_ids],
                "existing_review": existing,
                "is_update": existing is not None,
            }
        )
        if "form" not in ctx:
            start = source or existing
            initial = {}
            if start is not None:
                initial = {
                    "score": start.score,
                    "recommendation": start.recommendation,
                    "notes": start.notes,
                    "tags": start.tags,
                    "outcome": start.outcome,
                    "ask_next": start.ask_next,
                    "flags": [k for k in (vocabulary.flag_key(f) for f in start.flags or []) if k],
                    "claims_json": json.dumps(evidence.claims_of(start)),
                    **{f"criteria_score_{k}": v for k, v in (start.criteria_scores or {}).items()},
                }
                if start is existing and existing.based_on_id:
                    initial.update({"based_on": existing.based_on_id, "stance": existing.stance})
            if source is not None:
                initial.update({"based_on": source.pk, "stance": self.request.GET.get("stance") or "amended"})
                if initial["stance"] == "adds":
                    # Adding to a review keeps it whole: the scores and outcome
                    # start as its, and everything written here is an addition.
                    initial.update({"claims_json": "[]", "ask_next": "", "flags": [], "notes": ""})
            ctx["form"] = ReviewForm(evaluation_criteria=eval_criteria, initial=initial, for_round=is_round)
        if is_round:
            ctx.update(_round_reading_context(solicitation, response))
            ctx["evidence"] = {"legend": evidence.legend(eval_criteria)}
            ctx["flag_definitions"] = list(vocabulary.FLAGS.items())
            # What an addition is added to: shown read-only beside each criterion.
            form = ctx["form"]
            base = source if source is not None else (existing.based_on if existing and existing.based_on_id else None)
            if base is not None and (form["stance"].value() or "") == "adds":
                inherited = [{**c, "id": f"base-{c.get('id')}"} for c in evidence.claims_of(base)]
                ctx["adds_to"] = base
                ctx["evidence"]["base_claims"] = inherited
                ctx["unlinked_criteria"] = [
                    {**c, "base_claims": [x for x in inherited if x["about"] == f"criterion:{c.get('id')}"]}
                    for c in ctx["unlinked_criteria"]
                ]
                ctx["base_other_claims"] = [x for x in inherited if not x["about"].startswith("criterion:")]
        return ctx

    def post(self, request, *args, **kwargs):
        da, response = self._load(kwargs["pk"])
        is_round = access.is_directory_round(response.solicitation)
        form = ReviewForm(response.solicitation.evaluation_criteria or [], request.POST, for_round=is_round)
        if form.is_valid():
            data = {
                "notes": form.cleaned_data["notes"],
                "tags": form.cleaned_data["tags"],
                "review_date": timezone.now().isoformat(),
                "criteria_scores": form.get_criteria_scores(),
            }
            if "score" in form.fields:
                data["score"] = form.cleaned_data["score"]
            if is_round:
                for name in ("outcome", "ask_next", "flags", "stance"):
                    data[name] = form.cleaned_data[name]
                claims = form.cleaned_data["claims_json"]
                data["claims"] = claims
                if not claims:
                    data.update({"strengths": "", "concerns": "", "criteria_notes": {}})
                data["based_on"] = form.cleaned_data["based_on"]
                if not data["based_on"]:
                    data["stance"] = ""
            else:
                data["recommendation"] = form.cleaned_data["recommendation"]
            try:
                da.create_review(response.pk, data)
            except ValidationError as e:
                form.add_error(None, e.messages)
                return self.render_to_response(self.get_context_data(form=form, **kwargs))
            if is_round:
                return redirect("solicitations:response_detail", pk=response.pk)
            return redirect("solicitations:responses_list", pk=response.solicitation.public_id)
        ctx = self.get_context_data(form=form, **kwargs)
        return self.render_to_response(ctx)
