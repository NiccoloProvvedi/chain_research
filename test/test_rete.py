"""Reti vietate: tabella degli indirizzi (senza rete)."""
import pytest

from ricerca_catena.web.rete import ip_vietato


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "0.0.0.0", "224.0.0.1",
    "100.64.0.1", "100.100.100.100", "100.127.255.254",  # CGNAT e Tailscale
    "192.0.0.1", "198.18.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:100.64.0.1"])
def test_indirizzi_vietati(ip: str) -> None:
    assert ip_vietato(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "100.63.255.255", "100.128.0.1", "2606:4700:4700::1111"])
def test_indirizzi_pubblici(ip: str) -> None:
    assert not ip_vietato(ip)
