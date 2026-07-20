"""SSRF'e karsi URL guvenlik denetimi.

Bu modul, analiz edilecek URL'nin herkese acik (public) bir hedefe isaret
ettigini dogrular ve asagidakileri reddeder:

- http/https disindaki semalar (file, data, javascript, ftp, vb.)
- URL icinde kimlik bilgisi (userinfo, ör. `http://user:pass@host`)
- localhost, loopback (127.0.0.0/8, ::1), private (RFC1918/RFC4193),
  link-local (169.254.0.0/16 - bulut metadata IP'leri dahil, fe80::/10),
  reserved, multicast, unspecified (0.0.0.0) adresler
- Bilinen bulut metadata hostname'leri (ör. metadata.google.internal)

Not (DNS rebinding): `validate_public_url`, hostname'i **bir kez** cozumler
ve dogrulanan IP kumesini `ValidatedUrl.resolved_ips` icinde dondurur.
Cagiran taraf (bkz. `analyzer/app/browser.py`), gercek baglantiyi bu ayni
IP'ye **sabitleyerek** (host resolver pinning) kurmalidir; dogrulama ile
baglanti arasinda hostname'in tekrar cozumlenmesi (TOCTOU) DNS rebinding'e
acik kapi birakir - bkz. docs/security.md "SSRF tehdit modeli".

`backend/app/services/url_safety.py` ile ayni mantigi tasir (ayri
container/dagitim birimi oldugu icin kod paylasimi yerine kasitli olarak
kopyalanmistir); biri degisirse digeri de guncellenmelidir.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")

# Acikca isim vererek belgelemek icin ayrica listelenen bulut metadata
# hostname'leri (IP tabanli metadata adresleri zaten link-local/private
# araligina girdigi icin `is_blocked_ip` tarafindan da yakalanir).
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
    """Semayi, kimlik bilgisini ve hostname varligini dogrular (DNS cozumleme yapmaz).

    Basarili oldugunda (scheme, hostname) dondurur.
    """

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
        # Ayristirilamayan bir adres guvenli varsayilamaz.
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
    """Tam dogrulama: sema + kimlik bilgisi + DNS cozumleme + tum IP'lerin herkese acik olmasi.

    Cozumlenen IP'lerden **herhangi biri** engellenmisse tum URL reddedilir
    (bir saldirganin hem genel hem ozel bir IP donduren bir DNS kaydiyla
    kontrolu atlatmaya calismasina karsi).
    """

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
