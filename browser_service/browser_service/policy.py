"""Decides which URLs the browser may request. This is the service's SSRF defence."""

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

Resolver = Callable[[str], list[str]]
UrlPolicy = Callable[[str], str | None]


def resolve_host(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def public_web_only(url: str, resolver: Resolver = resolve_host) -> str | None:
    """Return why the URL is refused, or None if it's allowed.

    Every address a host resolves to must be public. A DNS answer that changes between this check
    and the request can still get through, which is why the service also runs with no access to
    internal services or secrets.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return f"scheme '{parts.scheme}' isn't allowed"
    if parts.username or parts.password:
        return "URLs with credentials aren't allowed"
    host = parts.hostname
    if not host:
        return "the URL has no host"
    try:
        addresses = resolver(host)
    except OSError:
        return f"couldn't resolve '{host}'"
    if not addresses:
        return f"couldn't resolve '{host}'"
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        mapped = getattr(ip, "ipv4_mapped", None)
        if not (mapped or ip).is_global:
            return f"'{host}' resolves to a non-public address"
    return None
