"""Preparing, previewing and sending communications.

Anyone the kind allows may PREPARE a draft: a person on a page, or an agent
over MCP (`prepared_via="mcp"`). Only a person may SEND, and only from labs in
a browser: `send` takes the request and refuses one that carries a bearer
token, and no MCP tool calls it. That is the whole rule, and it is here so no
caller can route around it.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Case, Value, When
from django.utils import timezone

from connect_labs.communications import kinds as kind_registry
from connect_labs.communications.kinds import RecipientSpec, render
from connect_labs.communications.models import (
    DELIVERED,
    FAILED,
    FOLLOWUP_DONE,
    FOLLOWUP_FAILED,
    FOLLOWUP_NONE,
    FOLLOWUP_PENDING,
    NOT_SENT,
    PREPARED_WEB,
    QUEUED,
    SENT,
    UNKNOWN,
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
    """Communications the caller may prepare, newest first.

    Filtered row by row in order, until `limit` are found: taking the newest N
    first and filtering after would hide a person's own drafts once others' outnumber them.
    """
    caller = caller_for(subject)
    rows = (queryset if queryset is not None else Communication.objects.all()).select_related(
        "owner_org", "about_type", "prepared_by", "sent_by"
    )
    out = []
    for communication in rows.iterator(chunk_size=200):
        try:
            kind = kind_registry.get(communication.kind)
        except ValidationError:
            continue
        if kind.can_prepare(caller, about_of(communication), communication):
            out.append(communication)
            if len(out) >= limit:
                break
    return out


def sent_about(subject, about, limit: int = 300) -> list:
    """What was sent about one record that the caller may read.

    A message sent from a communication is readable by whoever may prepare
    that communication -- not by whoever may read the record: an organisation's
    general message about a round is its own business, not the round owner's.
    Labs' own notices (no communication) go with the record, to those who may
    write about it.
    """
    if limit <= 0:
        return []
    caller = caller_for(subject)
    ct = ContentType.objects.get_for_model(about)
    rows = SentMessage.objects.filter(about_type=ct, about_id=str(about.pk)).select_related(
        "org", "sent_by", "communication"
    )
    readable: dict[int, bool] = {}
    out = []
    # Filtered in order until `limit` readable ones are found, not sliced first.
    for message in rows.iterator(chunk_size=200):
        communication = message.communication
        if communication is not None:
            if communication.pk not in readable:
                try:
                    kind = kind_registry.get(communication.kind)
                    readable[communication.pk] = kind.can_prepare(caller, about_of(communication), communication)
                except ValidationError:
                    readable[communication.pk] = False
            if not readable[communication.pk]:
                continue
        out.append(message)
        if len(out) >= limit:
            break
    return out


# -- preparing -----------------------------------------------------------------


def _clean_cc(raw, i) -> list[str]:
    out = []
    for address in raw or []:
        address = str(address).strip()
        if not address:
            continue
        try:
            validate_email(address)
        except ValidationError as e:
            raise ValidationError({"recipients": f"recipient {i + 1}: cc {address!r} is not an email address"}) from e
        out.append(address)
    return out


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
                cc=_clean_cc(item.get("cc"), i),
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
    opaque_ref = ""
    if kind.about_model:
        about = kind.resolve(about_id)
        if about is None:
            raise NotFound(about_id)
    elif hasattr(kind, "resolve_about"):
        linked = kind.resolve_about(about_ref, caller)
        about, opaque_ref = linked.record, ("" if linked.record is not None else linked.ref)
    else:
        about = None
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
    elif opaque_ref:
        # The writer's own reference (a workflow run): kept as text, never looked up.
        communication.about_id = opaque_ref
    if not kind.can_prepare(caller, about, communication):
        raise PermissionDenied(f"You can't write {kind.label.lower()}s about this.")
    communication.save()
    if recipients is not None and not kind.recipients_editable:
        raise ValidationError(
            {"recipients": f"Who a {kind.label.lower()} goes to is set by labs; choose among them after drafting."}
        )
    _write_recipients(communication, _clean_recipients(recipients) if recipients is not None else spec.recipients)
    return communication


def _locked_draft(caller, pk):
    """The draft, locked for this transaction: a send and an edit never interleave."""
    communication, kind, about = load(caller, pk)
    communication = (
        Communication.objects.select_for_update(of=("self",)).select_related("owner_org").get(pk=communication.pk)
    )
    if not communication.is_draft:
        raise ValidationError(
            {"status": f"Only a draft can change; this one is {communication.get_status_display().lower()}."}
        )
    return communication, kind, about


@transaction.atomic
def update_draft(subject, pk, data: dict) -> Communication:
    """Change a draft: its title, subject, body, reply-to, recipients, which are included,
    or a recipient's address (`emails`: {recipient id: address})."""
    caller = caller_for(subject)
    communication, kind, about = _locked_draft(caller, pk)
    unknown = set(data) - {"title", "subject", "body", "reply_to", "recipients", "included", "emails"}
    if unknown:
        raise ValidationError({name: "cannot be changed" for name in sorted(unknown)})
    changed = []
    for name, limit in (("title", 200), ("subject", MAX_SUBJECT), ("body", MAX_BODY), ("reply_to", 320)):
        if name in data:
            value = str(data[name] or "")
            setattr(communication, name, value[:limit] if name == "body" else value[:limit].strip())
            changed.append(name)
    # Only what changed: never write back a status read before a send claimed it.
    communication.save(update_fields=[*changed, "updated_at"])
    if "recipients" in data:
        if not kind.recipients_editable:
            raise ValidationError(
                {"recipients": f"Who a {kind.label.lower()} goes to is set by labs; choose among them."}
            )
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
    if "emails" in data:
        if not kind.emails_editable:
            raise ValidationError(
                {"recipients": f"A {kind.label.lower()} goes to the addresses labs holds; they can't be changed here."}
            )
        for recipient in communication.recipients.all():
            address = (data["emails"] or {}).get(recipient.pk, (data["emails"] or {}).get(str(recipient.pk)))
            if address is None or address.strip() == recipient.email:
                continue
            address = address.strip()
            if address:
                try:
                    validate_email(address)
                except ValidationError as e:
                    raise ValidationError({"recipients": f"{address!r} is not an email address"}) from e
            recipient.email = address
            recipient.save(update_fields=["email"])
    return communication


