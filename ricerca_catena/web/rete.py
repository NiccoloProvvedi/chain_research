"""Blocco delle reti private: il computer stesso, la rete di casa e i servizi interni non sono raggiungibili.

Usato dal browser (a ogni richiesta, con `context.route`) e dal client HTTP delle fonti
(`web/fonti/http.py`, che controlla anche l'indirizzo al momento della connessione).
"""
import functools
import ipaddress
import socket
from urllib.parse import urlsplit

NOMI_LOCALI = (".localhost", ".local", ".internal", ".lan", ".home")


def ip_vietato(ip: str) -> bool:
    """Vero per indirizzi privati, loopback, link-local, riservati, multicast o non specificati."""
    indirizzo = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(indirizzo, ipaddress.IPv6Address) and indirizzo.ipv4_mapped:
        indirizzo = indirizzo.ipv4_mapped  # ::ffff:127.0.0.1 è il computer stesso
    return (indirizzo.is_private or indirizzo.is_loopback or indirizzo.is_link_local or indirizzo.is_reserved
            or indirizzo.is_multicast or indirizzo.is_unspecified)


def nome_locale(host: str) -> bool:
    """Vero per i nomi che indicano il computer o la rete di casa, senza risolverli."""
    host = (host or "").strip("[]").lower().rstrip(".")
    return not host or host == "localhost" or host.endswith(NOMI_LOCALI)


@functools.lru_cache(maxsize=512)
def host_vietato(host: str) -> bool:
    """Vero per il computer stesso e la rete di casa (localhost, 192.168..., 10..., nomi .local, servizi Docker...)."""
    host = (host or "").strip("[]").lower()
    if nome_locale(host):
        return True
    try:
        return ip_vietato(host)
    except ValueError:
        pass
    try:
        indirizzi = {i[4][0] for i in socket.getaddrinfo(host, None)}
    except OSError:
        return False  # nome inesistente: la richiesta fallirà da sola
    return any(ip_vietato(ip) for ip in indirizzi)


def indirizzo_sicuro(url: str) -> bool:
    """Solo http/https verso Internet pubblico."""
    try:
        parti = urlsplit(url)
    except ValueError:
        return False
    return parti.scheme in ("http", "https") and bool(parti.hostname) and not host_vietato(parti.hostname)
