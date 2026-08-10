"""Isteğe bağlı, sağlayıcıdan bağımsız "AI destekli açıklama" katmanı.

Bu katman **karar vermez**; yalnızca zaten hesaplanmış, depolanmış rapor
metriklerini (bkz. `app.routers.reports`) doğal dile çevirip olası
açıklamalar/doğrulama önerileri sunar. Nihai değerlendirme her zaman bir
uzmana aittir (bkz. `AI_SECTION_UI_LABEL`).

Önemli ayrım (bkz. proje talimatı): geliştiricinin kendi Claude Pro/IDE
aboneliği bir API anahtarı SAYILMAZ. Bu modülün varsayılan sağlayıcısı
`AI_PROVIDER=none`'dır ve hiçbir anahtar/uzak servis olmadan tamamen
deterministik bir şablon açıklaması üretir; ürün bu sağlayıcıyla eksiksiz
çalışır. Uzak bir sağlayıcı yalnızca `AI_PROVIDER=remote` ve ilgili uç
nokta/anahtar ortam değişkenleriyle açıkça yapılandırılırsa devreye girer.

Veri minimizasyonu: bu modüle asla ham HTML, cookie, token, form değeri,
kullanıcı e-postası ya da başka bir kişisel veri VERİLMEZ. Girdi şeması
(`AIExplanationInput`) yalnızca zaten anonim/yapılandırılmış rapor
metriklerini, belirsizlik aralıklarını, metodoloji referanslarını ve
anonim segment etiketlerini taşıyabilir; `extra="forbid"` bu sınırı şema
düzeyinde de zorlar.

Bilimsel dürüstlük: bkz. docs/scientific-integrity.md ve
`app.engine.baseline.BANNED_CLAIM_PHRASES`. Bu modül ek olarak "gerçek
kullanıcı gördü/davrandı", "kesin neden-sonuç", "kanıtlandı", "gerçek göz
takibi", "hukuki/erişilebilirlik sertifikası" ve "gerçek pazar talebi"
gibi ifadeleri de yasaklar (bkz. `AI_BANNED_CLAIM_PHRASES`).
"""

from __future__ import annotations

import abc
import logging
from datetime import UTC, datetime
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import settings
from app.engine.baseline import BANNED_CLAIM_PHRASES
from app.logging_config import get_logger
from app.logging_utils import log_duration

logger = logging.getLogger(__name__)
_llm_logger = get_logger("llm")

# UI'da bu bölüm asla "AI kararı" olarak adlandırılmaz; otomatik üretildiği
# ve uzman değerlendirmesi gerektirdiği her zaman belirtilir.
AI_SECTION_UI_LABEL = "Hızlı rapor özeti"
AI_SECTION_UI_SUBLABEL = (
    "Mevcut rapor sonuçlarını kısaca açıklar; yeni bir AI analizi üretmez. "
    "Uzman değerlendirmesi/doğrulaması gerektirir."
)

PROMPT_VERSION = "ai-explanation-v1"

CALIBRATION_STATUS: Literal["uncalibrated"] = "uncalibrated"

# --- Yasak ifadeler --------------------------------------------------------------

AI_BANNED_CLAIM_PHRASES = (
    "gercek kullanici gordu",
    "gerçek kullanıcı gördü",
    "gercek kullanicilar gordu",
    "gerçek kullanıcılar gördü",
    "kullanici gordu",
    "kullanıcı gördü",
    "gercek kullanici davrandi",
    "gerçek kullanıcı davrandı",
    "kullanicilar boyle davrandi",
    "kullanıcılar böyle davrandı",
    "kesin neden-sonuc",
    "kesin neden-sonuç",
    "kesin olarak neden oldu",
    "kesinlikle neden oldu",
    "kanitlandi",
    "kanıtlandı",
    "gercek goz takibi",
    "gerçek göz takibi",
    "eye tracking",
    "hukuki sertifika",
    "hukuki sertifikasi",
    "hukuki sertifikası",
    "erisilebilirlik sertifikasi",
    "erişilebilirlik sertifikası",
    "yasal sertifika",
    "yasal uygunluk sertifikasi",
    "yasal uygunluk sertifikası",
    "gercek pazar talebi",
    "gerçek pazar talebi",
)


