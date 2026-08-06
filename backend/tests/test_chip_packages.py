"""Chip yukleme paketleri: surumleme ve bilinmeyen paket/surum reddi."""

import pytest

from app.services.chip_packages import CURRENT_CHIP_PACKAGE_VERSION, get_chip_package, get_chip_packages

pytestmark = pytest.mark.unit


def test_get_chip_packages_returns_current_version_by_default():
    packages = get_chip_packages()
    assert {p.key for p in packages} == {"starter", "growth", "scale"}
    assert {p.name for p in packages} == {"Başlangıç paketi", "Büyüme paketi", "Ölçek paketi"}


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
