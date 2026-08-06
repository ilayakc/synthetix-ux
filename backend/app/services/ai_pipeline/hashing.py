"""Canonical JSON serilestirme + SHA-256 hash yardimcilari, ve bunlarin
uzerine kurulu prompt-descriptor/idempotency-key hesaplayicilari.

Bu modul TAMAMEN saftir: DB'ye erismez, ag cagrisi yapmaz, hicbir yan etki
uretmez. `app.engine.baseline._hash_input_snapshot` (motor determinizmi
icin, `json.dumps(..., default=str)` ile GEVSEK/toleransli davranir) ile
KARISTIRILMAMALIDIR - o motorun geriye donuk hash'lerini bozmamak icin
buradan BAGIMSIZ, kasitli olarak daha SIKI (desteklenmeyen tip -> acik
hata, NaN/Infinity -> acik hata) bir yeni implementasyondur (bkz. Faz 2A
gorev talimati madde 2).
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

from app.services.ai_pipeline.errors import ERROR_CANONICALIZATION_FAILED, AIPipelineError

# --- Canonical JSON --------------------------------------------------------------


def _normalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="json"))
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AIPipelineError(ERROR_CANONICALIZATION_FAILED, "NaN/Infinity degerleri hash'lenemez")
        return value
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_normalize(item) for item in value]

    raise AIPipelineError(
        ERROR_CANONICALIZATION_FAILED,
        f"Desteklenmeyen tip hash'lenemez: {type(value).__name__}",
    )


def canonical_json(value: Any) -> str:
    """`value`i kararli/kanonik bir JSON metnine cevirir.

    Garantiler: dict anahtarlari (butun ic ice seviyelerde) siralanir,
    gereksiz bosluk yok, UTF-8/Turkce karakterler kacirilmadan (escape
    edilmeden) tasinir, liste sirasi KORUNUR (anlamlidir), NaN/Infinity ve
    desteklenmeyen tipler acikca reddedilir (asla sessizce `str()`e
    dusurulmez).
    """

    normalized = _normalize(value)
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def hash_payload(value: Any) -> str:
    """`value`in canonical JSON gosteriminin SHA-256 hex digest'i (64 karakter, kucuk harf)."""

    text = canonical_json(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Prompt descriptor hash --------------------------------------------------------


class PromptDescriptor(BaseModel):
    """Henuz gercek bir prompt dosyasi/metni DEGIL - ileride (Faz 2B+) her
    stage'in prompt'unu tanimlayacak sabit alan seti icin SAF bir hash
    girdisi sozlesmesidir (bkz. gorev talimati madde 3). `system_instructions`
    bu asamada testlerde ornek/sabit bir metin olarak kullanilabilir; DB'ye
    hicbir zaman yazilmaz (bkz. AIPipelineStage - ham prompt metni saklamaz)."""

    prompt_key: str
    prompt_version: str
    system_instructions: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    model_settings: dict[str, Any]


def hash_prompt_descriptor(descriptor: PromptDescriptor) -> str:
    """Ayni descriptor -> ayni hash; prompt metni/surumu/sema/model ayari
    degisirse hash degisir (bkz. modul dokstring'i)."""

    return hash_payload(descriptor)


# --- Idempotency key ---------------------------------------------------------------

# LLM/provider cagrisi YAPMAYAN (saf Python) asamalarda (Stage 1/2/5)
# kullanilacak sabit, acik provider/model etiketleri - bos string yerine.
DETERMINISTIC_PROVIDER = "deterministic"
DETERMINISTIC_MODEL = "python"


class IdempotencyKeyInput(BaseModel):
    """`AIPipelineStage.idempotency_key` hesaplamasi icin SAF girdi sozlesmesi.

    Tum alanlar ISIMLENDIRILMIS bir payload icinde hash'lenir (bkz.
    `compute_idempotency_key`) - string birlestirme ile ("ab"+"c" vb.)
    belirsizlik riski YOKTUR. `batch_index=None` ile `batch_index=0` farkli
    alanlar oldugu icin (biri null, digeri 0) canonical JSON'da farkli
    temsil edilir, dolayisiyla farkli key uretirler.
    """

    simulation_run_id: uuid.UUID
    stage_type: str
    batch_index: int | None = None
    prompt_version: str
    prompt_hash: str
    provider: str
    model_name: str
    input_hash: str


def compute_idempotency_key(payload: IdempotencyKeyInput) -> str:
    """SHA-256 hex `idempotency_key` (64 karakter) - `AIPipelineStage.
    idempotency_key` (String(128)) kolonuna rahatlikla sigar.

    Bu fonksiyon SAFTIR: DB'ye erismez, hicbir `AIPipelineStage` kaydi
    OLUSTURMAZ - yalnizca hesaplar, cagiran taraf sonucu ayrica persist eder.
    """

    return hash_payload(payload)


__all__ = [
    "canonical_json",
    "hash_payload",
    "PromptDescriptor",
    "hash_prompt_descriptor",
    "DETERMINISTIC_PROVIDER",
    "DETERMINISTIC_MODEL",
    "IdempotencyKeyInput",
    "compute_idempotency_key",
]
