"""SSRF guvenlik denetiminin (app.url_safety) birim testleri.

Bu testler gercek DNS/ag erisimi yapmaz: `resolve_host_ips`,
`socket.getaddrinfo`'yu sarmaladigi icin gerekli senaryolarda
`monkeypatch` ile sahtelenir (ozellikle DNS rebinding senaryosu icin).
"""

import socket

import pytest

from app import url_safety


# --- Sema / sozdizimi reddi -------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.com/dosya",
        "",
        "   ",
        "not-a-url",
    ],
)
def test_rejects_disallowed_schemes(url):
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.validate_url_syntax(url)


def test_rejects_credentials_in_url():
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.validate_url_syntax("http://user:pass@example.com/")


def test_rejects_missing_hostname():
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.validate_url_syntax("http:///path-only")


def test_rejects_known_metadata_hostname():
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.validate_url_syntax("http://metadata.google.internal/computeMetadata/v1/")


def test_accepts_well_formed_public_https_url():
    scheme, hostname = url_safety.validate_url_syntax("https://example.com/anasayfa")
    assert scheme == "https"
    assert hostname == "example.com"


# --- IP blok listesi ---------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.0.0.53",
        "::1",
        "10.0.0.1",
        "172.16.5.4",
        "192.168.1.1",
        "169.254.169.254",  # AWS/GCP/Azure metadata
        "0.0.0.0",
        "224.0.0.1",  # multicast
        "fe80::1",  # ipv6 link-local
        "fc00::1",  # ipv6 unique local
        "::ffff:127.0.0.1",  # ipv4-mapped ipv6 loopback
    ],
)
def test_blocks_private_loopback_link_local_and_metadata_ips(ip):
    assert url_safety.is_blocked_ip(ip) is True


@pytest.mark.parametrize("ip", ["93.184.216.34", "1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"])
def test_allows_public_ips(ip):
    assert url_safety.is_blocked_ip(ip) is False


def test_unparsable_ip_is_treated_as_blocked():
    assert url_safety.is_blocked_ip("not-an-ip") is True


# --- DNS cozumleme + tam dogrulama -------------------------------------------


def test_resolve_host_ips_wraps_dns_failure(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise socket.gaierror("bilinmeyen host")

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _raise)
    with pytest.raises(url_safety.UnsafeUrlError):
        url_safety.resolve_host_ips("does-not-exist.invalid")


def test_validate_public_url_allows_public_resolution(monkeypatch):
    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    validated = url_safety.validate_public_url("https://example.com/")
    assert validated.hostname == "example.com"
    assert validated.resolved_ips == ("93.184.216.34",)


def test_validate_public_url_rejects_private_ip():
    monkeypatch_target = "127.0.0.1"

    def _fake_resolve(hostname):
        return (monkeypatch_target,)

    import unittest.mock as mock

    with mock.patch.object(url_safety, "resolve_host_ips", side_effect=_fake_resolve):
        with pytest.raises(url_safety.UnsafeUrlError):
            url_safety.validate_public_url("http://internal.example.com/")


def test_validate_public_url_rejects_when_any_resolved_ip_is_private():
    """Bir hostname birden fazla IP'ye cozumlenirse (ör. round-robin) ve
    bunlardan biri bile ozel/engelli ise, tum URL reddedilir (saldirganin
    'gorunumdeki' genel IP'yi kontrole gosterip gercekte ozel IP'ye
    yonlendirme ihtimaline karsi)."""

    import unittest.mock as mock

    with mock.patch.object(url_safety, "resolve_host_ips", return_value=("93.184.216.34", "10.0.0.5")):
        with pytest.raises(url_safety.UnsafeUrlError):
            url_safety.validate_public_url("http://mixed.example.com/")


def test_dns_rebinding_scenario_is_pinned_to_single_resolution(monkeypatch):
    """DNS rebinding tehdidi: bir saldirgan, dogrulama anindan hemen sonra
    ayni hostname icin farkli (ozel) bir IP dondurmeye baslayabilir. Bu test,
    `validate_public_url`'nin hostname'i yalnizca **bir kez** cozumledigini
    ve bu anlik goruntuyu (`resolved_ips`) cagirana dondurdugunu dogrular;
    cagiran taraf (bkz. analyzer/app/browser.py) gercek baglantiyi bu ayni
    IP'ye sabitlemelidir - boylece dogrulamadan SONRA yapilan ikinci bir
    DNS sorgusu (rebinding penceresi) hic devreye girmez.
    """

    call_count = {"n": 0}
    responses = [("93.184.216.34",), ("10.0.0.5",)]  # ilk cagri genel, ikincisi ozel (rebinding)

    def _fake_resolve(hostname):
        result = responses[call_count["n"]]
        call_count["n"] += 1
        return result

    monkeypatch.setattr(url_safety, "resolve_host_ips", _fake_resolve)

    validated = url_safety.validate_public_url("https://rebinding.example.com/")

    assert call_count["n"] == 1  # yalnizca bir kez cozumlendi
    assert validated.resolved_ips == ("93.184.216.34",)  # ilk (genel) sonuc sabitlendi
