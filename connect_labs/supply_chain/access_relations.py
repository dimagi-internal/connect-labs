"""Who a row is to an organisation: the relationship layer under Supply's access control.

Today a caller's standing is decided once per programme (`runner.standing`:
programme | operator | partner) and the only per-row scoping is two
hand-written queries (`update_links.service._org_contracts` and
`market.service._own_quotes`). This module is the generalisation: an
organisation is related to a row by the relationship fields the models already
carry, one organisation can hold several relations at once (running a
procurement, bidding on another, receiving goods on a third), and what it may do
with a row follows from the relations it holds *to that row*.

PHASE 1 (docs/superpowers/specs/2026-11-06-supply-relationship-acl.md) was SHADOW
MODE: `relations`, `capabilities` and `scope` exist, are tested and are observed on
reads (`access_shadow`). PHASE 2 enforces them on reads for a partner-standing
viewer, per programme, behind `acl_enforce_reads` (default off; see
`access_enforce`). This module itself still refuses nothing: it only says which
rows a viewer's relationships reach.

Two owner decisions (5 Nov) widen the derivation, and `extended` says whether a
caller wants them:

  * the receiving partner of an order (`Contract.partner_org`) is its RECEIVER,
    on the order and on the shipments, receipts, charges and invoices under it;
  * a supply point below one an organisation manages is that organisation's too
    (the nearest managing ancestor wins, `stock.services.issues.partner_by_point`),
    wherever a relation is "manages the point" (SupplyPoint, Movement, the
    delivery point of an order, the point of a receipt).

`extended=True` is the default. The update-link and marketplace paths pass
`extended=False` so they keep reaching exactly what they reached before.

Rules this module holds to:

  * Relations are DERIVED from existing fields only (see `MODELS`). No field is
    invented. If the data cannot say it, the relation does not exist.
  * Fail closed. A viewer whose organisations cannot be established holds no
    relation, and `scope` returns nothing for a partner-standing viewer.
  * The runner is everything. A runner holds every capability on every row in
    its programme, and a viewer with programme or operator standing gets the
    ordinary, unfiltered programme queryset -- exactly what they get today.
  * `relations(...)` and `scope(...)` are built from the SAME paths, so a row is
    in a viewer's scope if and only if the viewer holds a relation to it (tested).

Custodian is deliberately NOT modelled. A `Custodian` is a person or a role at a
supply point, with no organisation link of its own; the only organisation near it
is the point's `managed_by_org`, which is already `point_operator` on the
SupplyPoint. Deriving "custodian's organisation" from that would be inventing a
field, so a Custodian row stays programme-level until the data says whose it is.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from django.db.models import Q

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain import models
from connect_labs.supply_chain.models import scope_key

logger = logging.getLogger(__name__)


class Relation(str, enum.Enum):
    """How an organisation stands to one row. A single organisation may hold several, per row."""

    RUNNER = "runner"  # runs the row's programme (ProgrammeRunner)
    TENDER_OWNER = "tender_owner"  # publishes the tender (Tender.owner_org)
    INVITED_BIDDER = "invited_bidder"  # named on a restricted tender (Tender.invited_orgs)
    SUPPLIER = "supplier"  # is the supplier company (Supplier.org)
    BUYER = "buyer"  # buys the order (Contract.buyer_org)
    RECEIVER = "receiver"  # runs the store the order is delivered to (SupplyPoint.managed_by_org)
    APPROVER = "approver"  # asked to agree an award (AwardApproval.approver_org)
    POINT_OPERATOR = "point_operator"  # runs the supply point (SupplyPoint.managed_by_org)
    PAYEE = "payee"  # is owed a charge (Charge.payee_org)
    OWED_TO = "owed_to"  # is owed an answer or a promise (Commitment.owed_to_org)


class Capability(str, enum.Enum):
    READ = "read"
    RECORD_OWN = "record_own"  # record its own side: a delivery, a receipt, a bid
    ANSWER_APPROVAL = "answer_approval"  # answer an approval asked of it


ALL_CAPABILITIES = frozenset(Capability)

# What each relation allows on the rows it is held to. Descriptive in phase 1:
# nothing enforces it yet (phase 3 turns RECORD_OWN / ANSWER_APPROVAL into
# per-object write checks). The runner row is not "everything listed" but
# everything -- see `capabilities`.
_CAPABILITIES: dict[Relation, frozenset[Capability]] = {
    Relation.RUNNER: ALL_CAPABILITIES,
    Relation.TENDER_OWNER: frozenset({Capability.READ}),
    Relation.INVITED_BIDDER: frozenset({Capability.READ, Capability.RECORD_OWN}),
    Relation.SUPPLIER: frozenset({Capability.READ, Capability.RECORD_OWN}),
    Relation.BUYER: frozenset({Capability.READ, Capability.RECORD_OWN}),
    Relation.RECEIVER: frozenset({Capability.READ, Capability.RECORD_OWN}),
    Relation.APPROVER: frozenset({Capability.READ, Capability.ANSWER_APPROVAL}),
    Relation.POINT_OPERATOR: frozenset({Capability.READ, Capability.RECORD_OWN}),
    Relation.PAYEE: frozenset({Capability.READ}),
    Relation.OWED_TO: frozenset({Capability.READ}),
}


def capabilities(held: Iterable[Relation]) -> frozenset[Capability]:
    """What a set of relations to one row allows. The runner implies everything."""
    held = set(held)
    if Relation.RUNNER in held:
        return ALL_CAPABILITIES
    out: set[Capability] = set()
    for relation in held:
        out |= _CAPABILITIES[relation]
    return frozenset(out)


# ---- what each model's relations are made of -------------------------------

# `rows_for(program_id=ANY_PROGRAMME)`: no programme filter (the marketplace is not programme-scoped).
ANY_PROGRAMME = object()


def _plain_program(path: str) -> Callable[[object], dict]:
    return lambda program_id: {path: int(program_id)}


def _supplier_program(program_id) -> dict:
    return {"scope_key": scope_key(program_id=int(program_id))}


@dataclass(frozen=True)
class ModelSpec:
    """Where a model's programme is, and which lookup paths make an organisation related to a row."""

    program_path: str
    program_filter: Callable[[object], dict]
    relations: dict[Relation, tuple[str, ...]] = field(default_factory=dict)
    # Paths to `Contract.partner_org` that ALSO make an organisation the RECEIVER (owner decision 2).
    # Left out when a caller asks for `extended=False`, so the update-link path is unchanged.
    order_partner: tuple[str, ...] = ()


