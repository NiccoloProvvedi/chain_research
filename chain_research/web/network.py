"""Blocking of private networks: the computer itself, the home network and internal services are unreachable.

Used by the browser (on every request, with `context.route`) and by the sources' HTTP client
(`web/sources/http.py`, which also checks the address at connection time).
"""
import functools
import ipaddress
import socket
from urllib.parse import urlsplit

LOCAL_NAMES = (".localhost", ".local", ".internal", ".lan", ".home")


def forbidden_ip(ip: str) -> bool:
    """True for private, loopback, link-local, reserved, multicast or unspecified addresses."""
    address = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped  # ::ffff:127.0.0.1 is the computer itself
    # is_global also excludes 100.64.0.0/10 (CGNAT, Tailscale), which is_private lets through.
    return (not address.is_global or address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast or address.is_unspecified)


def local_name(host: str) -> bool:
    """True for names that point to the computer or the home network, without resolving them."""
    host = (host or "").strip("[]").lower().rstrip(".")
    return not host or host == "localhost" or host.endswith(LOCAL_NAMES)


@functools.lru_cache(maxsize=512)
def forbidden_host(host: str) -> bool:
    """True for the computer itself and the home network (localhost, 192.168..., 10..., .local names, Docker services...)."""
    host = (host or "").strip("[]").lower()
    if local_name(host):
        return True
    try:
        return forbidden_ip(host)
    except ValueError:
        pass
    try:
        addresses = {i[4][0] for i in socket.getaddrinfo(host, None)}
    except OSError:
        return False  # nonexistent name: the request will fail on its own
    return any(forbidden_ip(ip) for ip in addresses)


def safe_address(url: str) -> bool:
    """Only http/https to the public Internet."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and not forbidden_host(parts.hostname)
