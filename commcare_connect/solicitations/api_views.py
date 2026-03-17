"""
JSON API views for solicitations.

Function-based views returning JsonResponse for solicitations, responses, and reviews.
All data access goes through SolicitationsDataAccess (API-backed, no local DB).
"""

import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from commcare_connect.solicitations.data_access import SolicitationsDataAccess

logger = logging.getLogger(__name__)


def _get_data_access(request):
    """Create data access from request."""
    return SolicitationsDataAccess(request=request)


# =========================================================================
# Serializers
# =========================================================================


def _serialize_solicitation(s):
    """Serialize a SolicitationRecord to a JSON-safe dict."""
    return {
        "id": s.pk,
        "title": s.title,
        "description": s.description,
        "scope_of_work": s.scope_of_work,
        "solicitation_type": s.solicitation_type,
        "status": s.status,
        "is_public": s.is_public,
        "questions": s.questions,
        "application_deadline": s.application_deadline.isoformat() if s.application_deadline else None,
        "expected_start_date": s.expected_start_date.isoformat() if s.expected_start_date else None,
        "expected_end_date": s.expected_end_date.isoformat() if s.expected_end_date else None,
        "estimated_scale": s.estimated_scale,
        "contact_email": s.contact_email,
        "created_by": s.created_by,
        "program_name": s.program_name,
    }


def _serialize_response(r):
    """Serialize a ResponseRecord to a JSON-safe dict."""
    return {
        "id": r.pk,
        "solicitation_id": r.solicitation_id,
        "llo_entity_id": r.llo_entity_id,
        "llo_entity_name": r.llo_entity_name,
        "responses": r.responses,
        "status": r.status,
        "submitted_by_name": r.submitted_by_name,
        "submitted_by_email": r.submitted_by_email,
        "submission_date": r.submission_date.isoformat() if r.submission_date else None,
    }


def _serialize_review(rv):
    """Serialize a ReviewRecord to a JSON-safe dict."""
    return {
        "id": rv.pk,
        "response_id": rv.response_id,
        "score": rv.score,
        "recommendation": rv.recommendation,
        "notes": rv.notes,
        "tags": rv.tags,
        "reviewer_username": rv.reviewer_username,
        "review_date": rv.review_date.isoformat() if rv.review_date else None,
    }


def _parse_json_body(request):
    """Parse JSON from request body. Returns (data_dict, error_response)."""
    try:
        data = json.loads(request.body)
        return data, None
    except (json.JSONDecodeError, ValueError):
        return None, JsonResponse({"error": "Invalid JSON body"}, status=400)


# =========================================================================
# Solicitation Views
# =========================================================================


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_solicitations_list(request):
    """
    GET: List solicitations with optional filters (?status, ?type, ?is_public).
    POST: Create a new solicitation.
    """
    try:
        da = _get_data_access(request)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=401)

    if request.method == "GET":
        status_filter = request.GET.get("status")
        type_filter = request.GET.get("type")
        is_public = request.GET.get("is_public")

        try:
            if is_public and is_public.lower() in ("true", "1"):
                solicitations = da.get_public_solicitations(solicitation_type=type_filter)
            else:
                solicitations = da.get_solicitations(
                    status=status_filter,
                    solicitation_type=type_filter,
                )
            return JsonResponse(
                {"solicitations": [_serialize_solicitation(s) for s in solicitations]},
            )
        except Exception:
            logger.exception("API: Failed to list solicitations")
            return JsonResponse({"error": "Failed to retrieve solicitations"}, status=500)

    # POST
    data, err = _parse_json_body(request)
    if err:
        return err

    try:
        solicitation = da.create_solicitation(data)
        return JsonResponse(
            {"solicitation": _serialize_solicitation(solicitation)},
            status=201,
        )
    except Exception:
        logger.exception("API: Failed to create solicitation")
        return JsonResponse({"error": "Failed to create solicitation"}, status=500)


@csrf_exempt
@require_http_methods(["GET", "PUT"])
def api_solicitation_detail(request, pk):
    """
    GET: Retrieve a single solicitation by ID.
    PUT: Update an existing solicitation.
    """
    try:
        da = _get_data_access(request)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=401)

    if request.method == "GET":
        try:
            solicitation = da.get_solicitation_by_id(pk)
            if not solicitation:
                return JsonResponse({"error": "Solicitation not found"}, status=404)
            return JsonResponse({"solicitation": _serialize_solicitation(solicitation)})
        except Exception:
            logger.exception("API: Failed to get solicitation %s", pk)
            return JsonResponse({"error": "Failed to retrieve solicitation"}, status=500)

    # PUT
    data, err = _parse_json_body(request)
    if err:
        return err

    try:
        solicitation = da.update_solicitation(pk, data)
        return JsonResponse({"solicitation": _serialize_solicitation(solicitation)})
    except Exception:
        logger.exception("API: Failed to update solicitation %s", pk)
        return JsonResponse({"error": "Failed to update solicitation"}, status=500)


