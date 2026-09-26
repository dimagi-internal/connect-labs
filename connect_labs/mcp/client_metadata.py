"""Fetching a client's metadata document (CIMD) and its JWKS, without SSRF.

The canopy client is identified by a URL (its Client ID Metadata Document), and
that document names the URL of its signing keys. Labs fetches both, from the
server, which makes them a server-side request to an address somebody else
wrote down. So every fetch here:

* is ``https`` only, on the default port, with no credentials in the URL;
* resolves the host ITSELF and refuses if any address it resolves to is private,
  loopback, link-local (the cloud metadata endpoint lives there), multicast,
  reserved or unspecified — and then connects to the address it vetted, not to
  a second lookup, so DNS cannot answer differently between the check and the
  connection (rebinding). TLS is still verified against the hostname;
* follows no redirects, times out quickly, and reads at most 64 KiB;
* is cached, for at most an hour (the contract's ceiling), so a rotated key is
  picked up within that window and a slow canopy does not slow every grant.

A refused or failed fetch raises ``MetadataError``; the grant then fails closed
with ``invalid_client``.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
from urllib.parse import urlsplit

import httpcore
from django.core.cache import cache

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 5
MAX_DOCUMENT_BYTES = 64 * 1024
CACHE_SECONDS = 3600
#: After a key lookup misses, the JWKS may be re-fetched early (a rotation), but
#: not more often than this — or a stream of bad ``kid`` values would turn into a
#: stream of requests at canopy.
REFETCH_FLOOR_SECONDS = 300


class MetadataError(Exception):
    pass


def _address_allowed(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def vet_url(url: str) -> tuple[str, int]:
    """The host and port of ``url`` if it is a URL labs may fetch, else ``MetadataError``."""
    if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() for c in url):
        raise MetadataError("not a URL")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise MetadataError("not a URL") from exc
    if parts.scheme != "https":
        raise MetadataError("only https URLs are fetched")
    if not parts.hostname or parts.username or parts.password:
        raise MetadataError("the URL must name a host and carry no credentials")
    if port not in (None, 443):
        raise MetadataError("only the default https port is fetched")
    try:
        # An IP literal is judged directly; a name is judged at connect time.
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        pass
    else:
        if not _address_allowed(parts.hostname):
            raise MetadataError("that address is not public")
    return parts.hostname, 443


class _VettedBackend(httpcore.SyncBackend):
    """Resolve, vet every address, and connect to a vetted one — never a fresh lookup."""

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise httpcore.ConnectError(f"could not resolve {host}") from exc
        addresses = [info[4][0] for info in infos]
        if not addresses:
            raise httpcore.ConnectError(f"{host} resolved to nothing")
        # ALL of them, not any: a name that resolves to one public and one
        # private address is exactly the shape a rebinding attack takes.
        if not all(_address_allowed(address) for address in addresses):
            raise httpcore.ConnectError(f"{host} resolves to an address that is not public")
        return super().connect_tcp(
            addresses[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )


def _fetch(url: str) -> dict:
    vet_url(url)
    timeouts = {name: FETCH_TIMEOUT_SECONDS for name in ("connect", "read", "write", "pool")}
    try:
        with httpcore.ConnectionPool(network_backend=_VettedBackend(), retries=0) as pool:
            with pool.stream(
                "GET", url, headers=[(b"Accept", b"application/json")], extensions={"timeout": timeouts}
            ) as response:
                if response.status != 200:
                    raise MetadataError(f"{url} answered {response.status}")
                body = b""
                for chunk in response.iter_stream():
                    body += chunk
                    if len(body) > MAX_DOCUMENT_BYTES:
                        raise MetadataError(f"{url} is larger than {MAX_DOCUMENT_BYTES} bytes")
    except httpcore.TimeoutException as exc:
        raise MetadataError(f"{url} timed out") from exc
    except httpcore.NetworkError as exc:
        raise MetadataError(f"{url} could not be fetched: {exc}") from exc
    except httpcore.ProtocolError as exc:
        raise MetadataError(f"{url} answered with something that is not HTTP") from exc
    try:
        document = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MetadataError(f"{url} is not JSON") from exc
    if not isinstance(document, dict):
        raise MetadataError(f"{url} is not a JSON object")
    return document


def fetch_json(url: str, *, fresh: bool = False) -> dict:
    """``url``'s JSON document, from the cache when it is there (and ``fresh`` is not asked for)."""
    key = f"mcp-client-metadata:{url}"
    if not fresh:
        cached = cache.get(key)
        if isinstance(cached, dict):
            return cached
    document = _fetch(url)
    cache.set(key, document, CACHE_SECONDS)
    return document


def refetch_allowed(url: str) -> bool:
    """Whether an early re-fetch of ``url`` is allowed now (at most one per floor window)."""
    return bool(cache.add(f"mcp-client-metadata-refetch:{url}", 1, REFETCH_FLOOR_SECONDS))


def client_signing_keys(client_id: str) -> list[dict]:
    """The canopy client's metadata, checked, and the keys it publishes.

    Returns the JWKS ``keys`` list. Raises ``MetadataError`` when the document
    does not describe this client as the contract requires.
    """
    return _keys(client_id, fresh=False)


def client_signing_keys_refreshed(client_id: str) -> list[dict] | None:
    """The keys re-fetched past the cache, or None when a re-fetch is not allowed yet."""
    if not refetch_allowed(client_id):
        return None
    return _keys(client_id, fresh=True)


def _keys(client_id: str, *, fresh: bool) -> list[dict]:
    document = fetch_json(client_id, fresh=fresh)
    if document.get("client_id") != client_id:
        raise MetadataError("the client metadata document names a different client_id")
    if document.get("token_endpoint_auth_method") != "private_key_jwt":
        raise MetadataError("the client does not authenticate with private_key_jwt")
    jwks_uri = document.get("jwks_uri")
    if not isinstance(jwks_uri, str):
        raise MetadataError("the client metadata document names no jwks_uri")
    jwks = fetch_json(jwks_uri, fresh=fresh)
    keys = jwks.get("keys")
    if not isinstance(keys, list) or not all(isinstance(k, dict) for k in keys):
        raise MetadataError("the client's JWKS has no keys")
    return keys
