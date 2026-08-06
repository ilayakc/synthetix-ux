"""Kontrollu otomatik retry + stale RUNNING recovery POLITIKASI (Faz 3B.2C).

Bu modul, AI pipeline worker'inin retry/stale davranisinin TEK, PAYLASILAN
politika kaynagidir:

- Sabitler (`MAX_STAGE_ATTEMPTS`, `STALE_RUNNING_TIMEOUT_SECONDS`,
  `STALE_REAP_BATCH_LIMIT`) yalnizca BURADA tanimlanir - worker ve testler
  bunlari import eder, kopyalamaz.
- `is_retryable_error`, bir hatanin retryable olup olmadigini YALNIZCA
  exception TIPINDEN / typed `retryable` alanindan belirler; hicbir zaman hata
  MESAJINI (string) incelemez (bkz. gorev talimati Part 4).

Retryable (True): provider timeout, gecici network/transport hatasi (ve typed
`AIProviderError.retryable=True` isaretli her hata). Ayrica worker cokmesi
nedeniyle stale RUNNING de "retryable" muamelesi gorur ama bu ayri bir yolla
(`reap_stale_ai_stages`) ele alinir - buradaki siniflandirmayla ayni mantigi
(attempt < MAX -> yeniden kuyruk) paylasir.

Non-retryable (False): validation/schema hatasi (`AIPipelineError` ve
`AIProviderInvalidOutputError`), unsupported stage / configuration hatasi,
hash/idempotency uyusmazligi, provider/model pin uyusmazligi, eksik dependency,
bozuk persisted output. Bu hatalar deneme sayisindan BAGIMSIZ terminaldir.
"""

from __future__ import annotations

from app.services.ai_pipeline.provider_errors import AIProviderError

# --- Retry/stale sabitleri (TEK kaynak) ------------------------------------------

# `attempt_count=1` ilk deneme; retryable hata -> bir kez daha QUEUED;
# `attempt_count=2` son deneme; ikinci hata terminal FAILED. Sonsuz retry
# MUMKUN DEGILDIR (attempt_count yalnizca claim'de artar).
MAX_STAGE_ATTEMPTS = 2

# Bir RUNNING stage'in "stale" (worker cokmus/kopmus) sayilmasi icin gecmesi
# gereken sure. Sinir davranisi (bkz. `reap_stale_ai_stages`): yas >=
# STALE_RUNNING_TIMEOUT_SECONDS ise stale (updated_at <= cutoff).
STALE_RUNNING_TIMEOUT_SECONDS = 600

# Tek bir reap cagrisinda islenecek azami stale stage sayisi (deterministik
# sirali, SKIP LOCKED batch).
STALE_REAP_BATCH_LIMIT = 20


def is_retryable_error(exc: BaseException) -> bool:
    """`exc` GECICI/kurtarilabilir bir hata mi (yani bir kez daha denenebilir mi)?

    Karar YALNIZCA exception TIPINDEN / typed `AIProviderError.retryable`
    alanindan verilir - hata mesaji (string) ASLA incelenmez. Provider disi
    hatalar (validation/`AIPipelineError`, hash/pin uyusmazligi, config vb.)
    daima non-retryable'dir.
    """

    if isinstance(exc, AIProviderError):
        return exc.retryable
    return False


__all__ = [
    "MAX_STAGE_ATTEMPTS",
    "STALE_RUNNING_TIMEOUT_SECONDS",
    "STALE_REAP_BATCH_LIMIT",
    "is_retryable_error",
]