def assert_no_banned_ai_claims(text: str) -> None:
    """`text` içinde bilimsel dürüstlük veya AI-özel yasak ifadelerden biri geçiyorsa fırlatır."""

    if not text:
        return
    normalized = text.strip().lower()
    for phrase in (*BANNED_CLAIM_PHRASES, *AI_BANNED_CLAIM_PHRASES):
        if phrase in normalized:
            raise AIExplanationValidationError(
                f"Uretilen AI aciklamasi yasakli bir ifade iceriyor: '{phrase}'"
            )


class AIExplanationError(Exception):
    """AI açıklama katmanındaki tüm hataların temel sınıfı."""


class AIExplanationValidationError(AIExplanationError):
    """Üretilen çıktı şema/dürüstlük kurallarını ihlal ettiğinde fırlatılır."""


class AIProviderUnavailableError(AIExplanationError):
    """Uzak sağlayıcıya ulaşılamadığında (timeout, ağ hatası, vb.) fırlatılır."""


# --- Girdi şeması (sıkı: yalnızca yapılandırılmış/anonim veri) -------------------


class AIExplanationMetricSnapshot(BaseModel):
    """Rapordan alınan, zaten hesaplanmış tek bir metrik/alt-metrik değeri.

    Bilerek yalnızca sayısal/etiket alanları taşır; hiçbir serbest metin
    (ör. kullanıcı girdisi, sayfa içeriği) burada bulunmaz.
    """

    model_config = ConfigDict(extra="forbid")

    metric_id: str
    point_estimate: float | int | bool | None = None
    low: float | None = None
    high: float | None = None
    unit: str | None = None


class AIExplanationPersonaSegment(BaseModel):
    """Anonim, sentetik persona segment özeti (bkz. `PersonaSegmentSummary`)."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    count: int
    share: float
    small_sample_warning: bool


class AIExplanationCriticalFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    severity: str
    text: str


class AIExplanationInput(BaseModel):
    """Modele/şablon motoruna gönderilecek TEK girdi biçimi.

    `extra="forbid"` sayesinde bu şemaya uymayan hiçbir alan (ör. `html`,
    `cookies`, `token`, `user_email`, `form_values`) sessizce eklenemez;
    böyle bir alanı geçirmeye çalışan çağıran kod ValidationError alır.
    """

    model_config = ConfigDict(extra="forbid")

    report_id: str
    simulation_run_id: str
    calibration_status: Literal["uncalibrated"] = CALIBRATION_STATUS
    model_version: str
    rules_version: str | None
    fixture_version: str | None
    methodology_reference: str
    metrics: list[AIExplanationMetricSnapshot]
    critical_findings: list[AIExplanationCriticalFinding]
    persona_segments: list[AIExplanationPersonaSegment]

    @property
    def allowed_metric_ids(self) -> frozenset[str]:
        ids = {m.metric_id for m in self.metrics}
        ids.update(f"persona_segment:{s.key}" for s in self.persona_segments)
        return frozenset(ids)


# --- Çıktı şeması ------------------------------------------------------------------


class AIExplanationFinding(BaseModel):
    """Metin + zorunlu metrik dayanağı.

    Her bulgu, hangi metriğe (`metric_id`) dayandığını taşımak ZORUNDADIR;
    boş bir liste veya girdide bulunmayan bir kimlik reddedilir (bkz.
    `AIExplanationOutput.validate_metric_citations`). Bu, kaynaksız sayı ya
    da simülasyonda bulunmayan bir oranın üretilmesini şema düzeyinde
    engeller.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    metric_ids: list[str] = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("finding metni bos olamaz")
        return value

    @field_validator("metric_ids")
    @classmethod
    def _metric_ids_not_blank(cls, value: list[str]) -> list[str]:
        if any(not m or not m.strip() for m in value):
            raise ValueError("metric_ids bos ogeler iceremez")
        return value


