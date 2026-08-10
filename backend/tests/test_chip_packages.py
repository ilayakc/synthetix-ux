"""Chip yukleme paketleri: surumleme ve bilinmeyen paket/surum reddi."""

import pytest

from app.services.chip_packages import CURRENT_CHIP_PACKAGE_VERSION, get_chip_package, get_chip_packages

pytestmark = pytest.mark.unit


def test_get_chip_packages_returns_current_version_by_default():
    packages = get_chip_packages()
    assert {p.key for p in packages} == {"mini", "starter", "standard", "growth", "pro", "scale"}
    assert {p.chip_amount for p in packages} == {50, 100, 200, 500, 1000, 2000}
    assert {p.price_try for p in packages} == {119, 199, 349, 799, 1399, 2499}


def test_legacy_chip_packages_remain_resolvable_without_prices():
    assert all(package.price_try is None for package in get_chip_packages("2026.1"))


def test_get_chip_packages_rejects_unknown_version():
    with pytest.raises(ValueError):
        get_chip_packages("9999.99-does-not-exist")


def test_get_chip_package_returns_matching_package():
    package = get_chip_package("growth")
    assert package.chip_amount == 500
    assert package.key == "growth"


def test_get_chip_package_rejects_unknown_key():
    with pytest.raises(ValueError):
        get_chip_package("not-a-real-package")


def test_current_chip_package_version_is_resolvable():
    assert get_chip_packages(CURRENT_CHIP_PACKAGE_VERSION) == get_chip_packages()