@transaction.atomic
def discard(subject, pk) -> Communication:
    communication, kind, about = _locked_draft(caller_for(subject), pk)
    communication.status = "discarded"
    communication.save(update_fields=["status", "updated_at"])
    return communication


# -- previewing ----------------------------------------------------------------


def copy_for(communication, kind, about, recipient, *, caller, sending: bool):
    """(subject, body, filled) as this recipient reads it."""
    filled = kind.fill(communication, recipient, about, sending=sending, caller=caller)
    values = filled.values if sending else filled.recorded
    # Placeholders fill in after the template's own cap: a long title must not overflow the record.
    subject = render(recipient.subject or communication.subject, values).strip()[:MAX_SUBJECT]
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

    The draft row is locked for the rest of the request (labs runs each request
    in a transaction), so a concurrent send or edit waits and then finds it
    sent. Emails are queued only once that transaction commits; what sending
    changes besides the email (`kind.after_sent`) runs when delivery succeeds
    (`delivered`), never on a mere hand-off.
    """
    from connect_labs.utils.email import email_enabled, send_labs_email

    if request.headers.get("authorization"):
        raise PermissionDenied("Messages are sent by a person in labs, never by a token.")
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        raise PermissionDenied("Sign in to send.")
    caller = Caller(user=request.user, request=request)
    with transaction.atomic():
        load(caller, pk, for_send=True)  # NotFound unless they may send it
        communication, kind, about = _locked_draft(caller, pk)
        kind.before_send(communication, about, caller)
        errors = problems(communication, kind)
        if errors:
            raise ValidationError(errors)
        if not email_enabled():
            raise ValidationError({"__all__": "Outbound email is not set up on this labs, so nothing was sent."})
        communication.status = SENT
        communication.sent_by = request.user
        communication.sent_at = timezone.now()
        communication.save(update_fields=["status", "sent_by", "sent_at", "updated_at"])

        sent = []
        for i, recipient in enumerate(communication.recipients.filter(included=True).select_related("org")):
            subject_line, body, filled = copy_for(communication, kind, about, recipient, caller=caller, sending=True)
            record_subject, record_body, _ = copy_for(
                communication, kind, about, recipient, caller=caller, sending=False
            )
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
                delivery=QUEUED,
                effects=filled.effects,
                sent_by=request.user,
            )

            def queue(message=message, subject_line=subject_line, body=body, recipient=recipient, i=i):
                try:
                    send_labs_email(
                        subject_line,
                        body,
                        [recipient.email],
                        reply_to=[communication.reply_to],
                        cc=list(recipient.cc or []),
                        countdown=i * SEND_SPACING_SECONDS or None,
                        sent_message_id=message.pk,
                    )
                except Exception:  # noqa: BLE001 -- one failed hand-off must not stop the others
                    logger.exception("communications: could not queue message %s", message.pk)
                    SentMessage.objects.filter(pk=message.pk).update(delivery=NOT_SENT)

            transaction.on_commit(queue)
            sent.append(message)
    return {"communication": communication, "messages": sent, "queued": len(sent)}


def delivered(sent_message_id) -> None:
    """The mail service took this message: record it, then do what sending it changes.

    Called by the mail task's report (`tasks.record_delivery`), or by a person
    confirming a message whose outcome was unknown. The delivery is claimed
    first -- the move to delivered happens once -- so a report that arrives
    twice never applies the follow-up twice. A success outranks any earlier
    report (a task run twice may report failure first).
    """
    # One statement: delivered, and its follow-up pending, are recorded together --
    # a stop between them can never leave a sent message with no follow-up to retry.
    claimed = (
        SentMessage.objects.filter(pk=sent_message_id)
        .exclude(delivery=DELIVERED)
        .update(
            delivery=DELIVERED,
            followup=Case(
                When(communication__isnull=False, recipient__isnull=False, then=Value(FOLLOWUP_PENDING)),
                default=Value(FOLLOWUP_NONE),
            ),
        )
    )
    if claimed:
        run_followup(sent_message_id)


def run_followup(sent_message_id) -> bool:
    """Do what sending this message changes (`kind.after_sent`), as the person who sent it.

    Never resends the email. Safe to run again after a partial failure: the
    kinds' follow-ups set dates and record replies, which are idempotent. True
    when it is done; a failure is kept on the record (`followup_error`) for a
    person to retry ("Retry follow-up", or "Check delivery").
    """
    with transaction.atomic():
        message = (
            SentMessage.objects.select_for_update(of=("self",))
            .select_related("communication", "recipient", "sent_by")
            .filter(pk=sent_message_id)
            .first()
        )
        if message is None or message.followup not in (FOLLOWUP_PENDING, FOLLOWUP_FAILED):
            return message is not None and message.followup == FOLLOWUP_DONE
        communication, recipient = message.communication, message.recipient
        message.followup_attempts += 1
        message.followup_at = timezone.now()
        try:
            with transaction.atomic():
                kind = kind_registry.get(communication.kind)
                caller = Caller(user=message.sent_by)
                effects = kind.after_sent(communication, recipient, message, about_of(communication), caller) or {}
        except Exception as e:  # noqa: BLE001 -- the email went; say what did not follow it, and retry later
            logger.exception("communications: follow-up failed for message %s", message.pk)
            message.followup = FOLLOWUP_FAILED
            message.followup_error = str(e)[:1000]
            message.save(update_fields=["followup", "followup_attempts", "followup_at", "followup_error"])
            return False
        message.effects = {**(message.effects or {}), **effects}
        message.followup = FOLLOWUP_DONE
        message.followup_error = ""
        message.save(update_fields=["effects", "followup", "followup_attempts", "followup_at", "followup_error"])
        return True


def not_delivered(sent_message_id, *, failed: bool) -> None:
    """The mail service refused it for good (`failed`), or mail is off here.

    Only a message still waiting (or whose outcome was unknown) changes: a
    report that arrives after a success never turns "Sent" into "Failed".
    """
    SentMessage.objects.filter(pk=sent_message_id, delivery__in=(QUEUED, UNKNOWN)).update(
        delivery=FAILED if failed else NOT_SENT
    )


#: A message handed to the mail queue with no report back after this long was
#: lost on the way (a worker stopped mid-send): say so, never guess sent or failed.
UNKNOWN_AFTER = timedelta(hours=2)


def reconcile(messages, now=None) -> dict:
    """Settle what the mail path left open among `messages`, when a person asks ("Check delivery").

    Nothing runs this on a timer. It sends no mail.

    * A message still "Sending" long after it was handed over becomes "Outcome
      unknown": the page then asks a person to confirm it went (which runs its
      follow-up) -- labs never assumes delivered or failed.
    * A delivered message whose follow-up is pending (a report stopped half-way)
      or failed is retried now. Never by resending the email.
    """
    now = now or timezone.now()
    ids = [m.pk for m in messages]
    lost = SentMessage.objects.filter(pk__in=ids, delivery=QUEUED, sent_at__lt=now - UNKNOWN_AFTER).update(
        delivery=UNKNOWN
    )
    stuck = SentMessage.objects.filter(
        pk__in=ids, delivery=DELIVERED, followup__in=(FOLLOWUP_PENDING, FOLLOWUP_FAILED)
    ).values_list("pk", flat=True)
    retried = done = 0
    for pk in list(stuck):
        retried += 1
        done += int(run_followup(pk))
    return {"unknown": lost, "followups_retried": retried, "followups_done": done}


def check_delivery(request, pk) -> dict:
    """ "Check delivery" on one message: its emails, by someone who may send it."""
    caller = Caller(user=request.user, request=request)
    communication, _, _ = load(caller, pk, for_send=True)
    return reconcile(list(communication.sent_messages.all()))


def check_delivery_about(request, kind, about) -> dict:
    """ "Check delivery" on a record's messages page: what the person may read there, if they may write about it."""
    caller = Caller(user=request.user, request=request)
    if not kind.can_send(caller, about):
        raise NotFound(about.pk)
    return reconcile(sent_about(caller, about, limit=10_000))


