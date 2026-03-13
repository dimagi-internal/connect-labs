import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from commcare_connect.solicitations_new.data_access import SolicitationsNewDataAccess
from commcare_connect.solicitations_new.forms import ReviewForm, SolicitationForm, SolicitationResponseForm

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


def _has_program_context(request):
    """Check if the request has a program_id in labs_context."""
    labs_context = getattr(request, "labs_context", {})
    return bool(labs_context.get("program_id"))


def _get_data_access(request):
    """Create data access from request. Works for authed requests."""
    return SolicitationsNewDataAccess(request=request)


# -- Public Views (no login) -----------------------------------------------


class PublicSolicitationListView(TemplateView):
    template_name = "solicitations_new/public_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        solicitation_type = self.request.GET.get("type")
        try:
            da = _get_data_access(self.request)
            ctx["solicitations"] = da.get_public_solicitations(
                solicitation_type=solicitation_type,
            )
        except Exception:
            logger.exception("Failed to load public solicitations")
            ctx["solicitations"] = []
        ctx["selected_type"] = solicitation_type or ""
        return ctx


class PublicSolicitationDetailView(TemplateView):
    template_name = "solicitations_new/public_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pk = kwargs["pk"]
        try:
            da = _get_data_access(self.request)
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

    template_name = "solicitations_new/manage_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["has_context"] = _has_program_context(self.request)
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

    template_name = "solicitations_new/solicitation_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["has_context"] = _has_program_context(self.request)
        ctx["form"] = SolicitationForm()
        ctx["is_create"] = True
        ctx["existing_questions_json"] = "[]"
        return ctx

    def post(self, request, *args, **kwargs):
        if not _has_program_context(request):
            ctx = self.get_context_data(**kwargs)
            ctx["error"] = "Please select a program from the context selector before creating a solicitation."
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
                return redirect("solicitations_new:manage_list")
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

    template_name = "solicitations_new/solicitation_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["has_context"] = _has_program_context(self.request)
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
                return redirect("solicitations_new:manage_list")
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

    template_name = "solicitations_new/responses_list.html"

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

    template_name = "solicitations_new/respond.html"

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
            ctx["form"] = SolicitationResponseForm(questions=solicitation.questions)
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
            return redirect("solicitations_new:public_detail", pk=pk)

        form = SolicitationResponseForm(questions=solicitation.questions, data=request.POST)
        if form.is_valid():
            # Determine status based on which button was pressed
            if "save_draft" in request.POST:
                status = "draft"
            else:
                status = "submitted"

            data = {
                "solicitation_id": pk,
                "responses": form.get_responses_dict(),
                "status": status,
                "submitted_by_name": request.user.get_full_name() or request.user.username,
                "submitted_by_email": request.user.email,
                "submission_date": timezone.now().isoformat(),
            }

            try:
                da.create_response(
                    solicitation_id=pk,
                    llo_entity_id="individual",
                    data=data,
                )
                return redirect("solicitations_new:public_detail", pk=pk)
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

    template_name = "solicitations_new/response_detail.html"

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

    template_name = "solicitations_new/award.html"

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
                return redirect("solicitations_new:responses_list", pk=response.solicitation_id)
            return redirect("solicitations_new:manage_list")
        except Exception:
            logger.exception("Failed to award response %s", pk)
            ctx = self.get_context_data(**kwargs)
            ctx["error"] = "Failed to award response. Please try again."
            return self.render_to_response(ctx)


# -- Review Views (manager required) ---------------------------------------


class ReviewView(ManagerRequiredMixin, TemplateView):
    """Create or update a review for a response."""

    template_name = "solicitations_new/review_form.html"

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

            # Build Q&A pairs for context
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
                    initial={
                        "score": existing_review.score,
                        "recommendation": existing_review.recommendation,
                        "notes": existing_review.notes,
                        "tags": existing_review.tags,
                    }
                )
            else:
                ctx["is_update"] = False
                ctx["form"] = ReviewForm()
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

        form = ReviewForm(request.POST)
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

                return redirect("solicitations_new:response_detail", pk=pk)
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