def _spec(program_path, relations, program_filter=None, order_partner=()) -> ModelSpec:
    return ModelSpec(program_path, program_filter or _plain_program(program_path), relations, tuple(order_partner))


R = Relation
MODELS: dict[type, ModelSpec] = {
    models.Tender: _spec(
        "program_id",
        {R.TENDER_OWNER: ("owner_org",), R.INVITED_BIDDER: ("invited_orgs",)},
    ),
    models.Supplier: _spec("scope_key", {R.SUPPLIER: ("org",)}, _supplier_program),
    models.Quote: _spec(
        "tender__program_id",
        {R.SUPPLIER: ("supplier__org",), R.TENDER_OWNER: ("tender__owner_org",)},
    ),
    models.Award: _spec(
        "tender__program_id",
        {
            R.SUPPLIER: ("supplier__org",),
            R.TENDER_OWNER: ("tender__owner_org",),
            R.APPROVER: ("approvals__approver_org",),
        },
    ),
    models.AwardApproval: _spec(
        "award__tender__program_id",
        {R.APPROVER: ("approver_org",), R.TENDER_OWNER: ("award__tender__owner_org",)},
    ),
    models.Contract: _spec(
        "program_id",
        {
            R.SUPPLIER: ("supplier__org",),
            R.BUYER: ("buyer_org",),
            R.RECEIVER: ("delivery_supply_point__managed_by_org",),
            R.TENDER_OWNER: ("tender__owner_org",),
        },
        order_partner=("partner_org",),
    ),
    models.Shipment: _spec(
        "contract__program_id",
        {
            R.SUPPLIER: ("contract__supplier__org",),
            R.BUYER: ("contract__buyer_org",),
            R.RECEIVER: ("contract__delivery_supply_point__managed_by_org",),
            R.TENDER_OWNER: ("contract__tender__owner_org",),
        },
        order_partner=("contract__partner_org",),
    ),
    # A charge is a cost: the payee, the buyer and the receiver of the goods it landed see it; the supplier does not.
    models.Charge: _spec(
        "shipment__contract__program_id",
        {
            R.PAYEE: ("payee_org",),
            R.BUYER: ("shipment__contract__buyer_org",),
            R.RECEIVER: ("shipment__contract__delivery_supply_point__managed_by_org",),
        },
        order_partner=("shipment__contract__partner_org",),
    ),
    models.Receipt: _spec(
        "supply_point__program_id",
        {
            R.RECEIVER: ("supply_point__managed_by_org",),
            R.SUPPLIER: ("contract__supplier__org",),
            R.BUYER: ("contract__buyer_org",),
        },
        order_partner=("contract__partner_org",),
    ),
    models.Invoice: _spec(
        "contract__program_id",
        {
            R.SUPPLIER: ("contract__supplier__org",),
            R.BUYER: ("contract__buyer_org",),
            R.RECEIVER: ("contract__delivery_supply_point__managed_by_org",),
        },
        order_partner=("contract__partner_org",),
    ),
    # Who a supplier approached is the tender owner's business and that supplier's own; competing bidders never see it.
    models.Outreach: _spec(
        "tender__program_id",
        {R.SUPPLIER: ("supplier__org",), R.TENDER_OWNER: ("tender__owner_org",)},
    ),
    models.Commitment: _spec("program_id", {R.OWED_TO: ("owed_to_org",)}),
    models.SupplyPoint: _spec("program_id", {R.POINT_OPERATOR: ("managed_by_org",)}),
    models.Movement: _spec(
        "program_id",
        {R.POINT_OPERATOR: ("from_supply_point__managed_by_org", "to_supply_point__managed_by_org")},
    ),
}
del R


