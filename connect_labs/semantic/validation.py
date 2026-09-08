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
