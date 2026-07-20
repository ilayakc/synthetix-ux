#!/usr/bin/env python3
"""Kritik is kurallarini tasiyan dosyalarda %100 branch coverage zorunlulugunu
kontrol eder.

`pytest --cov=app --cov-branch --cov-report=json` calistirildiktan sonra
uretilen `backend/coverage.json` dosyasini okur; asagida listelenen her
dosyada branch coverage tam olarak %100 degilse, hangi satirlarin eksik
kaldigini raporlayip non-zero exit ile basarisiz olur.

Bu dosyalar 0 Chip / ucretsiz hak tek kullanimi / 1.000 persona siniri /
ledger idempotency / tenant izolasyonu / bilimsel uyari gibi degismez urun
kurallarini tasidigi icin (bkz. docs/product-rules.md), buradaki her dal en
az bir testle kanitlanmis olmalidir - yalnizca genel kapsama yuzdesi yeterli
degildir.

Kullanim:
    python scripts/check_critical_coverage.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COVERAGE_JSON = REPO_ROOT / "backend" / "coverage.json"

# Dosya yollari, coverage.json icindeki (backend/ kok dizinine gore) anahtarlarla eslesir.
CRITICAL_FILES = [
    "app/services/chip_ledger.py",
    "app/services/entitlements.py",
    "app/services/quotes.py",
    "app/services/pricing.py",
    "app/services/module_catalog.py",
    "app/services/chip_packages.py",
    "app/dependencies.py",
    "app/engine/baseline.py",
]

REQUIRED_BRANCH_COVERAGE = 100.0


def main() -> int:
    if not COVERAGE_JSON.exists():
        print(
            f"HATA: {COVERAGE_JSON} bulunamadi. Once backend icinde "
            "`pytest --cov=app --cov-branch --cov-report=json` calistirin.",
            file=sys.stderr,
        )
        return 2

    data = json.loads(COVERAGE_JSON.read_text(encoding="utf-8"))
    files = data.get("files", {})

    failures: list[str] = []

    for relative_path in CRITICAL_FILES:
        file_report = files.get(relative_path)
        if file_report is None:
            failures.append(f"- {relative_path}: coverage.json'da bulunamadi (dosya adi degisti mi?)")
            continue

        summary = file_report.get("summary", {})
        percent = summary.get("percent_covered_display") or summary.get("percent_covered")
        missing_lines = file_report.get("missing_lines", [])
        partial_branches = file_report.get("missing_branches", {}) or file_report.get(
            "excluded_lines", []
        )

        num_branches = summary.get("num_branches", 0)
        covered_branches = summary.get("covered_branches", 0)

        if num_branches == 0:
            # Dalsiz (saf dogrusal) bir dosya: branch kosulu otomatik saglanir,
            # yalnizca satir kapsaminin %100 oldugunu dogrula.
            if summary.get("percent_covered", 0.0) < 100.0:
                failures.append(
                    f"- {relative_path}: dal yok ama satir kapsami %100 degil "
                    f"({percent}%). Eksik satirlar: {missing_lines}"
                )
            continue

        branch_percent = (covered_branches / num_branches) * 100 if num_branches else 100.0
        if branch_percent < REQUIRED_BRANCH_COVERAGE:
            failures.append(
                f"- {relative_path}: branch coverage %{branch_percent:.1f} "
                f"(gerekli: %100). Eksik satirlar/dallar: {missing_lines}"
            )

    if failures:
        print("Kritik dosyalarda %100 branch coverage saglanamadi:\n", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        print(
            "\nBu dosyalar 0 Chip / tek kullanimlik ucretsiz hak / 1.000 persona "
            "siniri / ledger idempotency / tenant izolasyonu gibi degismez urun "
            "kurallarini tasir; eksik dallari kapatan test case'leri ekleyin.",
            file=sys.stderr,
        )
        return 1

    print(f"Tum kritik dosyalarda branch coverage %{REQUIRED_BRANCH_COVERAGE:.0f}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
