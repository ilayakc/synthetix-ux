"""Chip yukleme (top-up) paketleri.

Ayni surumlenme deseni: paketler tek bir surumlu yapida tutulur. Bu
paketler PDF'deki 1.500-3.000 Chip degerlerini KULLANMAZ - kasitli olarak
farkli, kucuk miktarlardir. `POST /api/billing/topup-requests` yalnizca bu
paketlerden birini kabul eder; frontend kendi miktarini icat edemez.

Onemli: bu modul gercek bir odeme saglayicisiyla ENTEGRE DEGILDIR. Bir
talep olusturmak Chip bakiyesini ARTIRMAZ (bkz. app.services.chip_ledger.credit
docstring'i - bu yalnizca gercek satin alma entegrasyonu icin ayrilmistir).
Talep, ileride gercek bir odeme akisi baglanana kadar yalnizca `pending`
durumda kaydedilir.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ChipPackage:
    key: str
    name: str
    chip_amount: int


CHIP_PACKAGE_VERSIONS: dict[str, tuple[ChipPackage, ...]] = {
    "2026.1": (
        ChipPackage(key="starter", name="Başlangıç paketi", chip_amount=100),
        ChipPackage(key="growth", name="Büyüme paketi", chip_amount=500),
        ChipPackage(key="scale", name="Ölçek paketi", chip_amount=2000),
    ),
}

CURRENT_CHIP_PACKAGE_VERSION = "2026.1"


def get_chip_packages(version: str | None = None) -> tuple[ChipPackage, ...]:
    """Verilen surumun (veya guncel surumun) Chip yukleme paketlerini dondurur."""

    key = version or CURRENT_CHIP_PACKAGE_VERSION
    try:
        return CHIP_PACKAGE_VERSIONS[key]
    except KeyError as exc:
        raise ValueError(f"Bilinmeyen Chip paketi surumu: {key}") from exc


def get_chip_package(package_key: str, version: str | None = None) -> ChipPackage:
    """Verilen anahtara sahip paketi dondurur; bulunamazsa `ValueError` firlatir."""

    for package in get_chip_packages(version):
        if package.key == package_key:
            return package
    raise ValueError(f"Bilinmeyen Chip paketi: {package_key}")


__all__ = [
    "CHIP_PACKAGE_VERSIONS",
    "CURRENT_CHIP_PACKAGE_VERSION",
    "ChipPackage",
    "get_chip_package",
    "get_chip_packages",
]
