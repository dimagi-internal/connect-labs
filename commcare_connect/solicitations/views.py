import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from commcare_connect.solicitations.data_access import SolicitationsDataAccess
from commcare_connect.solicitations.forms import ReviewForm, SolicitationForm, SolicitationResponseForm

logger = logging.getLogger(__name__)


# -- Permission Mixins ------------------------------------------------------


class LabsLoginRequiredMixin(LoginRequiredMixin):
    """Redirect to labs login."""

    login_url = "/labs/login/"


class ManagerRequiredMixin(LabsLoginRequiredMixin, UserPassesTestMixin):
    """Require authenticated labs user (manager access)."""

    def test_func(self):
        return self.request.user.is_authenticated


# -- Helpers ----------------------------------------------------------------


def _has_context(request):
    """Check if the request has a program_id or organization_id in labs_context."""
    labs_context = getattr(request, "labs_context", {})
    return bool(labs_context.get("program_id") or labs_context.get("organization_id"))


def _get_data_access(request):
    """Create data access from request. Works for authed requests."""
    return SolicitationsDataAccess(request=request)


def _get_public_data_access(request):
    """Create data access for public views — uses CLI token as fallback if no session."""
    try:
        return SolicitationsDataAccess(request=request)
    except ValueError:
        # No OAuth session (unauthenticated user) — use CLI token
        from commcare_connect.labs.integrations.connect.cli import TokenManager

        tm = TokenManager()
        token = tm.get_valid_token()
        return SolicitationsDataAccess(access_token=token)


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


def _fetch_url_content(url: str, max_chars: int = 10000) -> str:
    """Fetch a URL and return its text content, truncated."""
    import re

    import httpx

    try:
        resp = httpx.get(url, follow_redirects=True, timeout=15.0)
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
            model="claude-sonnet-4-20250514",
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


# -- Public Views (no login) -----------------------------------------------


class PublicSolicitationListView(TemplateView):
    template_name = "solicitations/public_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        solicitation_type = self.request.GET.get("type")
        try:
            da = _get_public_data_access(self.request)
            ctx["solicitations"] = da.get_public_solicitations(
                solicitation_type=solicitation_type,
            )
        except Exception:
            logger.exception("Failed to load public solicitations")
            ctx["solicitations"] = []
        ctx["selected_type"] = solicitation_type or ""
        return ctx


class PublicSolicitationDetailView(TemplateView):
    template_name = "solicitations/public_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        try:
            da = _get_public_data_access(self.request)
            solicitation = da.get_solicitation_by_id(pk)
            if not solicitation:
                raise Http404("Solicitation not found")
            ctx["solicitation"] = solicitation
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load solicitation %s", pk)
            raise Http404("Solicitation not found")
        return ctx


# -- Manager Views (login required) ----------------------------------------


class ManageSolicitationsView(ManagerRequiredMixin, TemplateView):
    """List solicitations for the current program with response counts."""

    template_name = "solicitations/manage_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["has_context"] = _has_context(self.request)
        if not ctx["has_context"]:
            ctx["solicitations"] = []
            return ctx
        try:
            da = _get_data_access(self.request)
            solicitations = da.get_solicitations()
            for s in solicitations:
                try:
                    responses = da.get_responses_for_solicitation(s.pk)
                    s.response_count = len(responses)
                except Exception:
                    s.response_count = 0
            ctx["solicitations"] = solicitations
        except Exception:
            logger.exception("Failed to load solicitations for manage view")
            ctx["solicitations"] = []
        return ctx


