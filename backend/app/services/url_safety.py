"""SSRF'e karsi URL guvenlik denetimi (backend tarafi, hizli on kontrol).

Bu modul, `analyzer/app/url_safety.py` ile **ayni mantigi** tasir - kasitli
olarak kopyalanmistir, cunku backend ve analyzer ayri container/dagitim
birimleridir (paylasilan bir Python paketi yerine iki kucuk, birbirinden
bagimsiz kopya tercih edilmistir). Biri degisirse digeri de guncellenmelidir.

Backend bu dogrulamayi **is olusturulurken** (kullanici istegi ananinda)
hizli, kullanici dostu bir 400 hatasi dondurmek icin kullanir; ancak
otoriter/son dogrulama, gercek navigasyonu yapan analyzer servisinde
gerceklesir (bkz. docs/security.md "SSRF tehdit modeli") - DNS, backend'in
bu on kontrolu yaptigi an ile analyzer'in gercekten baglandigi an arasinda
degisebilir (rebinding); bu yuzden backend'in kontrolu bir savunma
katmanidir, tek basina yeterli degildir.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")

METADATA_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
    }
)


class UnsafeUrlError(ValueError):
    """URL, SSRF onleme politikasi geregi reddedildi."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ValidatedUrl:
    url: str
    scheme: str
    hostname: str
    port: int | None
    resolved_ips: tuple[str, ...]


def validate_url_syntax(url: str) -> tuple[str, str]:
    """Semayi, kimlik bilgisini ve hostname varligini dogrular (DNS cozumleme yapmaz)."""

    if not isinstance(url, str) or not url.strip():
        raise UnsafeUrlError("URL bos olamaz")

    candidate = url.strip()
    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"Yalnizca http/https semalarina izin verilir (alinan: {scheme or 'yok'})")

    if parts.username or parts.password:
        raise UnsafeUrlError("URL icinde kimlik bilgisi (userinfo) barindiramaz")

    try:
        hostname = parts.hostname
    except ValueError as exc:
        raise UnsafeUrlError("URL hostname'i ayristirilamadi") from exc

    if not hostname:
        raise UnsafeUrlError("URL bir hostname icermelidir")

    hostname = hostname.lower()
    if hostname in METADATA_HOSTNAMES:
        raise UnsafeUrlError("Bulut metadata endpoint'lerine erisim engellenir")

    return scheme, hostname


def is_blocked_ip(ip_str: str) -> bool:
    """Private/loopback/link-local/reserved/multicast/unspecified IP'leri (v4 ve v6) engeller."""

    try:
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(ip_str)
    except ValueError:
        return True

    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            ip = mapped

    blocked = (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )
    if isinstance(ip, ipaddress.IPv6Address):
        blocked = blocked or ip.is_site_local

    return blocked


def resolve_host_ips(hostname: str) -> tuple[str, ...]:
    """Hostname'i coz ve ele gecen tum benzersiz IP'leri dondurur."""

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Hostname cozumlenemedi: {hostname}") from exc

    ips = tuple(sorted({info[4][0] for info in infos}))
    if not ips:
        raise UnsafeUrlError(f"Hostname icin IP adresi bulunamadi: {hostname}")
    return ips


def validate_public_url(url: str) -> ValidatedUrl:
    """Tam dogrulama: sema + kimlik bilgisi + DNS cozumleme + tum IP'lerin herkese acik olmasi."""

    scheme, hostname = validate_url_syntax(url)
    ips = resolve_host_ips(hostname)
    for ip in ips:
        if is_blocked_ip(ip):
            raise UnsafeUrlError(
                f"Hostname '{hostname}' engellenmis bir IP'ye cozumleniyor ({ip}); "
                "ozel/loopback/link-local/metadata aglara erisim reddedilir"
            )

    parts = urlsplit(url.strip())
    return ValidatedUrl(url=url.strip(), scheme=scheme, hostname=hostname, port=parts.port, resolved_ips=ips)


__all__ = [
    "ALLOWED_SCHEMES",
    "METADATA_HOSTNAMES",
    "UnsafeUrlError",
    "ValidatedUrl",
    "validate_url_syntax",
    "is_blocked_ip",
    "resolve_host_ips",
    "validate_public_url",
]
