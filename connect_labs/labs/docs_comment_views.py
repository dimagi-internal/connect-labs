"""JSON endpoints for comments on Documentations pages.

Mounted by connect_labs/labs/urls.py under /labs/docs/api/comments/.
CSRF is enforced (the page supplies the token), so these are not csrf_exempt.
Storage is the labs-local ``DocComment`` model — see docs_comments.
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from connect_labs.labs import docs_comments

logger = logging.getLogger(__name__)

# Only pages that exist under /labs/docs/ may be commented on, so a caller
# can't use this endpoint as general-purpose key/value storage.
ALLOWED_DOC_KEYS = {"chc"}


@login_required
@require_http_methods(["GET", "POST"])
def doc_comments(request: HttpRequest, doc_key: str) -> JsonResponse:
    """GET lists every comment on the page; POST adds one."""
    if doc_key not in ALLOWED_DOC_KEYS:
        return JsonResponse({"error": f"Unknown documentation page '{doc_key}'"}, status=404)

    if request.method == "GET":
        return JsonResponse(
            {
                "comments": docs_comments.list_comments(doc_key),
                "current_username": request.user.username,
            }
        )

    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)

    try:
        comment = docs_comments.create_comment(
            doc_key=doc_key,
            body=body.get("body", ""),
            author=request.user,
            author_name=request.user.name or request.user.username,
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse({"comment": comment}, status=201)


@login_required
@require_http_methods(["POST"])
def delete_doc_comment(request: HttpRequest, doc_key: str, comment_id: int) -> JsonResponse:
    """Delete one comment. Only its author may delete it."""
    if doc_key not in ALLOWED_DOC_KEYS:
        return JsonResponse({"error": f"Unknown documentation page '{doc_key}'"}, status=404)

    if not docs_comments.delete_comment(comment_id, doc_key=doc_key, author=request.user):
        return JsonResponse({"error": "Comment not found, or it isn't yours to delete"}, status=403)
    return JsonResponse({"deleted": comment_id})
