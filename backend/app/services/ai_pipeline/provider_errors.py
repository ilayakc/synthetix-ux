"""AI provider katmaninin GUVENLI, sinirli hata hiyerarsisi (Faz 2B).

Bu hatalar, gercek/mock bir `AIProvider`in `generate_structured` cagrisi
sirasinda olusan sorunlari temsil eder. `errors.py`nin dokstring disiplini
AYNEN GECERLIDIR: hicbir hata mesajinda ham provider cevabi, istek govdesi,
prompt metni, API anahtari, URL query secret'i, stack trace veya persona
payload TASINMAZ - yalnizca kisa, insan-okunabilir bir aciklama.

`error_code` alani, ileride `AIPipelineStage.error_code` (String(100))
kolonuna dogrudan yazilabilecek kadar kisa ve sabittir.
"""

from __future__ import annotations

from typing import ClassVar

# Her alt sinif icin sabit, guvenli hata kodu (bos string yerine acik etiket).
ERROR_CODE_PROVIDER_TIMEOUT = "provider_timeout"
ERROR_CODE_PROVIDER_TRANSPORT = "provider_transport_error"
ERROR_CODE_PROVIDER_INVALID_OUTPUT = "provider_invalid_output"
ERROR_CODE_PROVIDER_UNSUPPORTED_STAGE = "provider_unsupported_stage"
# Faz 3D.1 (gercek OpenAI adaptoru) icin eklenen ek, sabit/guvenli hata kodlari.
ERROR_CODE_PROVIDER_REFUSAL = "provider_refusal"
ERROR_CODE_PROVIDER_RATE_LIMITED = "provider_rate_limited"
ERROR_CODE_PROVIDER_SERVER_ERROR = "provider_server_error"
ERROR_CODE_PROVIDER_AUTHENTICATION = "provider_authentication_error"
ERROR_CODE_PROVIDER_PERMISSION = "provider_permission_error"
ERROR_CODE_PROVIDER_INVALID_REQUEST = "provider_invalid_request"
ERROR_CODE_PROVIDER_CONFIGURATION = "provider_configuration_error"


class AIProviderError(Exception):
    """Tum provider hatalarinin temel sinifi.

    Temel sinif, dort ozel alt sinifa uymayan genel sozlesme ihlalleri icin
    (ornegin `generate_structured`e yanlis tipte `input_payload` gecilmesi)
    keyfi bir `error_code` ile DOGRUDAN firlatilabilir. `message` yalnizca
    kisa, guvenli bir aciklamadir - asla ham girdi/cikti icermez.

    `retryable` (Faz 3B.2C) - kontrollu otomatik retry icin TYPED siniflandirma
    sinyali. Taban sinif ve sema/sozlesme ihlali temsil eden alt siniflar icin
    varsayilan `False`'tur (muhafazakar); yalnizca GECICI/kurtarilabilir
    hatalar (timeout, transport) `True` isaretler. Retry karari ASLA hata
    MESAJINDAN (string) turetilmez - yalnizca bu typed alandan/exception
    TIPINDEN (bkz. `app.services.ai_pipeline.retry_policy.is_retryable_error`).
    """

    retryable: ClassVar[bool] = False

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


class AIProviderTimeoutError(AIProviderError):
    """Provider cagrisi zaman asimina ugradiginda firlatilir (GECICI - retryable)."""

    retryable: ClassVar[bool] = True

    def __init__(self, message: str) -> None:
        super().__init__(ERROR_CODE_PROVIDER_TIMEOUT, message)


class AIProviderTransportError(AIProviderError):
    """Ag/tasima katmani hatasi (baglanti, HTTP hatasi vb.) - GECICI, retryable."""

    retryable: ClassVar[bool] = True

    def __init__(self, message: str) -> None:
        super().__init__(ERROR_CODE_PROVIDER_TRANSPORT, message)


class AIProviderInvalidOutputError(AIProviderError):
    """Provider ciktisi beklenen sema/tutarlilik kurallarini ihlal ettiginde
    firlatilir (KALICI - retry AYNI hatayi verir, `retryable=False`)."""

    def __init__(self, message: str) -> None:
        super().__init__(ERROR_CODE_PROVIDER_INVALID_OUTPUT, message)