class SolicitationCreateView(ManagerRequiredMixin, TemplateView):
    """Create a new solicitation."""

    template_name = "solicitations/solicitation_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["has_context"] = _has_context(self.request)
        ctx["form"] = SolicitationForm()
        ctx["is_create"] = True
        ctx["existing_questions_json"] = "[]"
        ctx["existing_criteria_json"] = "[]"
        return ctx

    def post(self, request, *args, **kwargs):
        if not _has_context(request):
            ctx = self.get_context_data(**kwargs)
            ctx[
                "error"
            ] = "Please select a program or organization from the context selector before creating a solicitation."
            return self.render_to_response(ctx)

        form = SolicitationForm(request.POST)
        if form.is_valid():
            data = form.to_data_dict()
            data["created_by"] = request.user.username
            labs_context = getattr(request, "labs_context", {})
            data["program_name"] = labs_context.get("program_name", "")
            try:
                da = _get_data_access(request)
                da.create_solicitation(data)
                return redirect("solicitations:manage_list")
            except Exception:
                logger.exception("Failed to create solicitation")
                ctx = self.get_context_data(**kwargs)
                ctx["form"] = form
                ctx["error"] = "Failed to create solicitation. Please try again."
                return self.render_to_response(ctx)
        else:
            ctx = self.get_context_data(**kwargs)
            ctx["form"] = form
            return self.render_to_response(ctx)