# -- a person settling what labs could not ----------------------------------


def _message_for_sender(request, message_id):
    caller = Caller(user=request.user, request=request)
    message = SentMessage.objects.select_related("communication").filter(pk=message_id).first()
    if message is None or message.communication is None:
        raise NotFound(message_id)
    load(caller, message.communication.pk, for_send=True)  # NotFound unless they may send it
    return message


def confirm_delivered(request, message_id) -> None:
    """A person says a message whose outcome was unknown did arrive: record it and follow up."""
    message = _message_for_sender(request, message_id)
    if message.delivery != UNKNOWN:
        raise ValidationError({"__all__": "Only a message whose outcome is unknown can be confirmed."})
    delivered(message.pk)


def retry_followup(request, message_id) -> bool:
    """A person asks for a failed follow-up again (never the email)."""
    message = _message_for_sender(request, message_id)
    if message.delivery != DELIVERED or message.followup not in (FOLLOWUP_PENDING, FOLLOWUP_FAILED):
        raise ValidationError({"__all__": "Nothing to retry for this message."})
    return run_followup(message.pk)


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


def send_automatic(kind: str, *, about=None, to, subject: str, body: str, program_id=None, org=None) -> bool:
    """Send mail labs sends by itself (a receipt, a notice), recorded first.

    One email per address, each with its record, queued once the transaction
    commits and reported back by the mail task, so its row ends Sent, Failed or
    Not sent rather than waiting forever. True when mail is on and it was queued.
    """
    from connect_labs.utils.email import email_enabled, send_labs_email

    enabled = email_enabled()
    rows = record_automatic(
        kind, about=about, to=to, subject=subject, body=body, queued=enabled, program_id=program_id, org=org
    )
    if not enabled:
        return False

    def queue(row) -> bool:
        try:
            return send_labs_email(row.subject, row.body, [row.to_email], sent_message_id=row.pk) is not None
        except Exception:  # noqa: BLE001 -- recorded as not sent; the rest still go
            logger.exception("communications: could not queue notice %s", row.pk)
            SentMessage.objects.filter(pk=row.pk).update(delivery=NOT_SENT)
            return False

    if not transaction.get_connection().in_atomic_block:
        # Already committed (an award's own after-commit step): hand off now, and say how it went.
        return any([queue(row) for row in rows])
    for row in rows:
        transaction.on_commit(lambda row=row: queue(row))
    return bool(rows)
