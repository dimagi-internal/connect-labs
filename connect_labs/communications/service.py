"""Preparing, previewing and sending communications.

Anyone the kind allows may PREPARE a draft: a person on a page, or an agent
over MCP (`prepared_via="mcp"`). Only a person may SEND, and only from labs in
a browser: `send` takes the request and refuses one that carries a bearer
token, and no MCP tool calls it. That is the whole rule, and it is here so no
caller can route around it.
"""

from __future__ import annotations

import logging

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from connect_labs.communications import kinds as kind_registry
from connect_labs.communications.kinds import RecipientSpec, render
from connect_labs.communications.models import (
    DRAFT,
    NOT_SENT,
    PREPARED_WEB,
    QUEUED,
    SENT,
    Communication,
    Recipient,
    SentMessage,
)
from connect_labs.solicitations.access import Caller, caller_for

logger = logging.getLogger(__name__)

#: SES takes one message a second from labs: a batch spaced a little wider
#: does not lean on retries.
SEND_SPACING_SECONDS = 1.2
MAX_SUBJECT = 300
MAX_BODY = 20_000
MAX_RECIPIENTS = 500


class NotFound(Exception):
    pass


# -- reading -------------------------------------------------------------------


def about_of(communication):
    if communication.about_type_id is None:
        return None
    return communication.about


def load(subject, pk, *, for_send: bool = False):
    """(communication, kind, about) the caller may prepare (or send); NotFound otherwise."""
    caller = caller_for(subject)
    communication = Communication.objects.select_related("owner_org", "about_type").filter(pk=pk).first()
    if communication is None:
        raise NotFound(pk)
    kind = kind_registry.get(communication.kind)
    about = about_of(communication)
    allowed = kind.can_send if for_send else kind.can_prepare
    if not allowed(caller, about, communication):
        raise NotFound(pk)
    return communication, kind, about


def visible(subject, queryset=None, limit: int = 200) -> list:
    """Communications the caller may prepare, newest first."""
    caller = caller_for(subject)
    rows = (queryset if queryset is not None else Communication.objects.all()).select_related(
        "owner_org", "about_type", "prepared_by", "sent_by"
    )[: limit * 2]
    out = []
    for communication in rows:
        try:
            kind = kind_registry.get(communication.kind)
        except ValidationError:
            continue
        if kind.can_prepare(caller, about_of(communication), communication):
            out.append(communication)
        if len(out) >= limit:
            break
    return out


def for_about(about):
    """Every communication and sent message about one record."""
    ct = ContentType.objects.get_for_model(about)
    return (
        Communication.objects.filter(about_type=ct, about_id=str(about.pk)),
        SentMessage.objects.filter(about_type=ct, about_id=str(about.pk)).select_related("org", "sent_by"),
    )


# -- preparing -----------------------------------------------------------------


def _clean_recipients(raw) -> list[RecipientSpec]:
    from connect_labs.solicitations.access import resolve_org

    out = []
    for i, item in enumerate(raw or []):
        if not isinstance(item, dict):
            raise ValidationError({"recipients": f"recipient {i + 1} must be an object"})
        email = str(item.get("email") or "").strip()
        if email:
            try:
                validate_email(email)
            except ValidationError as e:
                raise ValidationError({"recipients": f"recipient {i + 1}: {email!r} is not an email address"}) from e
        org = resolve_org(item.get("org")) if item.get("org") else None
        out.append(
            RecipientSpec(
                email=email,
                name=str(item.get("name") or "").strip()[:200],
                org=org,
                cc=[str(c).strip() for c in item.get("cc") or [] if str(c).strip()],
                subject=str(item.get("subject") or "")[:MAX_SUBJECT],
                body=str(item.get("body") or "")[:MAX_BODY],
                label=str(item.get("label") or "")[:200],
                included=bool(item.get("included", True)),
            )
        )
    if len(out) > MAX_RECIPIENTS:
        raise ValidationError({"recipients": f"at most {MAX_RECIPIENTS} recipients in one message"})
    return out


def _write_recipients(communication, specs) -> None:
    communication.recipients.all().delete()
    Recipient.objects.bulk_create(
        [
            Recipient(
                communication=communication,
                org=spec.org,
                name=spec.name,
                email=spec.email,
                cc=list(spec.cc or []),
                subject=spec.subject,
                body=spec.body,
                label=spec.label,
                ref=spec.ref or {},
                included=spec.included,
                position=i,
            )
            for i, spec in enumerate(specs)
        ]
    )