class NotModelled(KeyError):
    """The model has no relation spec yet (so phase 1 says nothing about it)."""


def spec_for(model) -> ModelSpec:
    try:
        return MODELS[model]
    except KeyError:
        raise NotModelled(model.__name__) from None


def modelled(model) -> bool:
    return model in MODELS


def _crosses_many(model, path: str) -> bool:
    """Whether a lookup path walks a reverse or many-to-many relation (so a join can repeat a row)."""
    current = model
    for part in path.split("__"):
        try:
            step = current._meta.get_field(part)
        except Exception:  # noqa: BLE001 -- an attname such as `x_id`; plain column, not a join
            return False
        if step.many_to_many or step.one_to_many:
            return True
        if not step.is_relation:
            return False
        current = step.related_model
    return False


def _in(path: str, org_ids) -> Q:
    return Q(**{f"{path}__in": org_ids})


def _is_point_org_path(path: str) -> bool:
    return path == "managed_by_org" or path.endswith("__managed_by_org")


def owned_point_ids(program_id, org_ids) -> set[int]:
    """Supply points in the programme that `org_ids` run, directly or by sitting below a point they run.

    The nearest managing ancestor wins (`partner_by_point`): a field worker under a partner's store is
    that partner's, unless some other organisation manages the worker's own point.
    """
    from connect_labs.supply_chain.stock.services.issues import partner_by_point

    ids = {int(getattr(i, "pk", i)) for i in (org_ids or ()) if i is not None}
    if not ids or program_id in (None, "") or program_id is ANY_PROGRAMME:
        return set()
    return {pk for pk, owner in partner_by_point(int(program_id)).items() if owner in ids}


def _paths(spec: ModelSpec, relation: Relation, extended: bool) -> tuple[str, ...]:
    paths = spec.relations.get(relation, ())
    if extended and relation is Relation.RECEIVER and spec.order_partner:
        paths = paths + spec.order_partner
    return paths


