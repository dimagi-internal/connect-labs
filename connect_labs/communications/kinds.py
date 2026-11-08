"""The kinds of message labs writes, one per feature that writes them.

A kind says, for the records it writes about:

* who may prepare a draft and who may send it (`can_prepare`, `can_send`);
* what a new draft says and who it goes to (`new_draft`);
* what its placeholders fill in with for each person (`fill`) -- including
  anything sending must create, such as an invitation link, made only when it
  is really sent and kept out of the stored copy;
* what sending changes besides the email (`after_sent`): an outreach marked
  sent, a commitment's reply recorded.

A feature registers its kinds at start-up (`CommunicationsConfig.ready`).
`general` is labs' own: a message an organisation writes, about anything or
nothing -- what a workflow uses to keep its correspondence with a run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied, ValidationError

_REGISTRY: dict[str, Kind] = {}


@dataclass
class RecipientSpec:
    email: str = ""
    name: str = ""
    org: object = None
    cc: list = field(default_factory=list)
    subject: str = ""
    body: str = ""
    label: str = ""
    ref: dict = field(default_factory=dict)
    included: bool = True


@dataclass
class DraftSpec:
    title: str
    subject: str = ""
    body: str = ""
    recipients: list = field(default_factory=list)
    context: dict = field(default_factory=dict)
    owner_org: object = None
    program_id: int | None = None


@dataclass
class Filled:
    """One person's values: what they read, what labs keeps, and what sending made."""

    values: dict
    recorded: dict = field(default_factory=dict)
    effects: dict = field(default_factory=dict)


class Kind:
    key = ""
    label = ""
    #: "app_label.model" of what it is about, or "" for anything.
    about_model = ""
    #: Placeholders a writer may use, filled in per person.
    placeholders: tuple = ("first_name", "name", "org_name")
    description = ""
    #: Whether a draft's recipient list may be replaced, and an address changed.
    #: A kind whose recipients carry something sending acts on (an invitation
    #: to an organisation, an outreach row) fixes who they are: a writer only
    #: chooses among them.
    recipients_editable = True
    emails_editable = True

    # -- the record it is about ----------------------------------------------

    def resolve(self, about_id):
        """The record by id, or None."""
        if not self.about_model:
            return None
        from django.apps import apps

        model = apps.get_model(self.about_model)
        try:
            return model.objects.filter(pk=int(about_id)).first()
        except (TypeError, ValueError):
            return None

    def about_label(self, about) -> str:
        return str(about) if about is not None else ""

    def about_url(self, about) -> str:
        return ""

    # -- who ----------------------------------------------------------------

    def can_prepare(self, caller, about, communication=None) -> bool:
        return self.can_send(caller, about, communication)

    def can_send(self, caller, about, communication=None) -> bool:
        raise NotImplementedError

    # -- what ---------------------------------------------------------------

    def new_draft(self, caller, about, params: dict) -> DraftSpec:
        raise NotImplementedError

    def fill(self, communication, recipient, about, *, sending: bool, caller) -> Filled:
        org_name = recipient.org.name if recipient.org_id else ""
        values = {
            "first_name": recipient.first_name or (f"{org_name} team" if org_name else "colleague"),
            "name": recipient.name or org_name,
            "org_name": org_name,
        }
        return Filled(values=values, recorded=dict(values))

    def before_send(self, communication, about, caller) -> None:
        """Refuse (ValidationError) when the subject no longer allows it."""

    def after_sent(self, communication, recipient, sent, about, caller) -> dict:
        """What sending to this recipient changes besides the email; returns effects to record."""
        return {}


def register(kind: Kind) -> Kind:
    if kind.key in _REGISTRY:
        raise ValueError(f"communication kind {kind.key!r} registered twice")
    _REGISTRY[kind.key] = kind
    return kind


def get(key: str) -> Kind:
    try:
        return _REGISTRY[key]
    except KeyError as e:
        raise ValidationError({"kind": f"no kind of message called {key!r}; one of {sorted(_REGISTRY)}"}) from e


