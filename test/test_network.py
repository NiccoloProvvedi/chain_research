"""Forbidden networks: address table (no network)."""
import pytest

from chain_research.web.network import forbidden_ip


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "0.0.0.0", "224.0.0.1",
    "100.64.0.1", "100.100.100.100", "100.127.255.254",  # CGNAT and Tailscale
    "192.0.0.1", "198.18.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:100.64.0.1"])
def test_forbidden_addresses(ip: str) -> None:
    assert forbidden_ip(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "100.63.255.255", "100.128.0.1", "2606:4700:4700::1111"])
def test_public_addresses(ip: str) -> None:
    assert not forbidden_ip(ip)
