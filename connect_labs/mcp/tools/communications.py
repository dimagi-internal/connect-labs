"""communications_* -- prepare messages for a person to send from labs.

An agent writes drafts here: to a round's applicants, a tender's suppliers, an
award's partners, or anyone an organisation writes to. **None of these tools
sends.** Sending is a person's act, in labs, on the draft's page
(`connect_labs.communications.service.send`, which refuses a token-carrying
request). Each draft's result carries `url`: give it to the person.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from ..tool_registry import MCPToolError, register

_RECIPIENT = {
    "type": "object",
    "properties": {
        "id": {"type": "integer", "description": "An existing recipient's id, to keep what labs attached to it."},
        "email": {"type": "string"},
        "name": {"type": "string"},
        "org": {"type": "string", "description": "The organisation's labs slug."},
        "cc": {"type": "array", "items": {"type": "string"}},
        "subject": {"type": "string", "description": "This person's own subject (blank: the message's)."},
        "body": {"type": "string", "description": "This person's own text (blank: the message's)."},
        "label": {"type": "string"},
        "included": {"type": "boolean"},
    },
    "additionalProperties": False,
}


def _url(pk) -> str:
    path = reverse("communications:detail", args=[pk])
    base = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    return base + path if base else path


def _errors(fn):
    from connect_labs.communications.service import NotFound

    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except NotFound as e:
            raise MCPToolError("NOT_FOUND", f"No message or record {e} that you may write about.") from e
        except PermissionDenied as e:
            raise MCPToolError("PERMISSION_DENIED", str(e)) from e
        except (TypeError, ValueError) as e:
            raise MCPToolError("BAD_REQUEST", str(e)) from e
        except ValidationError as e:
            detail = e.message_dict if hasattr(e, "message_dict") else {"error": e.messages}
            raise MCPToolError("BAD_REQUEST", "; ".join(f"{k}: {' '.join(v)}" for k, v in detail.items())) from e

    wrapped.__name__ = fn.__name__
    return wrapped


def _summary(communication, *, full: bool = False, user=None) -> dict[str, Any]:
    from connect_labs.communications import kinds, service

    kind = kinds.get(communication.kind)
    about = service.about_of(communication)
    out = {
        "id": communication.pk,
        "url": _url(communication.pk),
        "kind": communication.kind,
        "status": communication.status,
        "title": communication.title,
        "about": (
            {"label": kind.about_label(about), "id": communication.about_id}
            if about is not None
            else ({"ref": communication.about_id} if communication.about_id else None)
        ),
        "reply_to": communication.reply_to,
        "prepared_via": communication.prepared_via,
        "sent_at": communication.sent_at.isoformat() if communication.sent_at else None,
    }
    if full:
        recipients = list(communication.recipients.select_related("org"))
        out.update(
            {
                "subject": communication.subject,
                "body": communication.body,
                "placeholders": list(kind.placeholders),
                "recipients": [
                    {
                        "id": r.pk,
                        "label": r.label,
                        "name": r.name,
                        "email": r.email,
                        "org": r.org.slug if r.org_id else None,
                        "cc": r.cc,
                        "included": r.included,
                        "subject": r.subject,
                        "body": r.body,
                        "note": (r.ref or {}).get("blocked")
                        or (r.ref or {}).get("note")
                        or (r.ref or {}).get("why")
                        or "",
                    }
                    for r in recipients
                ],
                "problems": service.problems(communication, kind) if communication.is_draft else {},
                "preview": service.preview(user, communication.pk) if recipients and user is not None else None,
                "sent": [
                    {"to": m.to_email, "subject": m.subject, "delivery": m.delivery, "sent_at": m.sent_at.isoformat()}
                    for m in communication.sent_messages.all()
                ],
            }
        )
    return out


@register(
    name="communications_kinds",
    description=(
        "The kinds of message labs can draft: what each is about, who prepares and sends it, and the "
        "placeholders its text may use (filled in per person, e.g. {first_name}, {link}). Read this before "
        "communications_draft_create. Labs never sends from an agent: a person sends each draft in labs."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def communications_kinds(user) -> dict[str, Any]:
    from connect_labs.communications import kinds

    return {
        "kinds": [
            {
                "kind": k.key,
                "label": k.label,
                "about": k.about_model or "anything (about_ref) or nothing",
                "placeholders": list(k.placeholders),
                "description": k.description,
            }
            for k in kinds.all_kinds()
            if k.description
        ]
    }


@register(
    name="communications_draft_create",
    description=(
        "Prepare a message for a person to send from labs. Labs writes it as its kind does -- the applicants "
        "of a round with their invitation links, the emails a tender owes each supplier -- and anything you "
        "pass replaces that: `subject`, `body` (with the kind's placeholders), `reply_to` (defaults to you), "
        "`recipients` (replaces the list -- only for a `general` message; a round's applicants and a tender's or "
        "award's emails are addressed by labs, so omit it and choose among them afterwards with "
        "communications_draft_update `included`). `about_id` names the record for a kind about one (a round, a "
        "tender, an award); a `general` message names `params.owner_org` (the organisation it is sent for) "
        'and may name any record as `about_ref` ("app_label.model:id", e.g. a workflow run). Returns the '
        "draft with its `url`. THIS DOES NOT SEND: give the person the url; they review and send it in labs."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string"},
            "about_id": {"type": ["integer", "string"]},
            "about_ref": {"type": "string"},
            "params": {"type": "object"},
            "title": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "reply_to": {"type": "string"},
            "recipients": {"type": "array", "items": _RECIPIENT},
        },
        "required": ["kind"],
        "additionalProperties": False,
    },
    is_write=True,
)
@_errors
def communications_draft_create(
    user,
    kind,
    about_id=None,
    about_ref="",
    params=None,
    title=None,
    subject=None,
    body=None,
    reply_to=None,
    recipients=None,
) -> dict[str, Any]:
    from connect_labs.communications import service
    from connect_labs.communications.models import PREPARED_MCP

    communication = service.create_draft(
        user,
        kind,
        about_id=about_id,
        about_ref=about_ref or "",
        params=params or {},
        via=PREPARED_MCP,
        title=title,
        subject_line=subject,
        body=body,
        reply_to=reply_to,
        recipients=recipients,
    )
    return _summary(communication, full=True, user=user)


@register(
    name="communications_draft_update",
    description=(
        "Change a draft before a person sends it: `title`, `subject`, `body`, `reply_to`, `recipients` (the "
        "whole list; name an existing recipient's `id` to keep it), `included` (the recipient ids to send "
        "to), or `emails` (a recipient's address). A kind that fixes its recipients (a round's applicants, a "
        "tender's suppliers) refuses `recipients`, and a round's also `emails`; its title, subject, body and "
        "reply-to change like any draft's. Only a draft can change. Does not send."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "title": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "reply_to": {"type": "string"},
            "recipients": {"type": "array", "items": _RECIPIENT},
            "included": {"type": "array", "items": {"type": "integer"}},
            "emails": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Recipient id -> corrected address, where the kind allows it.",
            },
        },
        "required": ["id"],
        "additionalProperties": False,
    },
    is_write=True,
)
@_errors
def communications_draft_update(user, id, **data) -> dict[str, Any]:  # noqa: A002 -- the tool's own name for it
    from connect_labs.communications import service

    communication = service.update_draft(user, id, data)
    return _summary(communication, full=True, user=user)


@register(
    name="communications_draft_discard",
    description="Discard a draft nobody should send. Only a draft. Nothing is sent.",
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
        "additionalProperties": False,
    },
    is_write=True,
)
@_errors
def communications_draft_discard(user, id) -> dict[str, Any]:  # noqa: A002
    from connect_labs.communications import service

    return _summary(service.discard(user, id))


@register(
    name="communications_get",
    description=(
        "One message: its text, recipients (with what each is for), what stops it being sent, a preview of the "
        "first recipient's copy, and -- once sent -- what went to whom."
    ),
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
        "additionalProperties": False,
    },
)
@_errors
def communications_get(user, id) -> dict[str, Any]:  # noqa: A002
    from connect_labs.communications import service

    communication, _, _ = service.load(user, id)
    return _summary(communication, full=True, user=user)


@register(
    name="communications_list",
    description=(
        "Messages you may prepare: drafts waiting for a person to send, and those sent. Narrow by `kind` and "
        "`about_id` (one round, tender or award), `about_ref` (a general message's reference), or `status` "
        "(draft, sent, discarded)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string"},
            "about_id": {"type": ["integer", "string"]},
            "status": {"type": "string", "enum": ["draft", "sent", "discarded"]},
            "about_ref": {
                "type": "string",
                "description": "A general message's reference, as written when drafted (e.g. workflow.run:812).",
            },
        },
        "additionalProperties": False,
    },
)
@_errors
def communications_list(user, kind=None, about_id=None, status=None, about_ref=None) -> dict[str, Any]:
    from django.contrib.contenttypes.models import ContentType

    from connect_labs.communications import kinds, service
    from connect_labs.communications.models import Communication

    qs = Communication.objects.all()
    if kind:
        qs = qs.filter(kind=kind)
        if about_id is not None:
            k = kinds.get(kind)
            about = k.resolve(about_id) if k.about_model else None
            if about is None:
                return {"communications": []}
            qs = qs.filter(about_type=ContentType.objects.get_for_model(about), about_id=str(about.pk))
    if about_ref:
        from connect_labs.solicitations.access import caller_for

        linked = kinds.get("general").resolve_about(about_ref, caller_for(user))
        if linked.record is not None:
            qs = qs.filter(about_type=ContentType.objects.get_for_model(linked.record), about_id=str(linked.record.pk))
        else:
            qs = qs.filter(about_type__isnull=True, about_id=linked.ref)
    if status:
        qs = qs.filter(status=status)
    return {"communications": [_summary(c) for c in service.visible(user, qs, limit=100)]}