class AIExplanationOutput(BaseModel):
    """AI destekli açıklama bölümünün tam yapısı.

    Bölümler (görev talimatındaki isimlerle birebir): kısa özet
    (`short_summary`), metrik dayanakları (`metric_basis`), olası
    açıklamalar (`possible_explanations`), önerilen doğrulama deneyi
    (`suggested_verification_experiment`), sınırlamalar (`limitations`).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    calibration_status: Literal["uncalibrated"] = CALIBRATION_STATUS
    short_summary: str
    metric_basis: list[AIExplanationFinding] = Field(min_length=1)
    possible_explanations: list[AIExplanationFinding] = Field(min_length=1)
    suggested_verification_experiment: str
    limitations: str

    # --- Audit alanları (bkz. modul dokstring'i) ---
    prompt_version: str
    provider: str
    model_name: str | None
    generated_at: datetime

    @field_validator("short_summary", "suggested_verification_experiment", "limitations")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("alan bos olamaz")
        return value

    def validate_against_input(self, ai_input: AIExplanationInput) -> None:
        """Çıktıdaki tüm metrik atıflarının girdide gerçekten var olduğunu doğrular.

        Bu, sağlayıcının (özellikle uzak bir modelin) simülasyonda hiç
        bulunmayan bir metriğe veya uydurma bir orana atıfta bulunmasını
        engeller.
        """

        allowed = ai_input.allowed_metric_ids
        for finding in (*self.metric_basis, *self.possible_explanations):
            unknown = [m for m in finding.metric_ids if m not in allowed]
            if unknown:
                raise AIExplanationValidationError(
                    f"Bulgu, girdide bulunmayan metric_id degerlerine atifta " f"bulunuyor: {unknown}"
                )

        assert_no_banned_ai_claims(self.short_summary)
        assert_no_banned_ai_claims(self.suggested_verification_experiment)
        assert_no_banned_ai_claims(self.limitations)
        for finding in (*self.metric_basis, *self.possible_explanations):
            assert_no_banned_ai_claims(finding.text)


# --- Sistem promptu (yalnızca uzak sağlayıcıya gönderilir) -----------------------

SYSTEM_PROMPT = f"""Sen Synthetix UX icin kalibre edilmemis, sentetik bir
simulasyon raporunu aciklayan bir asistansin. Kesinlikle uyman gereken
kurallar:

1. calibration_status HER ZAMAN "uncalibrated"dir; bunu asla degistirme
   veya gizleme.
2. Sadece sana verilen `metrics`, `critical_findings` ve `persona_segments`
   listelerindeki degerlere atifta bulun. Bu listede olmayan hicbir sayi,
   oran veya "metric_id" uydurma.
3. Her bulgunun (`metric_basis`, `possible_explanations`) bir veya daha
   fazla gercek `metric_id` referansi olmali.
4. Su ifadeleri ASLA kullanma: "gercek kullanici gordu/davrandi", kesin
   neden-sonuc iddialari, "kanitlandi", "gercek goz takibi", hukuki veya
   erisilebilirlik sertifikasi iddialari, "gercek pazar talebi", ve
   belirsizligi gizleyen/yok sayan ifadeler.
5. Sonuclar sentetik/tahmini bir heuristic modelden gelir; gercek
   kullanici verisi degildir. Bunu ima et, gizleme.
