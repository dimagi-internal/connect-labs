"""Turn an on-disk registry into the payload for a database-backed one.

The on-disk registries are the seed, not the rival. `registry_payload("kmc")`
reads the three YAML documents and returns exactly the dict a
``semantic_registry`` record holds, so moving KMC from files to a record is a
copy rather than a re-typing -- and the copy is validated on the way in, which
means a record can never be seeded from a registry that would not have been
accepted had someone written it by hand.

Keeping the files also keeps the fallback honest: a workflow that binds nothing
still compiles the shipped registry, so nothing had to be migrated for this to
land.
"""

from __future__ import annotations

from typing import Any

from connect_labs.semantic.runtime import load_deployment_facts, load_registry
from connect_labs.semantic.validation import assert_registry_valid

REGISTRY_ROOT_NAME = "kmc"


def registry_payload(name: str = REGISTRY_ROOT_NAME) -> dict[str, Any]:
    """The `data` for a semantic_registry record, seeded from the on-disk registry."""
    props, inds = load_registry(name)
    # EVERY deployment fact, not just the compiler's two. This read `load_deployment`,
    # which returns the back-compat (llo_map, settings) pair -- so when `app_asks` and
    # `asks_as` moved out of `semantic/gates.py` into the registry, the seeder
    # silently dropped them and every record was born without the facts its
    # availability gates need. A gate with no facts fails OPEN, so "not in this app"
    # could never fire and the on-disk registry looked identical to the record from
    # the outside. Measured on registry 5500: 0 app_asks opportunities against 22 on
    # disk.
    facts = load_deployment_facts(name)

    # JSON object keys are always strings, so an int-keyed llo_map does not survive
    # a round trip through the record. `normalise_deployment_facts` coerces them back
    # on read; writing them as strings here makes the stored shape honest about what
    # JSON can hold rather than pretending the ints came back.
    deployment = {
        "llo_map": {str(k): v for k, v in facts["llo_map"].items()},
        "settings": facts["settings"],
        "app_asks": facts["app_asks"],
        "asks_as": facts["asks_as"],
    }

    assert_registry_valid(props, inds, deployment)

    return {
        "name": f"{name.upper()} indicators (seeded from disk)",
        "description": (
            f"Seeded from connect_labs/semantic/registry/{name}. Edit this record to "
            f"change indicators without a deploy; the on-disk copy remains the fallback "
            f"for any workflow that binds no registry."
        ),
        "properties": props,
        "indicators": inds,
        "deployment": deployment,
    }
