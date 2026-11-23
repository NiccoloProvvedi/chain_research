"""Blocco delle reti private: il computer stesso, la rete di casa e i servizi interni non sono raggiungibili.

Usato dal browser (a ogni richiesta, con `context.route`) e dal client HTTP delle fonti
(`web/fonti/http.py`, che controlla anche l'indirizzo al momento della connessione).
"""
import functools
import ipaddress
import socket
from urllib.parse import urlsplit

LOCAL_NAMES = (".localhost", ".local", ".internal", ".lan", ".home")


def forbidden_ip(ip: str) -> bool:
    """Vero per indirizzi privati, loopback, link-local, riservati, multicast o non specificati."""
    address = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped  # ::ffff:127.0.0.1 è il computer stesso
    # is_global esclude anche 100.64.0.0/10 (CGNAT, Tailscale), che is_private lascia passare.
    return (not address.is_global or address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast or address.is_unspecified)


def local_name(host: str) -> bool:
    """Vero per i nomi che indicano il computer o la rete di casa, senza risolverli."""
    host = (host or "").strip("[]").lower().rstrip(".")
    return not host or host == "localhost" or host.endswith(LOCAL_NAMES)


@functools.lru_cache(maxsize=512)
def forbidden_host(host: str) -> bool:
    """Vero per il computer stesso e la rete di casa (localhost, 192.168..., 10..., nomi .local, servizi Docker...)."""
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
        return False  # nome inesistente: la richiesta fallirà da sola
    return any(forbidden_ip(ip) for ip in addresses)


def safe_address(url: str) -> bool:
    """Solo http/https verso Internet pubblico."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and not forbidden_host(parts.hostname)
