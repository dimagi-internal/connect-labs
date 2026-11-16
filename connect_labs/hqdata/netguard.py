"""Where labs may connect to on a customer's say-so.

A customer types a host. Left unchecked, that is a way to make labs open a connection from inside
its own network: to its database, to the cloud metadata service, to anything on a private address.
So the host is resolved HERE, every address it resolves to must be acceptable, and the connection is
then made to the address that was checked (``ConnectionSpec.hostaddr``), never to the name again, so
the name cannot be re-pointed in between. The TLS certificate is still checked against the name.

Never allowed, whatever a customer says: loopback, link-local (which includes 169.254.169.254, the
metadata service), multicast, unspecified, reserved, and labs' own database host.

Allowed only where staff have ticked ``allow_private_network`` on the connection: addresses that are
private rather than public (RFC 1918, unique-local IPv6, carrier-grade NAT). A customer reachable over
a tunnel or a peered network genuinely lives there; nobody else should.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable

from django.conf import settings

HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)

# Metadata services of the clouds labs might run in, beyond the 169.254.0.0/16 link-local range.
# Plus the IPv6 forms that carry an IPv4 address inside them (NAT64, 6to4): they could name 127.0.0.1
# or 169.254.169.254 in a way the checks below do not see.
_ALWAYS_REFUSED = [
    ipaddress.ip_network("fd00:ec2::/32"),
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("2002::/16"),
]


class HostRefused(ValueError):
    """The host is not one labs will connect to. The message is for the customer."""


def _classify(ip: ipaddress._BaseAddress, *, allow_private: bool) -> str | None:
    """Why this address is refused, or None if it is acceptable."""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:  # ::ffff:127.0.0.1 is 127.0.0.1
        ip = mapped
    if ip.is_loopback:
        return "a loopback address"
    if ip.is_link_local or any(ip in net for net in _ALWAYS_REFUSED):
        return "a link-local address (this includes cloud metadata services)"
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return "not a routable address"
    if ip.is_global:
        return None
    if allow_private:
        return None
    return "a private-network address"


def _own_database_hosts() -> set[str]:
    hosts = set()
    for conf in getattr(settings, "DATABASES", {}).values():
        host = (conf.get("HOST") or "").strip().lower()
        if host:
            hosts.add(host)
    return hosts


def check_host(
    host: str,
    *,
    allow_private: bool = False,
    resolver: Callable = socket.getaddrinfo,
) -> str:
    """Resolve ``host`` and return the address to connect to. Raises HostRefused.

    Every address the name resolves to must pass, not just the first: a name that answers with one
    public and one internal address is how a rebinding attack gets its second try.
    """
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        raise HostRefused("Enter the database's host name.")
    if host in _own_database_hosts():
        raise HostRefused("That is the labs database itself.")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
        if not HOSTNAME_RE.match(host):
            raise HostRefused("That is not a valid host name.")

    if literal is not None:
        addresses = [literal]
    else:
        try:
            found = resolver(host, None, type=socket.SOCK_STREAM)
        except OSError:
            raise HostRefused("That host name does not resolve.")
        addresses = []
        for entry in found:
            try:
                addresses.append(ipaddress.ip_address(entry[4][0]))
            except ValueError:
                continue
        if not addresses:
            raise HostRefused("That host name does not resolve.")

    for ip in addresses:
        why = _classify(ip, allow_private=allow_private)
        if why:
            raise HostRefused(f"{host} resolves to {why}, which labs will not connect to.")
    # Prefer IPv4: it is what a customer's firewall rule is most likely to name.
    ordered = sorted(addresses, key=lambda a: a.version)
    return str(ordered[0])


def check_port(port) -> int:
    try:
        number = int(port)
    except (TypeError, ValueError):
        raise HostRefused("The port must be a number.")
    if not 1 <= number <= 65535:
        raise HostRefused("The port must be between 1 and 65535.")
    return number
