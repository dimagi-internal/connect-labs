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

    # -- the record it is about ----------------------------------------------

    def resolve(self, about_id):
        """The record by id, or None."""
        if not self.about_model:
            return None
        from django.apps import apps

        model = apps.get_model(self.about_model)
        return model.objects.filter(pk=about_id).first()

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


class GeneralKind(Kind):
    key = "general"
    label = "Message"
    description = (
        "A message an organisation writes to people, about any labs record (a workflow run, a programme...) "
        "or nothing. Its members prepare and send it. Name the organisation it is sent for."
    )

    def resolve_about(self, ref: str):
        """A record from "app_label.model:id"; None for an empty ref."""
        if not ref:
            return None
        from django.apps import apps

        try:
            model_label, pk = ref.rsplit(":", 1)
            model = apps.get_model(model_label)
        except (ValueError, LookupError) as e:
            raise ValidationError({"about": f"{ref!r} names no labs record; write it as app_label.model:id"}) from e
        record = model.objects.filter(pk=pk).first()
        if record is None:
            raise ValidationError({"about": f"no {model_label} with id {pk}"})
        return record

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
        return DraftSpec(
            title=params.get("title") or (f"Message about {about}" if about is not None else "Message"),
            owner_org=org,
            program_id=params.get("program_id"),
        )


register(GeneralKind())
