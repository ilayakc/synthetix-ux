"""AI pipeline'in saf (DB'siz, ag cagrisiz) katmani icin sinirli domain hata tipleri.

Tek bir hata sinifi (`AIPipelineError`, `ValueError` alt sinifi - repo'nun
mevcut `PersonaValidationError`/`SimulationInputError` deseniyle ayni ruh)
+ sabit, guvenli `code` degerleri. Mesaj metinlerinde HICBIR ZAMAN ham
payload, PII, HTML, prompt veya provider cevabi TASINMAZ - yalnizca kisa,
insan-okunabilir bir aciklama (bkz. her ERROR_* kullanim yeri). `code`
alani, ileride `AIPipelineStage.error_code` kolonuna DOGRUDAN yazilabilecek
kadar kisa ve guvenlidir.
"""

from __future__ import annotations

ERROR_INVALID_PAYLOAD = "invalid_payload"
ERROR_INVALID_EVIDENCE = "invalid_evidence"
ERROR_PERSONAS_REQUIRED = "personas_required"
ERROR_DUPLICATE_PERSONA = "duplicate_persona"
ERROR_INVALID_PERSONA_DIMENSION = "invalid_persona_dimension"
ERROR_BATCH_LIMIT_EXCEEDED = "batch_limit_exceeded"
ERROR_PERSONA_RESULT_MISMATCH = "persona_result_mismatch"
ERROR_INVALID_EVIDENCE_REFERENCE = "invalid_evidence_reference"
ERROR_INVALID_HYPOTHESIS_REFERENCE = "invalid_hypothesis_reference"
ERROR_INVALID_AGGREGATION = "invalid_aggregation"
ERROR_CANONICALIZATION_FAILED = "canonicalization_failed"
ERROR_BANNED_CLAIM = "banned_claim"


class AIPipelineError(ValueError):
    """AI pipeline'in saf katmanindaki tum domain hatalarinin temel sinifi.

    `code`, `AIPipelineStage.error_code` (String(100)) kolonuna yazilmaya
    uygun, sabit/kisa bir tanimlayicidir (bkz. modul dokstring'i). `message`
    yalnizca gelistirici/log icin kisa bir aciklamadir - hicbir zaman ham
    girdi/cikti/PII icermemelidir.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


__all__ = [
    "AIPipelineError",
    "ERROR_INVALID_PAYLOAD",
    "ERROR_INVALID_EVIDENCE",
    "ERROR_PERSONAS_REQUIRED",
    "ERROR_DUPLICATE_PERSONA",
    "ERROR_INVALID_PERSONA_DIMENSION",
    "ERROR_BATCH_LIMIT_EXCEEDED",
    "ERROR_PERSONA_RESULT_MISMATCH",
    "ERROR_INVALID_EVIDENCE_REFERENCE",
    "ERROR_INVALID_HYPOTHESIS_REFERENCE",
    "ERROR_INVALID_AGGREGATION",
    "ERROR_CANONICALIZATION_FAILED",
    "ERROR_BANNED_CLAIM",
]