def all_kinds() -> list[Kind]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def render(template: str, values: dict) -> str:
    """The template with each `{name}` it knows filled in; other braces are left as written."""
    text = template or ""
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


# -- labs' own: a message an organisation writes -----------------------------


def _may_read_call(caller, record) -> bool:
    from connect_labs.solicitations import access

    return access.can_read(caller, record) or access.can_read_round_responses(caller, record)


def _may_read_tender(caller, record) -> bool:
    """The programme, or a supplier invited to the tender (it reads its own invitation)."""
    from connect_labs.supply_chain.communication_kinds import _is_programme
    from connect_labs.supply_chain.models import Outreach

    if _is_programme(caller, record.program_id):
        return True
    if record.status != "open":
        # A supplier sees a tender only while it takes quotes (the marketplace hides the rest).
        return False
    invited = Outreach.objects.filter(tender=record).select_related("supplier__org")
    return any(o.supplier.org_id and caller.is_member(o.supplier.org) for o in invited)


#: Records a general message may be linked to, each with who may name it. Any
#: other reference ("workflow.run:812", a programme) is kept as the writer's own
#: text and never looked up, so naming one reveals nothing about it.
LINKABLE = {
    "solicitations.solicitation": _may_read_call,
    "supply_chain.tender": _may_read_tender,
    "labs.labsorg": lambda caller, record: True,  # the organisation directory is public
}


#: What `about_id` holds: a reference longer than this is refused, never cut short
#: (two cut the same way would share each other's correspondence).
MAX_REF = 64


@dataclass
class About:
    record: object = None
    ref: str = ""


class GeneralKind(Kind):
    key = "general"
    label = "Message"
    description = (
        "A message an organisation writes to people, about a labs record or nothing. Name the organisation it "
        "is sent for (`params.owner_org`); its members prepare and send it. `about_ref` links it: "
        '"solicitations.solicitation:<id>", "supply_chain.tender:<id>" or "labs.labsorg:<id>" link that '
        'record (you must be able to read it); anything else, e.g. "workflow.run:<id>", is kept as written.'
    )

    def resolve_about(self, ref: str, caller) -> About:
        """The record a reference names, if it is a linkable one the caller may read; else the text itself."""
        ref = (ref or "").strip()
        if not ref:
            return About()
        if len(ref) > MAX_REF:
            raise ValidationError({"about": f"a reference is at most {MAX_REF} characters"})
        model_label, _, pk = ref.rpartition(":")
        model_label = model_label.lower()
        check = LINKABLE.get(model_label)
        if check is None:
            return About(ref=ref)
        from django.apps import apps

        model = apps.get_model(model_label)
        try:
            record = model.objects.filter(pk=int(pk)).first()
        except (TypeError, ValueError):
            record = None
        # One answer for a record that is missing and one the caller may not read.
        if record is None or not check(caller, record):
            raise ValidationError({"about": f"{ref!r} is not a record you can write about."})
        return About(record=record, ref=ref)

    def can_send(self, caller, about, communication=None) -> bool:
        org = communication.owner_org if communication is not None and communication.owner_org_id else None
        if org is None:
            return False
        return caller.is_member(org) or caller.is_operator

    def new_draft(self, caller, about, params: dict) -> DraftSpec:
        from connect_labs.solicitations.access import resolve_org

        org = resolve_org(params.get("owner_org"))
        if org is None:
            raise ValidationError({"owner_org": "Name the organisation this message is sent for."})
        if not (caller.is_member(org) or caller.is_operator):
            raise PermissionDenied(f"You can write only for an organisation you belong to, and not for {org.name}.")
        program_id = params.get("program_id")
        if program_id not in (None, ""):
            try:
                program_id = int(program_id)
            except (TypeError, ValueError) as e:
                raise ValidationError({"program_id": "must be a whole number"}) from e
        else:
            program_id = None
        return DraftSpec(
            title=params.get("title") or (f"Message about {about}" if about is not None else "Message"),
            owner_org=org,
            program_id=program_id,
        )


register(GeneralKind())
