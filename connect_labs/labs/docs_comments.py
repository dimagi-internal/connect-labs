"""Comments on Documentations pages.

Stored locally in the labs database via the ``DocComment`` model — NOT in the
Connect LabsRecord store. These are notes about labs' own documentation, so
there is no opportunity/program/organization to scope them by and no reason for
them to live on production Connect.

Comments are global: every logged-in labs user sees the same thread on a page
regardless of their selected context, which is the point of the Documentations
section (see ``LabsDocsView``). Deletion is author-only, enforced here.
"""

import logging

from connect_labs.labs.models import DocComment

logger = logging.getLogger(__name__)

MAX_BODY_LENGTH = 5000
# Match the model's field widths so an over-long anchor is trimmed, not an error.
MAX_SECTION_ID_LENGTH = 128
MAX_SECTION_LABEL_LENGTH = 255


def list_comments(doc_key: str) -> list[dict]:
    """Return every comment on ``doc_key``, oldest first."""
    comments = DocComment.objects.filter(doc_key=doc_key).select_related("author")
    return [comment.as_dict() for comment in comments]


def create_comment(
    doc_key: str,
    body: str,
    author,
    author_name: str = "",
    section_id: str = "",
    section_label: str = "",
) -> dict:
    """Persist one comment and return its serialized form.

    ``section_id`` anchors the comment to an element in the document; blank
    means the comment is about the page as a whole. Both section fields are
    truncated rather than rejected — a renamed heading is not worth failing a
    comment over.

    Raises:
        ValueError: if the body is empty or over ``MAX_BODY_LENGTH``.
    """
    # str() rather than a type check: these arrive from JSON, and a number or
    # bool is harmless once stringified.
    body = str(body or "").strip()
    if not body:
        raise ValueError("Comment cannot be empty")
    if len(body) > MAX_BODY_LENGTH:
        raise ValueError(f"Comment is too long (limit {MAX_BODY_LENGTH} characters)")

    comment = DocComment.objects.create(
        doc_key=doc_key,
        body=body,
        author=author,
        author_name=author_name or author.username,
        section_id=str(section_id or "").strip()[:MAX_SECTION_ID_LENGTH],
        section_label=str(section_label or "").strip()[:MAX_SECTION_LABEL_LENGTH],
    )
    return comment.as_dict()


def delete_comment(comment_id: int, doc_key: str, author) -> bool:
    """Delete a comment the requesting user authored.

    Returns False when the comment is missing or belongs to someone else — the
    caller maps that onto a 403.
    """
    deleted, _ = DocComment.objects.filter(id=comment_id, doc_key=doc_key, author=author).delete()
    if not deleted:
        logger.warning("User %s could not delete doc comment %s (missing or not theirs)", author.username, comment_id)
    return bool(deleted)
