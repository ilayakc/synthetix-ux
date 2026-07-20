#!/usr/bin/env python3
"""Frontend-backend semasi uyum kontrolu (hafif bir "contract test").

Backend'in urettigi OpenAPI semasini (`app.openapi()`), `frontend/src/api/client.ts`
icindeki `apiFetch(...)` cagrilarinda kullanilan gercek istek path'leriyle
karsilastirir. Frontend'in cagirdigi ama backend semasinda artik var olmayan
bir uc nokta varsa (ornegin bir router yanlislikla kaldirildi/yeniden
adlandirildi), bu script non-zero exit ile basarisiz olur.

Kasitli olarak agir bir sozlesme test araci (pact/schemathesis/dredd) EKLEMEZ;
yalnizca Python stdlib + docker compose (semayi backend container'indan almak
icin) kullanir - "sade" arac gereksinimine uygun.

Kullanim:
    python scripts/contract_check.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OPENAPI_JSON = REPO_ROOT / "backend" / "openapi.json"
CLIENT_TS = REPO_ROOT / "frontend" / "src" / "api" / "client.ts"

# client.ts'de path olusturmak icin kullanilan bir kac fonksiyon/degisken
# adi (template literal icindeki `${...}` kisimlari) - bunlar OpenAPI'nin
# `{param}` bicimindeki path parametreleriyle eslestirilir.
_TEMPLATE_EXPR = re.compile(r"\$\{[^}]+\}")

# `apiFetch<...>("/api/...")` veya backtick template literal cagrilari.
_PATH_LITERAL = re.compile(r"""apiFetch<[^>]*>\(\s*(?:`([^`]+)`|"([^"]+)")""")


def _dump_openapi_schema() -> dict:
    """Backend container'i icinde `app.openapi()` semasini uretip JSON olarak okur."""

    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "backend",
            "python",
            "-c",
            "import json; from app.main import app; print(json.dumps(app.openapi()))",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("HATA: backend'den OpenAPI semasi alinamadi.", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(2)

    schema = json.loads(result.stdout)
    OPENAPI_JSON.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    return schema


def _normalize_path(path: str) -> str:
    """Path parametrelerini `{param}` ile birlestirilebilir tek bir forma indirger."""

    normalized = _TEMPLATE_EXPR.sub("{param}", path)
    # Sorgu dizgesini (varsa) at; OpenAPI path'leri sorgu icermez.
    normalized = normalized.split("?", 1)[0]
    # `{encodeURIComponent(...)}` gibi ifadeler de yukarida {param} oldu;
    # OpenAPI tarafindaki gercek parametre adlarini (`{report_id}` vb.)
    # tek bir joker karaktere indirgeyerek karsilastiriyoruz.
    normalized = re.sub(r"\{[^}]+\}", "{param}", normalized)
    # `/api/projects${query}` gibi bir onceki '/' olmadan dogrudan path
    # sonuna eklenmis bir `{param}` gercek bir path segmenti degil, bir
    # sorgu dizgesi degiskenidir (frontend'de `?...` zaten degiskenin
    # icinde olusturulur) - path karsilastirmasindan cikarilir.
    normalized = re.sub(r"(?<!/)\{param\}$", "", normalized)
    return normalized


def _extract_frontend_paths() -> set[str]:
    if not CLIENT_TS.exists():
        print(f"HATA: {CLIENT_TS} bulunamadi.", file=sys.stderr)
        sys.exit(2)

    text = CLIENT_TS.read_text(encoding="utf-8")
    paths = set()
    for match in _PATH_LITERAL.finditer(text):
        raw_path = match.group(1) or match.group(2)
        if raw_path and raw_path.startswith("/api/"):
            paths.add(_normalize_path(raw_path))
    return paths


def _extract_backend_paths(schema: dict) -> set[str]:
    return {_normalize_path(path) for path in schema.get("paths", {})}


def main() -> int:
    schema = _dump_openapi_schema()
    frontend_paths = _extract_frontend_paths()
    backend_paths = _extract_backend_paths(schema)

    orphaned = sorted(frontend_paths - backend_paths)

    if orphaned:
        print(
            "Frontend'in cagirdigi ama backend OpenAPI semasinda bulunmayan uc noktalar:\n",
            file=sys.stderr,
        )
        for path in orphaned:
            print(f"- {path}", file=sys.stderr)
        print(
            f"\nToplam {len(frontend_paths)} frontend path'i, {len(backend_paths)} "
            "backend path'iyle karsilastirildi.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Sozlesme uyumlu: {len(frontend_paths)} frontend path'inin tumu backend "
        f"semasinda ({len(backend_paths)} path) karsiligini buluyor."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