class SolicitationEditView(ManagerRequiredMixin, TemplateView):
    """Edit an existing solicitation."""

    template_name = "solicitations/solicitation_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["has_context"] = _has_context(self.request)
        pk = kwargs["pk"]
        try:
            da = _get_data_access(self.request)
            solicitation = da.get_solicitation_by_id(pk)
            if not solicitation:
                raise Http404("Solicitation not found")
            ctx["solicitation"] = solicitation
            # Populate form with initial data from the existing solicitation
            initial = {
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
            ctx["form"] = SolicitationForm(initial=initial)
            ctx["is_create"] = False
            ctx["existing_questions_json"] = json.dumps(solicitation.questions)
            ctx["existing_criteria_json"] = json.dumps(solicitation.evaluation_criteria)
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load solicitation %s for editing", pk)
            raise Http404("Solicitation not found")
        return ctx

    def post(self, request, *args, **kwargs):
        pk = kwargs["pk"]
        form = SolicitationForm(request.POST)
        if form.is_valid():
            data = form.to_data_dict()
            try:
                da = _get_data_access(request)
                da.update_solicitation(pk, data)
                return redirect("solicitations:manage_list")
            except Exception:
                logger.exception("Failed to update solicitation %s", pk)
                ctx = self.get_context_data(**kwargs)
                ctx["form"] = form
                ctx["error"] = "Failed to update solicitation. Please try again."
                return self.render_to_response(ctx)
        else:
            ctx = self.get_context_data(**kwargs)
            ctx["form"] = form
            return self.render_to_response(ctx)


class ResponsesListView(ManagerRequiredMixin, TemplateView):
    """List responses for a solicitation with review data."""

    template_name = "solicitations/responses_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        try:
            da = _get_data_access(self.request)
            solicitation = da.get_solicitation_by_id(pk)
            if not solicitation:
                raise Http404("Solicitation not found")
            ctx["solicitation"] = solicitation

            responses = da.get_responses_for_solicitation(pk)
            for r in responses:
                try:
                    reviews = da.get_reviews_for_response(r.pk)
                    r.latest_review = reviews[-1] if reviews else None
                except Exception:
                    r.latest_review = None
            ctx["responses"] = responses
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load responses for solicitation %s", pk)
            raise Http404("Solicitation not found")
        return ctx


# -- Response Views (login required) ---------------------------------------


class RespondView(LabsLoginRequiredMixin, TemplateView):
    """Submit or save a draft response to a solicitation."""

    template_name = "solicitations/respond.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        try:
            da = _get_data_access(self.request)
            solicitation = da.get_solicitation_by_id(pk)
            if not solicitation:
                raise Http404("Solicitation not found")
            if not solicitation.can_accept_responses():
                ctx["not_accepting"] = True
            ctx["solicitation"] = solicitation
            form = SolicitationResponseForm(questions=solicitation.questions)
            ctx["form"] = form

            # Build zipped question + field + criteria list for template rendering
            criteria_by_question = {}
            for criterion in solicitation.evaluation_criteria:
                for q_id in criterion.get("linked_questions", []):
                    criteria_by_question.setdefault(q_id, []).append(criterion)

            question_fields = []
            for question in solicitation.questions:
                q_id = question.get("id", "")
                field_name = f"question_{q_id}"
                field = form[field_name] if field_name in form.fields else None
                question_fields.append(
                    {
                        "question": question,
                        "field": field,
                        "field_name": field_name,
                        "criteria": criteria_by_question.get(q_id, []),
                    }
                )
            ctx["question_fields"] = question_fields
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load solicitation %s for response", pk)
            raise Http404("Solicitation not found")
        return ctx

    def post(self, request, *args, **kwargs):
        pk = kwargs["pk"]
        try:
            da = _get_data_access(request)
            solicitation = da.get_solicitation_by_id(pk)
            if not solicitation:
                raise Http404("Solicitation not found")
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load solicitation %s for response POST", pk)
            raise Http404("Solicitation not found")

        if not solicitation.can_accept_responses():
            return redirect("solicitations:public_detail", pk=pk)

        form = SolicitationResponseForm(questions=solicitation.questions, data=request.POST)
        if form.is_valid():
            # Determine status based on which button was pressed
            if "save_draft" in request.POST:
                status = "draft"
            else:
                status = "submitted"

            # Pull org info from context for display on responses list
            labs_context = getattr(request, "labs_context", {})
            org = labs_context.get("organization", {})

            data = {
                "solicitation_id": pk,
                "responses": form.get_responses_dict(),
                "status": status,
                "submitted_by_name": request.user.name or request.user.username,
                "submitted_by_email": request.user.email,
                "org_id": org.get("id", ""),
                "org_name": org.get("name", ""),
                "submission_date": timezone.now().isoformat(),
            }

            try:
                da.create_response(
                    solicitation_id=pk,
                    llo_entity_id="individual",
                    data=data,
                )
                return redirect("solicitations:public_detail", pk=pk)
            except Exception:
                logger.exception("Failed to create response for solicitation %s", pk)
                ctx = self.get_context_data(**kwargs)
                ctx["form"] = form
                ctx["error"] = "Failed to submit response. Please try again."
                return self.render_to_response(ctx)
        else:
            ctx = self.get_context_data(**kwargs)
            ctx["form"] = form
            return self.render_to_response(ctx)


class ResponseDetailView(LabsLoginRequiredMixin, TemplateView):
    """View details of a single response with Q&A pairs and reviews."""

    template_name = "solicitations/response_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        try:
            da = _get_data_access(self.request)
            response = da.get_response_by_id(pk)
            if not response:
                raise Http404("Response not found")
            ctx["response"] = response

            # Load parent solicitation
            solicitation = da.get_solicitation_by_id(response.solicitation_id)
            ctx["solicitation"] = solicitation

            # Load reviews
            reviews = da.get_reviews_for_response(pk)
            ctx["reviews"] = reviews

            # Build Q&A pairs
            qa_pairs = []
            if solicitation and solicitation.questions:
                for question in solicitation.questions:
                    q_id = question.get("id", "")
                    qa_pairs.append(
                        {
                            "question": question.get("text", ""),
                            "answer": response.responses.get(q_id, ""),
                        }
                    )
            ctx["qa_pairs"] = qa_pairs
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load response %s", pk)
            raise Http404("Response not found")
        return ctx


# -- Award Views (manager required) ----------------------------------------


class AwardView(ManagerRequiredMixin, TemplateView):
    """Award a response — mark as awarded with budget and org_id."""

    template_name = "solicitations/award.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        try:
            da = _get_data_access(self.request)
            response = da.get_response_by_id(pk)
            if not response:
                raise Http404("Response not found")
            ctx["response"] = response

            solicitation = da.get_solicitation_by_id(response.solicitation_id)
            ctx["solicitation"] = solicitation
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load response %s for award", pk)
            raise Http404("Response not found")
        return ctx

    def post(self, request, *args, **kwargs):
        pk = kwargs["pk"]
        try:
            da = _get_data_access(request)
            reward_budget = int(request.POST.get("reward_budget", 0))
            org_id = request.POST.get("org_id", "")
            da.award_response(pk, reward_budget=reward_budget, org_id=org_id)
            # Redirect back to the responses list for the parent solicitation
            response = da.get_response_by_id(pk)
            if response:
                return redirect("solicitations:responses_list", pk=response.solicitation_id)
            return redirect("solicitations:manage_list")
        except Exception:
            logger.exception("Failed to award response %s", pk)
            ctx = self.get_context_data(**kwargs)
            ctx["error"] = "Failed to award response. Please try again."
            return self.render_to_response(ctx)


# -- Review Views (manager required) ---------------------------------------


class ReviewView(ManagerRequiredMixin, TemplateView):
    """Create or update a review for a response."""

    template_name = "solicitations/review_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        try:
            da = _get_data_access(self.request)
            response = da.get_response_by_id(pk)
            if not response:
                raise Http404("Response not found")
            ctx["response"] = response

            # Load parent solicitation
            solicitation = da.get_solicitation_by_id(response.solicitation_id)
            ctx["solicitation"] = solicitation

            # Build criteria-by-question lookup
            criteria_by_question = {}
            eval_criteria = solicitation.evaluation_criteria if solicitation else []
            for criterion in eval_criteria:
                for q_id in criterion.get("linked_questions", []):
                    criteria_by_question.setdefault(q_id, []).append(criterion)

            # Build Q&A pairs with linked criteria for inline scoring
            qa_pairs = []
            if solicitation and solicitation.questions:
                for question in solicitation.questions:
                    q_id = question.get("id", "")
                    qa_pairs.append(
                        {
                            "question": question.get("text", ""),
                            "answer": response.responses.get(q_id, ""),
                            "question_id": q_id,
                            "criteria": criteria_by_question.get(q_id, []),
                        }
                    )
            ctx["qa_pairs"] = qa_pairs

            # Check for existing review by current user
            reviews = da.get_reviews_for_response(pk)
            reviewer_username = self.request.user.username
            existing_review = None
            for review in reviews:
                if review.reviewer_username == reviewer_username:
                    existing_review = review
                    break

            if existing_review:
                ctx["existing_review"] = existing_review
                ctx["is_update"] = True
                ctx["form"] = ReviewForm(
                    evaluation_criteria=eval_criteria,
                    initial={
                        "score": existing_review.score,
                        "recommendation": existing_review.recommendation,
                        "notes": existing_review.notes,
                        "tags": existing_review.tags,
                    },
                )
            else:
                ctx["is_update"] = False
                ctx["form"] = ReviewForm(evaluation_criteria=eval_criteria)
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load response %s for review", pk)
            raise Http404("Response not found")
        return ctx

    def post(self, request, *args, **kwargs):
        pk = kwargs["pk"]
        try:
            da = _get_data_access(request)
            response = da.get_response_by_id(pk)
            if not response:
                raise Http404("Response not found")
        except Http404:
            raise
        except Exception:
            logger.exception("Failed to load response %s for review POST", pk)
            raise Http404("Response not found")

        # Load solicitation for evaluation criteria
        try:
            solicitation = da.get_solicitation_by_id(response.solicitation_id)
            eval_criteria = solicitation.evaluation_criteria if solicitation else []
        except Exception:
            eval_criteria = []

        form = ReviewForm(eval_criteria, request.POST)
        if form.is_valid():
            reviewer_username = request.user.username
            data = {
                "response_id": pk,
                "llo_entity_id": response.llo_entity_id,
                "score": form.cleaned_data["score"],
                "recommendation": form.cleaned_data["recommendation"],
                "notes": form.cleaned_data["notes"],
                "tags": form.cleaned_data["tags"],
                "reviewer_username": reviewer_username,
                "review_date": timezone.now().isoformat(),
                "criteria_scores": form.get_criteria_scores(),
            }

            try:
                # Check for existing review by this user
                reviews = da.get_reviews_for_response(pk)
                existing_review = None
                for review in reviews:
                    if review.reviewer_username == reviewer_username:
                        existing_review = review
                        break

                if existing_review:
                    da.update_review(existing_review.pk, data)
                else:
                    da.create_review(response_id=pk, data=data)

                return redirect("solicitations:response_detail", pk=pk)
            except Exception:
                logger.exception("Failed to save review for response %s", pk)
                ctx = self.get_context_data(**kwargs)
                ctx["form"] = form
                ctx["error"] = "Failed to save review. Please try again."
                return self.render_to_response(ctx)
        else:
            ctx = self.get_context_data(**kwargs)
            ctx["form"] = form
            return self.render_to_response(ctx)