# =========================================================================
# Response Views
# =========================================================================


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_responses_list(request):
    """
    GET: List responses (requires ?solicitation_id filter).
    POST: Create a new response.
    """
    try:
        da = _get_data_access(request)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=401)

    if request.method == "GET":
        solicitation_id = request.GET.get("solicitation_id")
        if not solicitation_id:
            return JsonResponse(
                {"error": "solicitation_id query parameter is required"},
                status=400,
            )
        try:
            solicitation_id = int(solicitation_id)
        except (ValueError, TypeError):
            return JsonResponse({"error": "solicitation_id must be an integer"}, status=400)

        try:
            responses = da.get_responses_for_solicitation(solicitation_id)
            return JsonResponse(
                {"responses": [_serialize_response(r) for r in responses]},
            )
        except Exception:
            logger.exception("API: Failed to list responses for solicitation %s", solicitation_id)
            return JsonResponse({"error": "Failed to retrieve responses"}, status=500)

    # POST
    data, err = _parse_json_body(request)
    if err:
        return err

    solicitation_id = data.get("solicitation_id")
    llo_entity_id = data.get("llo_entity_id", "")
    if not solicitation_id:
        return JsonResponse({"error": "solicitation_id is required"}, status=400)

    try:
        response = da.create_response(
            solicitation_id=solicitation_id,
            llo_entity_id=llo_entity_id,
            data=data,
        )
        return JsonResponse(
            {"response": _serialize_response(response)},
            status=201,
        )
    except Exception:
        logger.exception("API: Failed to create response")
        return JsonResponse({"error": "Failed to create response"}, status=500)


@csrf_exempt
@require_http_methods(["GET", "PUT"])
def api_response_detail(request, pk):
    """
    GET: Retrieve a single response by ID.
    PUT: Update an existing response.
    """
    try:
        da = _get_data_access(request)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=401)

    if request.method == "GET":
        try:
            response = da.get_response_by_id(pk)
            if not response:
                return JsonResponse({"error": "Response not found"}, status=404)
            return JsonResponse({"response": _serialize_response(response)})
        except Exception:
            logger.exception("API: Failed to get response %s", pk)
            return JsonResponse({"error": "Failed to retrieve response"}, status=500)

    # PUT
    data, err = _parse_json_body(request)
    if err:
        return err

    try:
        response = da.update_response(pk, data)
        return JsonResponse({"response": _serialize_response(response)})
    except Exception:
        logger.exception("API: Failed to update response %s", pk)
        return JsonResponse({"error": "Failed to update response"}, status=500)


# =========================================================================
# Review Views
# =========================================================================


@csrf_exempt
@require_http_methods(["POST"])
def api_reviews_create(request):
    """
    POST: Create a new review.
    """
    try:
        da = _get_data_access(request)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=401)

    data, err = _parse_json_body(request)
    if err:
        return err

    response_id = data.get("response_id")
    if not response_id:
        return JsonResponse({"error": "response_id is required"}, status=400)

    try:
        review = da.create_review(response_id=response_id, data=data)
        return JsonResponse(
            {"review": _serialize_review(review)},
            status=201,
        )
    except Exception:
        logger.exception("API: Failed to create review")
        return JsonResponse({"error": "Failed to create review"}, status=500)


@csrf_exempt
@require_http_methods(["GET", "PUT"])
def api_review_detail(request, pk):
    """
    GET: Retrieve a single review by ID.
    PUT: Update an existing review.
    """
    try:
        da = _get_data_access(request)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=401)

    if request.method == "GET":
        try:
            review = da.get_review_by_id(pk)
            if not review:
                return JsonResponse({"error": "Review not found"}, status=404)
            return JsonResponse({"review": _serialize_review(review)})
        except Exception:
            logger.exception("API: Failed to get review %s", pk)
            return JsonResponse({"error": "Failed to retrieve review"}, status=500)

    # PUT
    data, err = _parse_json_body(request)
    if err:
        return err

    try:
        review = da.update_review(pk, data)
        return JsonResponse({"review": _serialize_review(review)})
    except Exception:
        logger.exception("API: Failed to update review %s", pk)
        return JsonResponse({"error": "Failed to update review"}, status=500)