def _path_condition(path: str, org_ids, point_ids) -> Q:
    condition = _in(path, org_ids)
    if point_ids and _is_point_org_path(path):
        prefix = path.rpartition("__")[0]
        condition |= Q(**{f"{prefix}__in": point_ids}) if prefix else Q(pk__in=point_ids)
    return condition


def _condition(spec: ModelSpec, wanted, org_ids, program_id, extended: bool) -> tuple[Q, list[str]]:
    """The one place a set of relations becomes a filter: `rows_for` and `relations` both read it."""
    paths = [path for relation in wanted for path in _paths(spec, relation, extended)]
    points = owned_point_ids(program_id, org_ids) if extended and any(map(_is_point_org_path, paths)) else set()
    condition = Q()
    for path in paths:
        condition |= _path_condition(path, org_ids, points)
    return condition, paths


def rows_for(
    model,
    org_ids,
    *,
    program_id=ANY_PROGRAMME,
    relations: Iterable[Relation] | None = None,
    extended: bool = True,
):
    """Rows of `model` that `org_ids` hold any of `relations` to (default: every one), optionally in one programme.

    `org_ids` may be ids, instances or a queryset of LabsOrg: anything `__in` takes.
    The runner relation is not a path -- it is programme-wide -- so it is not
    here; see `scope_for_orgs`. A row reached through a reverse or many-to-many
    path is selected by `pk__in`, so it is never listed twice.

    `extended` (default True) includes the receiving partner of an order and the
    supply points below one an organisation manages (needs `program_id`). Callers
    that must keep their pre-phase-2 reach pass False: the update links.
    """
    spec = spec_for(model)
    wanted = list(spec.relations) if relations is None else [r for r in relations if r in spec.relations]
    queryset = model.objects.all()
    if program_id is not ANY_PROGRAMME:
        queryset = queryset.filter(**spec.program_filter(program_id))
    condition, paths = _condition(spec, wanted, org_ids, program_id, extended)
    if not paths:
        return queryset.none()
    if any(_crosses_many(model, path) for path in paths):
        return queryset.filter(pk__in=model.objects.filter(condition).values("pk"))
    return queryset.filter(condition)


def programme_rows(model, program_id):
    """Every row of `model` in a programme: the unfiltered queryset today's programme and operator viewers get."""
    spec = spec_for(model)
    return model.objects.filter(**spec.program_filter(program_id))


# ---- relations of one row --------------------------------------------------


def _program_of(obj) -> int | None:
    spec = spec_for(type(obj))
    value = type(obj).objects.filter(pk=obj.pk).values_list(spec.program_path, flat=True).first()
    if value is None:
        return None
    if isinstance(value, str):  # Supplier.scope_key == "prog:<id>"
        _, _, tail = value.partition(":")
        value = tail
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def runner_org_id(program_id) -> int | None:
    from connect_labs.supply_chain.runner.models import ProgrammeRunner

    if program_id in (None, ""):
        return None
    try:
        pid = int(program_id)
    except (TypeError, ValueError):
        return None
    return ProgrammeRunner.objects.filter(program_id=pid).values_list("org_id", flat=True).first()


def relations(viewer_org_ids, obj) -> set[Relation]:
    """Every relation `viewer_org_ids` hold to `obj`, derived from the row's own fields. Empty if unknown.

    One cheap existence query per relation the model defines, plus one for the
    runner. A model with no spec yields nothing: phase 1 does not guess.
    """
    ids = {int(i) for i in (viewer_org_ids or ()) if i is not None}
    if not ids or obj is None or getattr(obj, "pk", None) is None or type(obj) not in MODELS:
        return set()
    model = type(obj)
    spec = MODELS[model]
    held: set[Relation] = set()
    program_id = _program_of(obj)
    runner = runner_org_id(program_id)
    if runner is not None and runner in ids:
        held.add(Relation.RUNNER)
    for relation in spec.relations:
        condition, paths = _condition(spec, [relation], ids, program_id, True)
        if paths and model.objects.filter(pk=obj.pk).filter(condition).exists():
            held.add(relation)
    return held