@transaction.atomic
def create_draft(
    subject,
    kind_key: str,
    *,
    about_id=None,
    about_ref: str = "",
    params: dict | None = None,
    via: str = PREPARED_WEB,
    title: str | None = None,
    subject_line: str | None = None,
    body: str | None = None,
    reply_to: str | None = None,
    recipients: list | None = None,
) -> Communication:
    """A new draft of `kind` about a record, as the kind writes it, with any of it overridden."""
    caller = caller_for(subject)
    if not caller.is_authenticated:
        raise PermissionDenied("Sign in to write a message.")
    kind = kind_registry.get(kind_key)
    if kind.about_model:
        about = kind.resolve(about_id)
        if about is None:
            raise NotFound(about_id)
    else:
        about = kind.resolve_about(about_ref) if hasattr(kind, "resolve_about") else None
    spec = kind.new_draft(caller, about, dict(params or {}))
    communication = Communication(
        kind=kind.key,
        owner_org=spec.owner_org,
        program_id=spec.program_id,
        title=(title or spec.title)[:200],
        subject=(subject_line if subject_line is not None else spec.subject)[:MAX_SUBJECT],
        body=(body if body is not None else spec.body)[:MAX_BODY],
        reply_to=(reply_to if reply_to is not None else (getattr(caller.user, "email", "") or "")).strip(),
        context=spec.context or {},
        prepared_by=caller.user,
        prepared_via=via,
    )
    if about is not None:
        communication.about_type = ContentType.objects.get_for_model(about)
        communication.about_id = str(about.pk)
    if not kind.can_prepare(caller, about, communication):
        raise PermissionDenied(f"You can't write {kind.label.lower()}s about this.")
    communication.save()
    _write_recipients(communication, _clean_recipients(recipients) if recipients is not None else spec.recipients)
    return communication


@transaction.atomic
def update_draft(subject, pk, data: dict) -> Communication:
    """Change a draft: its title, subject, body, reply-to, recipients or which are included."""
    communication, kind, about = load(subject, pk)
    if not communication.is_draft:
        raise ValidationError({"status": "Only a draft can be changed; this one is " + communication.status + "."})
    unknown = set(data) - {"title", "subject", "body", "reply_to", "recipients", "included"}
    if unknown:
        raise ValidationError({name: "cannot be changed" for name in sorted(unknown)})
    for name, limit in (("title", 200), ("subject", MAX_SUBJECT), ("body", MAX_BODY), ("reply_to", 320)):
        if name in data:
            setattr(communication, name, str(data[name] or "")[:limit].strip() if name != "body" else str(data[name]))
    communication.save()
    if "recipients" in data:
        # A recipient named by its `id` keeps what the kind attached to it (the
        # outreach row a reminder marks sent); a new one carries nothing.
        refs = {r.pk: r.ref for r in communication.recipients.all()}
        specs = _clean_recipients(data["recipients"])
        for spec, item in zip(specs, data["recipients"]):
            spec.ref = refs.get(item.get("id"), {}) if isinstance(item, dict) else {}
        _write_recipients(communication, specs)
    if "included" in data:
        chosen = {int(i) for i in data["included"]}
        for recipient in communication.recipients.all():
            recipient.included = recipient.pk in chosen
            recipient.save(update_fields=["included"])
    return communication


def discard(subject, pk) -> Communication:
    communication, kind, about = load(subject, pk)
    if not communication.is_draft:
        raise ValidationError({"status": "Only a draft can be discarded."})
    communication.status = "discarded"
    communication.save(update_fields=["status", "updated_at"])
    return communication


# -- previewing ----------------------------------------------------------------


def copy_for(communication, kind, about, recipient, *, caller, sending: bool):
    """(subject, body, filled) as this recipient reads it."""
    filled = kind.fill(communication, recipient, about, sending=sending, caller=caller)
    values = filled.values if sending else filled.recorded
    subject = render(recipient.subject or communication.subject, values).strip()
    body = render(recipient.body or communication.body, values)
    return subject, body, filled


def preview(subject, pk, recipient_id=None) -> dict:
    """One person's copy as they will read it, credentials shown as what they are."""
    caller = caller_for(subject)
    communication, kind, about = load(caller, pk)
    recipients = list(communication.recipients.all())
    recipient = next((r for r in recipients if r.pk == recipient_id), None) or next(
        (r for r in recipients if r.included), recipients[0] if recipients else None
    )
    if recipient is None:
        return {"recipient": None}
    subject_line, body, _ = copy_for(communication, kind, about, recipient, caller=caller, sending=False)
    return {
        "recipient": {"id": recipient.pk, "name": recipient.name, "email": recipient.email, "cc": recipient.cc},
        "reply_to": communication.reply_to,
        "subject": subject_line,
        "body": body,
    }


