"""Asamalar arasi PAYLASILAN, saf hash/idempotency yardimcilari (Faz 3B.2A).

Bu modul, daha once `stage_runner.py` ve `batching.py` icinde SATIR-ICI
(inline) yazilmis olan kanonik hash-payload kurma mantigini TEK bir yere
cikarir. Amac (bkz. Faz 3B.2A gorev talimati, Part 2 & Part 5): hem asama
calistiricisi (`stage_runner`) hem de yeni persistence/initialization
katmani (`persistence.py`/`orchestration.py`) AYNI yardimciyi cagirsin -
hicbir yerde ikinci bir hash algoritmasi/yapisi YENIDEN yazilmasin.

ONEMLI: Buradaki fonksiyonlar, tasindiklari yerdeki ESKI hesaplamayla
BIREBIR AYNI kanonik yapiyi uretir (golden-deger korunmasi) - `stage_runner`
ve `batching` artik bu fonksiyonlari cagirir, dolayisiyla mevcut hash/
idempotency degerleri DEGISMEZ (bkz. regresyon testleri).

Tamamen saftir: DB'ye erismez, ag cagrisi yapmaz, yan etki uretmez.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline.hashing import (
    DETERMINISTIC_MODEL,
    DETERMINISTIC_PROVIDER,
    IdempotencyKeyInput,
    compute_idempotency_key,
    hash_payload,
)
from app.services.ai_pipeline.schemas import PersonaBatch, PersonaContext

# Saf (LLM'siz) asamalarin idempotency key'inde kullanilacak sabit,
# reprodusible prompt etiketleri (bos string yerine acik/kararli degerler).
# Bu iki sabit daha once `stage_runner.py` icinde `_DETERMINISTIC_PROMPT_*`
# olarak yasiyordu; deger AYNIDIR (golden korunur).
DETERMINISTIC_PROMPT_VERSION = "deterministic-v1"
DETERMINISTIC_PROMPT_HASH = hash_payload("deterministic")


def evidence_stage_input_hash(
    *,
    source_type: str,
    metrics: object,
    page_features: object,
    selected_modules: Sequence[str],
    module_results: object,
) -> str:
    """Stage 1 (EVIDENCE_PREPARATION) `input_hash`ini hesaplar.

    Kanonik yapi, `stage_runner.run_evidence_stage`in ESKI satir-ici
    hesaplamasiyla BIREBIR AYNIDIR - bu yardimci, hem calistiricinin hem de
    initialization katmaninin ayni `input_hash`i (ve dolayisiyla ayni
    deterministik `idempotency_key`i) uretmesini garanti eder.
    """

    return hash_payload(
        {
            "source_type": source_type,
            "metrics": metrics,
            "page_features": page_features,
            "selected_modules": list(selected_modules),
            "module_results": module_results,
        }
    )


def deterministic_stage_idempotency_key(
    *,
    simulation_run_id: uuid.UUID,
    stage_type: AIPipelineStageType,
    input_hash: str,
    batch_index: int | None = None,
) -> str:
    """Saf (LLM'siz) bir asama satiri icin deterministik `idempotency_key`.

    Daha once `stage_runner._deterministic_idempotency_key` icindeydi;
    hesaplama BIREBIR AYNIDIR (sabit deterministik prompt/provider/model
    etiketleri). Boylece `orchestration.initialize_ai_pipeline` tarafindan
    olusturulan QUEUED evidence satirinin key'i, worker ileride Stage 1'i
    calistirdiginda `run_evidence_stage`in urettigi key ile AYNIDIR.

    `batch_index` VARSAYILAN OLARAK `None`'dir - boylece Stage 1/2/5 gibi
    batch'lenmeyen asamalarin key'i (ve mevcut golden degerleri) DEGISMEZ.
    Faz 3B.2B worker'i, PERSONA_BEHAVIOR fan-out'unda her batch icin AYRI bir
    QUEUED satir olusturdugunda (deterministik, provider-bagimsiz bir DAG dedup
    anahtari uretmek icin) `batch_index`i acikca gecerek her batch'e benzersiz
    bir key verir; `input_hash` zaten batch'e gore farklidir, `batch_index`
    ek/savunma amacli bir ayrim saglar.
    """

    return compute_idempotency_key(
        IdempotencyKeyInput(
            simulation_run_id=simulation_run_id,
            stage_type=stage_type.value,
            batch_index=batch_index,
            prompt_version=DETERMINISTIC_PROMPT_VERSION,
            prompt_hash=DETERMINISTIC_PROMPT_HASH,
            provider=DETERMINISTIC_PROVIDER,
            model_name=DETERMINISTIC_MODEL,
            input_hash=input_hash,
        )
    )


def provider_stage_execution_idempotency_key(
    *,
    simulation_run_id: uuid.UUID,
    stage_type: AIPipelineStageType,
    batch_index: int | None,
    prompt_version: str,
    prompt_hash: str,
    provider: str,
    model_name: str,
    input_hash: str,
    provider_configuration_fingerprint: str,
) -> str:
    """Provider (LLM) asamalari (3/4/6) icin GERCEK execution idempotency key'i.

    Bu, `AIPipelineStage.execution_idempotency_key`e yazilacak deger olup,
    `deterministic_stage_idempotency_key`den (logical) FARKLI olarak gercek
    provider/model/prompt kimligine baglidir. Faz 3B.2C worker'i bu key'i
    provider cagrisindan ONCE (pinning) hesaplar; `stage_runner` de basarili
    sonucta AYNI key'i uretir (bkz. `_provider_stage_audit` - bu ayni yardimciyi
    cagirir). Boylece TEK bir hash algoritmasi vardir: pre-pin ve audit key'i
    BIREBIR eslesir (uyusmazlik integrity failure sayilir).

    `provider_configuration_fingerprint` (Faz 3D.1): provider'in SEMANTIK
    yapilandirma kimligi (bkz. `provider.compute_configuration_fingerprint`) -
    yalnizca `model_name` alaninin yakalayamayacagi reasoning effort/
    structured-output modu/semantik uretim ayari degisikliklerinin de execution
    key'ini degistirmesini saglamak icin `input_hash` ile birlikte katlanir
    (GOLDEN DEGISIKLIK: bu alanin eklenmesi mevcut provider execution key
    degerlerini DEGISTIRIR - bkz. rapor "bilinen riskler"; `deterministic_
    stage_idempotency_key` / logical DAG key BUNDAN ETKILENMEZ).
    """

    folded_input_hash = hash_payload(
        {
            "input_hash": input_hash,
            "provider_configuration_fingerprint": provider_configuration_fingerprint,
        }
    )
    return compute_idempotency_key(
        IdempotencyKeyInput(
            simulation_run_id=simulation_run_id,
            stage_type=stage_type.value,
            batch_index=batch_index,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            provider=provider,
            model_name=model_name,
            input_hash=folded_input_hash,
        )
    )


def compute_batch_hash(
    *,
    batch_index: int,
    personas: Sequence[PersonaContext],
    population_weight: int,
) -> str:
    """Tek bir persona batch'inin `batch_hash`ini hesaplar.

    Kanonik yapi, `batching.build_persona_batches`in ESKI satir-ici
    hesaplamasiyla BIREBIR AYNIDIR. Rehydration (bkz. `persistence.py`) bu
    ayni yardimciyi kullanarak yeniden-uretilen batch'in hash'ini dogrular -
    hash yapisi hicbir yerde tekrar yazilmaz/tahmin edilmez.
    """

    return hash_payload(
        {
            "batch_index": batch_index,
            "personas": [p.model_dump(mode="json") for p in personas],
            "population_weight": population_weight,
        }
    )


def persona_batches_output_hash(batches: Sequence[PersonaBatch]) -> str:
    """Stage 2 (PERSONA_BATCH_PREPARATION) `output_hash`ini hesaplar.

    Kanonik yapi, `stage_runner.run_batching_stage`in ESKI satir-ici
    hesaplamasiyla (`hash_payload([b.model_dump(mode="json") for b in batches])`)
    BIREBIR AYNIDIR. Rehydration edilmis (DB'den taze `attributes` ile
    yeniden kurulmus) tam `PersonaBatch` tuple'i, bu ayni yardimciyla
    hash'lendiginde, pipeline-init aninda `stage_runner` tarafindan kaydedilen
    `output_hash` ile AYNI cikmalidir (bkz. Faz 3B.2A Part 2 "kritik hash
    kurali").
    """

    return hash_payload([b.model_dump(mode="json") for b in batches])


__all__ = [
    "DETERMINISTIC_PROMPT_VERSION",
    "DETERMINISTIC_PROMPT_HASH",
    "evidence_stage_input_hash",
    "deterministic_stage_idempotency_key",
    "provider_stage_execution_idempotency_key",
    "compute_batch_hash",
    "persona_batches_output_hash",
]