6. Prompt surumu: {PROMPT_VERSION}.
"""


# --- Sağlayıcı arayüzü --------------------------------------------------------------


class ExplainabilityProvider(Protocol):
    name: str
    model_name: str | None

    async def generate(self, ai_input: AIExplanationInput) -> AIExplanationOutput: ...


class BaseExplainabilityProvider(abc.ABC):
    name: str = "base"
    model_name: str | None = None

    @abc.abstractmethod
    async def generate(self, ai_input: AIExplanationInput) -> AIExplanationOutput: ...


# --- none/template sağlayıcı (varsayılan, anahtar gerektirmez) -------------------


def _pct(value: float) -> str:
    return f"%{round(value * 100)}"


class NoneProvider(BaseExplainabilityProvider):
    """Varsayılan, deterministik şablon sağlayıcısı.

    Hiçbir ağ çağrısı yapmaz; yalnızca girdideki metriklerden kural tabanlı
    (deterministik) bir açıklama üretir. `AI_PROVIDER` ayarlanmamışsa ya da
    uzak sağlayıcı kullanılamıyorsa ürün DAİMA bu sağlayıcıya döner.
    """

    name = "none"
    model_name = None

    async def generate(self, ai_input: AIExplanationInput) -> AIExplanationOutput:
        metrics_by_id = {m.metric_id: m for m in ai_input.metrics}

        summary_parts: list[str] = []
        metric_basis: list[AIExplanationFinding] = []

        completion = metrics_by_id.get("task_completion_probability")
        if completion is not None and completion.point_estimate is not None:
            summary_parts.append(
                f"Sentetik görev tamamlama tahmini {_pct(float(completion.point_estimate))}."
            )
            metric_basis.append(
                AIExplanationFinding(
                    text=(
                        f"Görev tamamlama olasılığı nokta tahmini "
                        f"{_pct(float(completion.point_estimate))} "
                        f"(belirsizlik aralığı {_pct(float(completion.low or 0))}–"
                        f"{_pct(float(completion.high or 0))})."
                    ),
                    metric_ids=["task_completion_probability"],
                )
            )

        misclick = metrics_by_id.get("misclick_probability")
        if misclick is not None and misclick.point_estimate is not None:
            metric_basis.append(
                AIExplanationFinding(
                    text=(
                        f"Yanlış tıklama olasılığı nokta tahmini " f"{_pct(float(misclick.point_estimate))}."
                    ),
                    metric_ids=["misclick_probability"],
                )
            )

        if not metric_basis:
            # Hicbir taninan metrik yoksa (beklenmeyen bos girdi), en azindan
            # tek bir genel bulgu uret; bos liste (min_length=1) asla ihlal
            # edilmez.
            fallback_id = next(iter(ai_input.allowed_metric_ids), None)
            if fallback_id is None:
                raise AIExplanationValidationError("Girdide atifta bulunulabilecek hicbir metric_id yok")
            metric_basis.append(
                AIExplanationFinding(
                    text="Bu rapor için raporlanmış metrik değerleri temel alınmıştır.",
                    metric_ids=[fallback_id],
                )
            )

        possible_explanations = [
            AIExplanationFinding(
                text=(
                    "Bu, kalibre edilmemiş bir heuristic modelin kural tabanlı "
                    "çıktısıdır; sayfa özellikleri (gezinme derinliği, form alan "
                    "sayısı, kontrast) ile ilişkilendirilmiştir. Gerçek kullanıcı "
                    "davranışını gözlemlemez."
                ),
                metric_ids=list(metrics_by_id.keys())[:3] or [next(iter(ai_input.allowed_metric_ids))],
            )
        ]

        short_summary = " ".join(summary_parts) or (
            "Bu rapor için sentetik, kalibre edilmemiş metrik özetleri mevcuttur."
        )

        return AIExplanationOutput(
            calibration_status=CALIBRATION_STATUS,
            short_summary=short_summary,
            metric_basis=metric_basis,
            possible_explanations=possible_explanations,
            suggested_verification_experiment=(
                "Bu sentetik bulguyu doğrulamak için küçük bir gerçek kullanıcı "
                "kullanılabilirlik testi (ör. 5-8 katılımcı, moderasyonlu görev "
                "tamamlama testi) planlayın; sentetik tahminle karşılaştırın."
            ),
            limitations=(
                "Bu açıklama, kalibre edilmemiş (uncalibrated) bir heuristic "
                "motorun deterministik şablon çıktısıdır; gerçek kullanıcı "
                "verisine dayanmaz ve nihai bir karar dayanağı olarak "
                "kullanılmamalıdır. Bkz. docs/methodology.md."
            ),
            prompt_version=PROMPT_VERSION,
            provider=self.name,
            model_name=self.model_name,
            generated_at=datetime.now(UTC),
        )


# --- İsteğe bağlı uzak sağlayıcı adaptörü (yalnızca env ile açılır) --------------


class RemoteHttpProvider(BaseExplainabilityProvider):
    """Genel, sağlayıcıdan bağımsız bir HTTP adaptörü.

    Yalnızca `AI_PROVIDER=remote` ve `AI_REMOTE_ENDPOINT` (+ isteğe bağlı
    `AI_REMOTE_API_KEY`) ortam değişkenleriyle açıkça yapılandırıldığında
    kullanılır. Uzak servise `AIExplanationInput.model_dump()` (yalnızca
    yapılandırılmış/anonim veri) ve `SYSTEM_PROMPT` gönderilir; ham HTML,
    cookie, token veya e-posta bu girdide zaten bulunmaz.

    Zaman aşımı, sınırlı yeniden deneme ve çıktı token sınırı burada
    uygulanır; her türlü hata `AIProviderUnavailableError` olarak
    yükseltilir ve çağıran taraf DAİMA `NoneProvider`'a güvenli şekilde
    döner (bkz. `generate_explanation`).
    """

    name = "remote"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str | None,
        model_name: str,
        timeout_seconds: int,
        max_retries: int,
        max_output_tokens: int,
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.max_output_tokens = max_output_tokens

    async def generate(self, ai_input: AIExplanationInput) -> AIExplanationOutput:
        # Toplam sure (tum yeniden denemeler dahil) tek bir "llm" kategorili
        # satirda olculur; basarisizlikta (`AIProviderUnavailableError`)
        # `log_duration` bunu ERROR+stack-trace olarak kaydedip yeniden
        # yukseltir - tekil deneme yeniden-deneme uyarilari (asagida) AYRI
        # ve daha ayrintili bir sinyal olarak kalir.
        with log_duration(
            _llm_logger,
            "llm",
            f"generate_explanation (model={self.model_name})",
            slow_threshold_ms=settings.slow_request_threshold_ms,
        ):
            return await self._generate(ai_input)

    async def _generate(self, ai_input: AIExplanationInput) -> AIExplanationOutput:
        payload = {
            "system_prompt": SYSTEM_PROMPT,
            "prompt_version": PROMPT_VERSION,
            "max_output_tokens": self.max_output_tokens,
            "input": ai_input.model_dump(mode="json"),
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: Exception | None = None
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(self.endpoint, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                return AIExplanationOutput(
                    **{
                        **data,
                        # Sağlayıcı ne dönerse dönsün audit alanları burada
                        # kesinlik kazanır (guvenlik: sahte provider/model
                        # adi enjekte edilemez).
                        "prompt_version": PROMPT_VERSION,
                        "provider": self.name,
                        "model_name": self.model_name,
                        "calibration_status": CALIBRATION_STATUS,
                        "generated_at": datetime.now(UTC),
                    }
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                logger.warning(
                    "ai_explanation.remote_provider.retry",
                    extra={"attempt": attempt, "error": type(exc).__name__},
                )
                continue
            except (httpx.HTTPStatusError, ValueError, TypeError) as exc:
                # Şema dışı / geçersiz yanıt: yeniden denemenin faydası yok.
                last_error = exc
                break

        raise AIProviderUnavailableError(f"Uzak AI saglayicisina ulasilamadi: {last_error}") from last_error


def get_provider() -> BaseExplainabilityProvider:
    """Yapılandırmaya göre aktif sağlayıcıyı döndürür.

    `settings.ai_provider` "remote" olsa bile uç nokta yapılandırılmamışsa
    (anahtar/uç nokta eksikse) güvenli şekilde `NoneProvider`'a düşer; hiçbir
    zaman yapılandırma eksikliği yüzünden istisna fırlatıp raporu bozmaz.
    """

    if settings.ai_provider == "remote" and settings.ai_remote_endpoint:
        return RemoteHttpProvider(
            endpoint=settings.ai_remote_endpoint,
            api_key=settings.ai_remote_api_key,
            model_name=settings.ai_remote_model_name,
            timeout_seconds=settings.ai_request_timeout_seconds,
            max_retries=settings.ai_max_retries,
            max_output_tokens=settings.ai_max_output_tokens,
        )
    return NoneProvider()


# --- Orkestrasyon: her zaman güvenli bir çıktı üretir ----------------------------


async def generate_explanation(
    ai_input: AIExplanationInput,
    *,
    provider: BaseExplainabilityProvider | None = None,
) -> tuple[AIExplanationOutput, dict]:
    """AI açıklamasını üretir; sağlayıcı ne olursa olsun ASLA istisna sızdırmaz.

    Dönüş: `(output, audit_record)`. `audit_record`, hiçbir gizli anahtar ya
    da ham hassas içerik taşımayan, yalnızca denetim amaçlı özet alanlarını
    (prompt sürümü, sağlayıcı/model adı, oluşturma zamanı, kaynak
    run/report kimliği, fallback uygulanıp uygulanmadığı) içerir.
    """

    active_provider = provider or get_provider()
    fallback_used = False
    fallback_reason: str | None = None

    output: AIExplanationOutput
    try:
        output = await active_provider.generate(ai_input)
        output.validate_against_input(ai_input)
    except (AIExplanationError, ValueError, TypeError) as exc:
        # Şema ihlali, yasak ifade veya sağlayıcı hatası: rapor ASLA
        # bozulmaz; güvenli deterministik şablona düşülür.
        logger.warning(
            "ai_explanation.provider_failed_falling_back_to_none",
            extra={"provider": active_provider.name, "error": str(exc)},
        )
        fallback_used = True
        fallback_reason = f"{type(exc).__name__}: {exc}"
        none_provider = NoneProvider()
        output = await none_provider.generate(ai_input)
        output.validate_against_input(ai_input)

    audit_record = {
        "prompt_version": output.prompt_version,
        "provider": output.provider,
        "model_name": output.model_name,
        "generated_at": output.generated_at.isoformat(),
        "report_id": ai_input.report_id,
        "simulation_run_id": ai_input.simulation_run_id,
        "calibration_status": output.calibration_status,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
    }
    return output, audit_record


# --- Rapordan (yalnızca yapılandırılmış alanlardan) girdi kurulumu --------------


_METRIC_FIELD_SPECS: tuple[tuple[str, str], ...] = (
    ("task_completion_probability", "probability"),
    ("misclick_probability", "probability"),
    ("abandonment_probability", "probability"),
)


def build_input_from_report(
    *,
    report_id: str,
    simulation_run_id: str,
    model_version: str,
    rules_version: str | None,
    fixture_version: str | None,
    methodology_reference: str,
    metrics: dict,
    critical_findings: list[dict],
    persona_segments: list[dict],
) -> AIExplanationInput:
    """Zaten hesaplanmış rapor alanlarından sıkı `AIExplanationInput` üretir.

    Yalnızca çağıranın açıkça geçirdiği (allowlisted) alanları okur; ham
    HTML, cookie, token veya kullanıcı e-postası gibi alanlar bu fonksiyonun
    imzasında hiç yer almaz, dolayısıyla kazara sızamaz.
    """

    snapshots: list[AIExplanationMetricSnapshot] = []
    for key, unit in _METRIC_FIELD_SPECS:
        value = metrics.get(key)
        if isinstance(value, dict):
            snapshots.append(
                AIExplanationMetricSnapshot(
                    metric_id=key,
                    point_estimate=value.get("point_estimate"),
                    low=value.get("low"),
                    high=value.get("high"),
                    unit=unit,
                )
            )

    duration = metrics.get("task_duration_seconds")
    if isinstance(duration, dict):
        snapshots.append(
            AIExplanationMetricSnapshot(
                metric_id="task_duration_seconds",
                point_estimate=duration.get("point_estimate"),
                low=duration.get("p10"),
                high=duration.get("p90"),
                unit="seconds",
            )
        )

    readability = metrics.get("readability_score")
    if readability is not None:
        snapshots.append(
            AIExplanationMetricSnapshot(
                metric_id="readability_score", point_estimate=readability, unit="score"
            )
        )

    contrast = metrics.get("contrast_check")
    if isinstance(contrast, dict):
        snapshots.append(
            AIExplanationMetricSnapshot(
                metric_id="contrast_check",
                point_estimate=contrast.get("avg_ratio"),
                unit="ratio",
            )
        )

    return AIExplanationInput(
        report_id=report_id,
        simulation_run_id=simulation_run_id,
        model_version=model_version,
        rules_version=rules_version,
        fixture_version=fixture_version,
        methodology_reference=methodology_reference,
        metrics=snapshots,
        critical_findings=[
            AIExplanationCriticalFinding(key=f["key"], severity=f["severity"], text=f["text"])
            for f in critical_findings
        ],
        persona_segments=[
            AIExplanationPersonaSegment(
                key=s["key"],
                label=s["label"],
                count=s["count"],
                share=s["share"],
                small_sample_warning=s["small_sample_warning"],
            )
            for s in persona_segments
        ],
    )


__all__ = [
    "AI_SECTION_UI_LABEL",
    "AI_SECTION_UI_SUBLABEL",
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "AIExplanationError",
    "AIExplanationValidationError",
    "AIProviderUnavailableError",
    "AIExplanationInput",
    "AIExplanationMetricSnapshot",
    "AIExplanationPersonaSegment",
    "AIExplanationCriticalFinding",
    "AIExplanationFinding",
    "AIExplanationOutput",
    "BaseExplainabilityProvider",
    "NoneProvider",
    "RemoteHttpProvider",
    "get_provider",
    "generate_explanation",
    "build_input_from_report",
    "assert_no_banned_ai_claims",
]
