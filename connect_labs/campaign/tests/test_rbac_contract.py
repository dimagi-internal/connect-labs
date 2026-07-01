"""Contract test: the client-side `perms.js` matrix must agree with `rbac.py`.

`rbac.py` is the real gate; `perms.js` is a second hand-maintained copy used only
for show/hide. They are in different role vocabularies, bridged at runtime by
`app.jsx`'s ROLE_DISPLAY map. Nothing stops the two from silently drifting — and
they already have (`perms.js` omitted the `training` module). This test parses
`perms.js` and locks `client_can(role, module, verb)` to
`rbac.can(server_role, module, verb)` for every combination, so any future drift
fails CI instead of shipping a wrong UI.

If `connect_labs/campaign/` migrates out, this test travels with it; the only
coupling is the relative path to the static `perms.js`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from connect_labs.campaign.services import rbac

PERMS_JS = Path(__file__).resolve().parents[2] / "static" / "campaign" / "perms.js"

# client role-id (perms.js) -> server role-id (rbac.py)
ROLE_ALIAS = {
    "admin": "campaign_admin",
    "payment": "payment_admin",
    "compliance": "compliance_admin",
    "operations": "operations_manager",
    "reporting": "reporting_user",
}

_VERBS_JSON = json.dumps(rbac.VERBS)


def _extract_block(src: str, opener: str) -> str:
    """Return the brace/bracket-balanced literal that follows `opener` in `src`."""
    start = src.index(opener) + len(opener)
    open_ch = src[start - 1]
    close_ch = {"{": "}", "[": "]"}[open_ch]
    depth, i = 1, start
    while depth:
        if src[i] == open_ch:
            depth += 1
        elif src[i] == close_ch:
            depth -= 1
        i += 1
    return open_ch + src[start:i]


def _js_literal_to_py(literal: str) -> object:
    """Convert a (simple) JS object/array literal to a Python value via JSON."""
    text = re.sub(r"\bFULL\b", _VERBS_JSON, literal)  # expand the FULL constant
    text = re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', text)  # quote bareword keys
    text = text.replace("'", '"')  # single -> double quotes
    text = re.sub(r",(\s*[}\]])", r"\1", text)  # drop trailing commas
    return json.loads(text)


def _parse_perms_js():
    src = PERMS_JS.read_text()
    matrix = _js_literal_to_py(_extract_block(src, "const MATRIX = {"))
    connections_roles = _js_literal_to_py(_extract_block(src, "const CONNECTIONS_ROLES = ["))
    m = re.search(r"const TRAINING_ROLES\s*=\s*(\[[^\]]*\])", src)
    training_roles = _js_literal_to_py(m.group(1)) if m else None
    return matrix, set(connections_roles), (set(training_roles) if training_roles is not None else None)


MATRIX, CONNECTIONS_ROLES, TRAINING_ROLES = _parse_perms_js()


def _client_can(client_role: str, module: str, verb: str) -> bool:
    """Mirror perms.js `can()` semantics exactly."""
    if module == "connections":
        return client_role in CONNECTIONS_ROLES
    if module == "training":
        return client_role in TRAINING_ROLES if TRAINING_ROLES is not None else False
    return verb in MATRIX.get(client_role, {}).get(module, [])


pytestmark = pytest.mark.contract


def test_perms_js_role_set_matches_server():
    assert set(ROLE_ALIAS.keys()) == set(MATRIX.keys())
    assert set(ROLE_ALIAS.values()) == set(rbac.ROLES)


@pytest.mark.parametrize("client_role,server_role", sorted(ROLE_ALIAS.items()))
def test_client_matrix_agrees_with_server(client_role, server_role):
    mismatches = []
    for module in rbac.MODULES:
        for verb in rbac.VERBS:
            client = _client_can(client_role, module, verb)
            server = rbac.can(server_role, module, verb)
            if client != server:
                mismatches.append(f"{module}:{verb} client={client} server={server}")
    assert not mismatches, f"perms.js drifted from rbac.py for {server_role}: " + "; ".join(mismatches)
