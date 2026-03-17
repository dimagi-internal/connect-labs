"""MCP tool definitions for solicitations.

These functions call data_access directly and are registered
with the MCP server for AI agent access.
"""

from commcare_connect.solicitations.data_access import SolicitationsDataAccess


def _serialize_solicitation(s):
    return {
        "id": s.pk,
        "title": s.title,
        "description": s.description,
        "solicitation_type": s.solicitation_type,
        "status": s.status,
        "is_public": s.is_public,
        "application_deadline": s.application_deadline.isoformat() if s.application_deadline else None,
        "estimated_scale": s.estimated_scale,
        "program_name": s.program_name,
    }


def _serialize_response(r):
    return {
        "id": r.pk,
        "solicitation_id": r.solicitation_id,
        "llo_entity_id": r.llo_entity_id,
        "llo_entity_name": r.llo_entity_name,
        "status": r.status,
        "submitted_by_name": r.submitted_by_name,
    }


def _serialize_review(r):
    return {
        "id": r.pk,
        "response_id": r.response_id,
        "score": r.score,
        "recommendation": r.recommendation,
        "reviewer_username": r.reviewer_username,
    }


def list_solicitations(access_token, program_id=None, status=None, solicitation_type=None, is_public=None):
    """List solicitations, optionally filtered."""
    da = SolicitationsDataAccess(program_id=program_id, access_token=access_token)
    if is_public:
        results = da.get_public_solicitations(solicitation_type=solicitation_type)
    else:
        results = da.get_solicitations(status=status, solicitation_type=solicitation_type)
    return [_serialize_solicitation(s) for s in results]


def get_solicitation(access_token, solicitation_id):
    """Get a single solicitation by ID."""
    da = SolicitationsDataAccess(access_token=access_token)
    result = da.get_solicitation_by_id(solicitation_id)
    return _serialize_solicitation(result) if result else None


def create_solicitation(access_token, program_id, data):
    """Create a new solicitation."""
    da = SolicitationsDataAccess(program_id=program_id, access_token=access_token)
    result = da.create_solicitation(data)
    return _serialize_solicitation(result)


def update_solicitation(access_token, solicitation_id, data):
    """Update an existing solicitation."""
    da = SolicitationsDataAccess(access_token=access_token)
    result = da.update_solicitation(solicitation_id, data)
    return _serialize_solicitation(result)


def list_responses(access_token, solicitation_id, status=None):
    """List responses for a solicitation."""
    da = SolicitationsDataAccess(access_token=access_token)
    results = da.get_responses_for_solicitation(solicitation_id)
    if status:
        results = [r for r in results if r.status == status]
    return [_serialize_response(r) for r in results]


def get_response(access_token, response_id):
    """Get a single response by ID."""
    da = SolicitationsDataAccess(access_token=access_token)
    result = da.get_response_by_id(response_id)
    return _serialize_response(result) if result else None


def create_response(access_token, solicitation_id, llo_entity_id, data):
    """Create a new response to a solicitation."""
    da = SolicitationsDataAccess(access_token=access_token)
    result = da.create_response(solicitation_id, llo_entity_id, data)
    return _serialize_response(result)


def create_review(access_token, response_id, data):
    """Create a review for a response."""
    da = SolicitationsDataAccess(access_token=access_token)
    result = da.create_review(response_id, data)
    return _serialize_review(result)


def update_review(access_token, review_id, data):
    """Update an existing review."""
    da = SolicitationsDataAccess(access_token=access_token)
    result = da.update_review(review_id, data)
    return _serialize_review(result)