# ---- the viewer ------------------------------------------------------------


def viewer_org_ids(access) -> frozenset[int]:
    """The LabsOrg ids the caller is, for the purpose of relations. Empty if they cannot be established.

    Built from the same sources the rest of Supply already trusts, reimplementing none of them:
    the caller's Connect organisations (`identity`), a Dimagi address (Dimagi's own row),
    a membership granted FOR an organisation (`runner.is_member`'s second source), and -- for a
    caller with programme or operator standing -- the organisation that runs the programme,
    because that is who they act for. Cached on the access object (one request / tool call).
    """
    cached = getattr(access, "_acl_viewer_orgs", None)
    if cached is not None:
        return cached
    try:
        found = _viewer_org_ids(access)
    except Exception:  # noqa: BLE001 -- unknowable identity grants nothing
        logger.info("supply acl: could not establish the viewer's organisations", exc_info=True)
        found = frozenset()
    try:
        access._acl_viewer_orgs = found
    except AttributeError:
        pass
    return found


def _viewer_org_ids(access) -> frozenset[int]:
    from django.db.models import F

    from connect_labs.marketplace.models import OrgMembership
    from connect_labs.supply_chain import identity, runner
    from connect_labs.utils.dimagi_user import is_dimagi_user

    ids: set[int] = set()
    user = runner._user_of(access)

    held = runner.standing(access)
    if held.is_programme and held.runner is not None:
        ids.add(held.runner.pk)

    if user is not None and getattr(user, "pk", None):
        if is_dimagi_user(user):
            ids |= set(LabsOrg.objects.filter(slug=runner.DIMAGI_ORG_SLUG).values_list("pk", flat=True))
        ids |= set(
            OrgMembership.objects.filter(user=user, granted_for_org_id=F("org_id")).values_list("org_id", flat=True)
        )

    organizations = identity._caller_organizations(access)
    if organizations:
        pairs = [(identity._as_org_id(entry.get("id")), entry.get("slug")) for entry in organizations]
        pairs = [(i, s) for i, s in pairs if i is not None or s]
        connect_ids = [i for i, _ in pairs if i is not None]
        candidates = LabsOrg.objects.filter(
            Q(connect_organization_id__in=connect_ids) | Q(connect_organization_id=None)
        )
        for org in candidates:
            if any(org.matches(organization_id=i, slug=s) for i, s in pairs):
                ids.add(org.pk)
    return frozenset(ids)


# ---- a viewer's scope ------------------------------------------------------


def scope_for_orgs(org_ids, program_id, model, *, is_programme: bool = False, extended: bool = True):
    """The rows of `model` in `program_id` that `org_ids` may see.

    `is_programme` (programme or operator standing, or a system caller) returns
    the unfiltered programme queryset -- today's behaviour, unchanged. Anyone else
    gets the rows they hold a relation to; the runner organisation itself holds
    every row; no programme or no organisations means nothing.
    """
    if program_id in (None, ""):
        return model.objects.none()
    spec_for(model)
    if is_programme:
        return programme_rows(model, program_id)
    ids = {int(i) for i in (org_ids or ()) if i is not None}
    if not ids:
        return model.objects.none()
    runner = runner_org_id(program_id)
    if runner is not None and runner in ids:
        return programme_rows(model, program_id)
    return rows_for(model, ids, program_id=program_id, extended=extended)


def scope(access, model):
    """The rows of `model` this caller's relationships give them, in `access`'s programme.

    A caller with programme or operator standing (or the system caller) gets the same
    unfiltered programme queryset they get today. Fails closed for everyone else.
    """
    from connect_labs.supply_chain import runner

    program_id = getattr(access, "program_id", None)
    try:
        is_programme = runner.is_system(access) or runner.standing(access).is_programme
    except Exception:  # noqa: BLE001 -- standing that cannot be established is a partner's
        is_programme = False
    return scope_for_orgs(viewer_org_ids(access), program_id, model, is_programme=is_programme)