class AIProviderUnsupportedStageError(AIProviderError):
    """Desteklenmeyen bir `stage_type` icin cagri yapildiginda firlatilir
    (KALICI konfig/sozlesme hatasi, `retryable=False`)."""

    def __init__(self, message: str) -> None:
        super().__init__(ERROR_CODE_PROVIDER_UNSUPPORTED_STAGE, message)


class AIProviderRefusalError(AIProviderError):
    """Provider (OpenAI) istegi ACIKCA reddettiginde (refusal) firlatilir -
    KALICI/non-retryable (bkz. gorev talimati Faz 3D.1 madde 9): ayni istek
    tekrar denendiginde de büyük ihtimalle ayni sekilde reddedilir. Refusal'in
    HAM metni bu hataya/mesajina ASLA eklenmez - yalnizca sabit, guvenli bir
    aciklama tasinir."""

    def __init__(self, message: str) -> None:
        super().__init__(ERROR_CODE_PROVIDER_REFUSAL, message)


class AIProviderRateLimitError(AIProviderError):
    """Provider rate-limit (HTTP 429) dondurdugunde firlatilir - GECICI,
    retryable (bkz. gorev talimati madde 10)."""

    retryable: ClassVar[bool] = True

    def __init__(self, message: str) -> None:
        super().__init__(ERROR_CODE_PROVIDER_RATE_LIMITED, message)


class AIProviderServerError(AIProviderError):
    """Provider sunucu tarafi hatasi (HTTP 5xx/internal server error)
    dondurdugunde firlatilir - GECICI, retryable."""

    retryable: ClassVar[bool] = True

    def __init__(self, message: str) -> None:
        super().__init__(ERROR_CODE_PROVIDER_SERVER_ERROR, message)


class AIProviderAuthenticationError(AIProviderError):
    """Provider kimlik dogrulamasi (HTTP 401) reddettiginde firlatilir -
    KALICI/non-retryable. API anahtari/Authorization header ASLA mesaja
    eklenmez."""

    def __init__(self, message: str) -> None:
        super().__init__(ERROR_CODE_PROVIDER_AUTHENTICATION, message)


class AIProviderPermissionError(AIProviderError):
    """Provider izin hatasi (HTTP 403) dondurdugunde firlatilir - KALICI/
    non-retryable."""

    def __init__(self, message: str) -> None:
        super().__init__(ERROR_CODE_PROVIDER_PERMISSION, message)


class AIProviderInvalidRequestError(AIProviderError):
    """Malformed istek (HTTP 400/422) veya desteklenmeyen model (HTTP 404)
    icin firlatilir - KALICI/non-retryable (istek tekrar AYNI sekilde
    reddedilir)."""

    def __init__(self, message: str) -> None:
        super().__init__(ERROR_CODE_PROVIDER_INVALID_REQUEST, message)


class AIProviderConfigurationError(AIProviderError):
    """Provider olusturulmadan ONCE tespit edilen bir yapilandirma hatasi
    (ornegin eksik API key/model) icin firlatilir - KALICI/non-retryable."""

    def __init__(self, message: str) -> None:
        super().__init__(ERROR_CODE_PROVIDER_CONFIGURATION, message)


__all__ = [
    "ERROR_CODE_PROVIDER_TIMEOUT",
    "ERROR_CODE_PROVIDER_TRANSPORT",
    "ERROR_CODE_PROVIDER_INVALID_OUTPUT",
    "ERROR_CODE_PROVIDER_UNSUPPORTED_STAGE",
    "ERROR_CODE_PROVIDER_REFUSAL",
    "ERROR_CODE_PROVIDER_RATE_LIMITED",
    "ERROR_CODE_PROVIDER_SERVER_ERROR",
    "ERROR_CODE_PROVIDER_AUTHENTICATION",
    "ERROR_CODE_PROVIDER_PERMISSION",
    "ERROR_CODE_PROVIDER_INVALID_REQUEST",
    "ERROR_CODE_PROVIDER_CONFIGURATION",
    "AIProviderError",
    "AIProviderTimeoutError",
    "AIProviderTransportError",
    "AIProviderInvalidOutputError",
    "AIProviderUnsupportedStageError",
    "AIProviderRefusalError",
    "AIProviderRateLimitError",
    "AIProviderServerError",
    "AIProviderAuthenticationError",
    "AIProviderPermissionError",
    "AIProviderInvalidRequestError",
    "AIProviderConfigurationError",
]
