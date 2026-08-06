"""arq worker job/cron zamanlamasi icin PAYLASILAN, tek dogruluk kaynagi sabitler.

`app.worker.WorkerSettings` ve `app.config.Settings` (provider timeout
dogrulamasi icin) BUNU import eder - deger baska hicbir yerde KOPYALANMAZ/
hardcode EDILMEZ (bkz. Faz 3D.2.1 gorev talimati madde 1). Bu modul KASITLI
olarak `app.config`/`app.worker`e BAGIMLI DEGILDIR (sifir bagimlilik) - boylece
her iki yonde de circular import riski olmadan import edilebilir.
"""

from __future__ import annotations

# arq'nin `Worker`/`WorkerSettings.job_timeout` degeri (arq'nin kendi
# varsayilani da 300sn'dir - burada ACIKCA sabitlenir, ORTUK/varsayilana
# guvenilmez). Bu deger, TEK bir arq job'i (ornegin
# `process_ai_pipeline_stage_job`) calisirken izin verilen azami suredir;
# asilirsa arq gorevi iptal eder (asyncio task cancellation).
ARQ_JOB_TIMEOUT_SECONDS = 300

# Bir provider request timeout'unun (ornegin `Settings.ollama_timeout_seconds`)
# `ARQ_JOB_TIMEOUT_SECONDS`den ne kadar KUCUK olmasi GEREKTIGI - provider
# cagrisinin DISINDA da (DB claim/pin/persist transaction'lari, serialize/
# parse) is parcasi zaman harcar; provider timeout'u job timeout'una tam
# esit/yakin olursa bu ek is icin PAY KALMAZ (bkz. Settings icindeki
# `_ensure_provider_timeout_below_job_timeout` alan dogrulayicisi).
MIN_JOB_TIMEOUT_SAFETY_MARGIN_SECONDS = 30

__all__ = ["ARQ_JOB_TIMEOUT_SECONDS", "MIN_JOB_TIMEOUT_SAFETY_MARGIN_SECONDS"]