def problems(communication, kind) -> dict:
    """What stops this draft being sent, by field ({} when nothing)."""
    errors = {}
    included = [r for r in communication.recipients.all() if r.included]
    if not included:
        errors["recipients"] = "Choose at least one person to send to."
    missing = [r.label or r.name or f"recipient {r.position + 1}" for r in included if not r.email]
    if missing:
        errors["recipients"] = "No email address for " + ", ".join(missing) + "."
    if not all((r.subject or communication.subject).strip() for r in included):
        errors["subject"] = "Write a subject."
    if not all((r.body or communication.body).strip() for r in included):
        errors["body"] = "Write a message."
    try:
        validate_email((communication.reply_to or "").strip())
    except ValidationError:
        errors["reply_to"] = "Give the address replies should go to."
    return errors


# -- sending: a person, in labs ------------------------------------------------


def send(request, pk) -> dict:
    """Send a draft to everyone included in it, as the person making this request.

    Only from a browser session: a request carrying a bearer token (an agent's
    PAT, an API client) is refused, and no MCP tool calls this. Each recipient
    gets their own copy; what the kind makes on the way (an invitation) is made
    only now, and the record keeps the copy without it.
    """
    from connect_labs.utils.email import email_enabled, send_labs_email

    if request.headers.get("authorization"):
        raise PermissionDenied("Messages are sent by a person in labs, never by a token.")
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        raise PermissionDenied("Sign in to send.")
    caller = Caller(user=request.user, request=request)
    communication, kind, about = load(caller, pk, for_send=True)
    if not communication.is_draft:
        raise ValidationError({"status": f"This message is {communication.get_status_display().lower()}."})
    kind.before_send(communication, about, caller)
    errors = problems(communication, kind)
    if errors:
        raise ValidationError(errors)
    if not email_enabled():
        raise ValidationError({"__all__": "Outbound email is not set up on this labs, so nothing was sent."})

    # Claim it: a second click (or a second tab) finds it already sent.
    now = timezone.now()
    claimed = Communication.objects.filter(pk=communication.pk, status=DRAFT).update(
        status=SENT, sent_by=request.user, sent_at=now
    )
    if not claimed:
        raise ValidationError({"status": "This message was already sent."})
    communication.refresh_from_db()

    sent, queued_count = [], 0
    for recipient in communication.recipients.filter(included=True).select_related("org"):
        subject_line, body, filled = copy_for(communication, kind, about, recipient, caller=caller, sending=True)
        try:
            queued = (
                send_labs_email(
                    subject_line,
                    body,
                    [recipient.email],
                    reply_to=[communication.reply_to],
                    cc=list(recipient.cc or []),
                    countdown=queued_count * SEND_SPACING_SECONDS or None,
                )
                is not None
            )
        except Exception:  # noqa: BLE001 -- one failed hand-off must not lose the others' records
            logger.exception("communications: could not queue message %s to %s", communication.pk, recipient.email)
            queued = False
        queued_count += int(queued)
        record_subject, record_body, _ = copy_for(communication, kind, about, recipient, caller=caller, sending=False)
        message = SentMessage.objects.create(
            communication=communication,
            recipient=recipient,
            kind=communication.kind,
            about_type=communication.about_type,
            about_id=communication.about_id,
            program_id=communication.program_id,
            org=recipient.org,
            to_email=recipient.email,
            to_name=recipient.name,
            cc=list(recipient.cc or []),
            reply_to=communication.reply_to,
            subject=record_subject,
            body=record_body,
            delivery=QUEUED if queued else NOT_SENT,
            effects=filled.effects,
            sent_by=request.user,
        )
        if queued:
            try:
                effects = kind.after_sent(communication, recipient, message, about, caller) or {}
            except Exception as e:  # noqa: BLE001 -- the email went; say what did not follow it
                logger.exception("communications: after-send step failed for message %s", message.pk)
                effects = {"after_sent_error": str(e)[:500]}
            if effects:
                message.effects = {**message.effects, **effects}
                message.save(update_fields=["effects"])
        sent.append(message)
    return {"communication": communication, "messages": sent, "queued": queued_count}


# -- what labs sends on its own -----------------------------------------------


def record_automatic(kind: str, *, about=None, to, subject: str, body: str, queued: bool, program_id=None, org=None):
    """Record mail labs sent by itself (a receipt, a notice): one row per address."""
    about_type = ContentType.objects.get_for_model(about) if about is not None else None
    rows = []
    for address in [a for a in (to or []) if a]:
        rows.append(
            SentMessage.objects.create(
                kind=kind,
                about_type=about_type,
                about_id=str(about.pk) if about is not None else "",
                program_id=program_id,
                org=org,
                to_email=address,
                subject=subject[:MAX_SUBJECT],
                body=body,
                delivery=QUEUED if queued else NOT_SENT,
            )
        )
    return rows
