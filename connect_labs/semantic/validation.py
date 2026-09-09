"""Prove a registry is safe to save BEFORE it is saved.

A registry held on disk is reviewed, tested and deployed before it can affect
anything. A registry held in the database is not: it is edited live, by a person
or an agent, and the next dashboard load compiles whatever is there. That is the
whole point -- iterating an indicator should not cost a merge and a deploy -- but
it moves the only remaining safety net onto the write path.

So this is the gate. ``validate_registry`` does what the test suite does to the
shipped registry: resolves every reference, parses every SQL fragment against the
expression grammar, and then COMPILES the whole thing at every declared scope
with the deployment facts attached. Compiling at every scope is not belt-and-
braces -- the suppression gate referenced ``props.llo`` bare, which is valid SQL
at the llo scope and invalid at every other one, so a registry that compiled
"fine" still returned a raw Postgres 400 from three of the five scopes the
dashboard offers.

What it deliberately does NOT do is execute. Execution needs a warm visit cache
and a live database, which a save should not depend on; the parity suite covers
execution against Postgres.
"""

from __future__ import annotations

from typing import Any

from connect_labs.semantic.compiler import SCOPES, RegistryError, compile_indicator_sql, validate

# A registry is compiled against Layer 1's output columns, and Layer 1 is
# generated from the pipeline. At save time there is no pipeline in hand, so the
# fragment below stands in for one: it is never executed, only parsed and
# type-checked by the compiler's own reference resolution.
_PLACEHOLDER_VISIT_SQL = "SELECT * FROM labs_visit_rows"


class RegistryInvalid(Exception):
    """A registry was rejected. ``errors`` carries every problem, not just the first."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_registry(
    props_doc: dict[str, Any],
    registry: dict[str, Any],
    deployment: dict[str, Any] | None = None,
) -> list[str]:
    """Return every reason this registry cannot be saved. Empty list means good.

    Returns rather than raises so a caller can show a person all of it at once.
    """
    errors: list[str] = []

    if not isinstance(props_doc, dict) or not props_doc.get("properties"):
        errors.append("properties: document is empty or has no `properties` list")
    if not isinstance(registry, dict) or not registry.get("measures"):
        errors.append("indicators: document is empty or has no `measures` list")
    if errors:
        return errors  # nothing below can run without both documents

    deployment = deployment or {}
    raw_map = deployment.get("llo_map") or {}
    try:
        llo_map = {int(k): v for k, v in raw_map.items()} or None
    except (TypeError, ValueError):
        errors.append("deployment: every llo_map key must be an opportunity id (an integer)")
        llo_map = None
    settings = deployment.get("settings") or None

    # 1. References and the expression grammar.
    try:
        errors.extend(validate(props_doc, registry, llo_map=llo_map))
    except RegistryError as exc:
        errors.append(str(exc))
    except Exception as exc:  # a malformed document should not 500 the save
        errors.append(f"{type(exc).__name__}: {exc}")

    if errors:
        return errors

    # 2. It has to COMPILE at every scope, with the gates attached. See the module
    #    docstring: scope-dependent SQL is exactly what slipped through before.
    for scope in SCOPES:
        try:
            compile_indicator_sql(
                props_doc,
                registry,
                _PLACEHOLDER_VISIT_SQL,
                scope=scope,
                as_of="CURRENT_DATE",
                llo_map=llo_map,
                settings=settings,
            )
        except Exception as exc:
            errors.append(f"scope {scope!r} does not compile: {type(exc).__name__}: {exc}")

    # 3. Coherence of the facts the GATES read. These moved out of Python
    #    (`gates.IND_INPUTS` / `APP_ASKS`, and a `_C16_MIN_COVERAGE` literal) into
    #    registry data so they can be edited without a deploy — which means their
    #    correctness has to be checked HERE, against the registry actually being
    #    saved, rather than by a unit test over the on-disk copy. A test would pin
    #    values that are meant to change, and would read the seed file while a
    #    workflow bound to a record computed from something else.
    errors.extend(_gate_coherence(registry, deployment))

    return errors


def _gate_coherence(registry: dict[str, Any], deployment: dict[str, Any]) -> list[str]:
    """Reasons the availability/credibility facts would misgrade a real number."""
    errors: list[str] = []

    for m in registry.get("measures") or []:
        meta = m.get("meta") or {}
        ind = meta.get("indicator")
        if not ind:
            continue

        # A thin-coverage floor is a fraction OF something. A floor with nothing to
        # divide by silently footnotes nothing, and a denominator with no floor is
        # dead weight that reads as if a rule were active.
        floor, base = meta.get("min_input_coverage"), meta.get("coverage_denominator")
        if floor and not base:
            errors.append(f"{ind}: min_input_coverage is set but coverage_denominator names no measure")
        if base and not floor:
            errors.append(f"{ind}: coverage_denominator is set but there is no min_input_coverage to apply")
        if floor is not None:
            try:
                if not 0 < float(floor) <= 1:
                    errors.append(f"{ind}: min_input_coverage must be a fraction in (0, 1], got {floor!r}")
            except (TypeError, ValueError):
                errors.append(f"{ind}: min_input_coverage must be a number, got {floor!r}")
        if base and not any((x.get("name") == base) for x in (registry.get("measures") or [])):
            errors.append(f"{ind}: coverage_denominator {base!r} is not a measure in this registry")

        inputs = meta.get("inputs")
        if inputs is not None and not isinstance(inputs, list):
            errors.append(f"{ind}: inputs must be a list of derived-property names, got {type(inputs).__name__}")

    # Credibility is read two opposite ways on purpose — an allow-list for
    # mortality ("is this LLO listed true") and a deny-list for completion ("is it
    # NOT false"). They agree ONLY when every LLO is listed explicitly. An LLO
    # omitted from the completion table reads credible to the gate and suppressed to
    # the compiler, which is the exact shape of bug a shared table exists to prevent.
    settings = deployment.get("settings") or {}
    every_llo = sorted({str(v) for v in (deployment.get("llo_map") or {}).values()})
    for setting, table in settings.items():
        if not every_llo or not table:
            continue
        missing = [llo for llo in every_llo if llo not in table]
        if missing:
            errors.append(f"deployment.settings.{setting}: states no verdict for {missing} — a gate would guess")

    # An availability map that has been flattened to all-true gates nothing while
    # looking configured, so every indicator reads as available everywhere. That is
    # the failure mode of regenerating the map, and it is silent.
    app_asks = deployment.get("app_asks") or {}
    if app_asks and not any(v is False for fields in app_asks.values() for v in fields.values()):
        errors.append(
            "deployment.app_asks: every opportunity asks every field, so the availability gate "
            "can never fire. If that is genuinely true, omit app_asks rather than declaring it."
        )

    return errors


def assert_registry_valid(
    props_doc: dict[str, Any],
    registry: dict[str, Any],
    deployment: dict[str, Any] | None = None,
) -> None:
    """``validate_registry``, raising ``RegistryInvalid`` when anything is wrong."""
    errors = validate_registry(props_doc, registry, deployment)
    if errors:
        raise RegistryInvalid(errors)
