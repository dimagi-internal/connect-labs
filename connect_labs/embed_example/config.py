"""The example system's own settings, read in one place.

* ``EMBED_EXAMPLE_PRIVATE_KEY`` -- its Ed25519 private key (PEM), a secret. Unset: the
  example says it is not configured and does nothing else.
* ``EMBED_EXAMPLE_CLIENT_ID`` -- the client id labs registered it under.
* ``EMBED_EXAMPLE_LABS_URL`` -- the labs it embeds (default ``LABS_PUBLIC_URL``).
* ``EMBED_EXAMPLE_BASE_URL`` -- where the example itself is served, so where its JWKS
  lives and which origin frames labs (default: the labs URL). Point this at a second
  hostname to make the example genuinely cross-origin; nothing else changes.
* ``EMBED_EXAMPLE_RESOURCES`` -- what it shows: ``[{"kind": "workflow", "id": 123,
  "opportunity_id": 10001, "label": "..."}]``. Generated data only (labs enforces it).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from connect_labs.embed_example.embedder import Embedder


@dataclass(frozen=True)
class Resource:
    kind: str
    id: str
    label: str
    entry: dict

    @property
    def key(self) -> str:
        return f"{self.kind}/{self.id}"


def labs_url() -> str:
    return (getattr(settings, "EMBED_EXAMPLE_LABS_URL", "") or getattr(settings, "LABS_PUBLIC_URL", "")).rstrip("/")


def base_url() -> str:
    return (getattr(settings, "EMBED_EXAMPLE_BASE_URL", "") or labs_url()).rstrip("/")


def client_id() -> str:
    return getattr(settings, "EMBED_EXAMPLE_CLIENT_ID", "") or "labs-example"


def private_key_pem() -> str:
    # Environment variables often carry a PEM's newlines as "\n".
    return (getattr(settings, "EMBED_EXAMPLE_PRIVATE_KEY", "") or "").replace("\\n", "\n").strip()


def configured() -> bool:
    return bool(private_key_pem() and labs_url())


def embedder() -> Embedder:
    return Embedder(client_id=client_id(), private_key_pem=private_key_pem(), labs_url=labs_url())


def resources() -> list[Resource]:
    out = []
    for entry in getattr(settings, "EMBED_EXAMPLE_RESOURCES", None) or []:
        if isinstance(entry, dict) and entry.get("kind") and entry.get("id") is not None:
            label = entry.get("label") or f"{entry['kind'].title()} {entry['id']}"
            out.append(Resource(kind=str(entry["kind"]), id=str(entry["id"]), label=label, entry=entry))
    return out


def resource(key: str | None) -> Resource | None:
    found = resources()
    if not key:
        return found[0] if found else None
    return next((r for r in found if r.key == key), None)
