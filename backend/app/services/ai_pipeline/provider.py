"""AIProvider sozlesmesi + ProviderResult + stage-sozlesme dogrulamasi (Faz 2B).

`AIProvider`, gercek (uzak LLM) veya `MockAIProvider` gibi deterministik bir
saglayicinin uymasi gereken PROTOKOL'dur. `ProviderResult` ise saglayicidan
donen tek/guvenli tasima nesnesidir - ham prompt/istek/cevap TASIMAZ.

Onemli: Gercek provider hatasinda pipeline COKER (asla sessizce Mock'a
dusmez); bu, `ai_explanation.py`nin `NoneProvider` fallback davranisinin
KASITLI olarak TERSIDIR (bkz. gorev talimati).
"""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline.hashing import PromptDescriptor, hash_payload
from app.services.ai_pipeline.provider_errors import (
    AIProviderError,
    AIProviderInvalidOutputError,
    AIProviderUnsupportedStageError,
)
from app.services.ai_pipeline.schemas import (
    PersonaBehaviorBatchOutput,
    ScenarioInterpretation,
    UXReport,
)
from app.services.ai_pipeline.stage_inputs import (
    PersonaBehaviorBatchInput,
    ScenarioInterpretationInput,
    UXReportInput,
)

OutputT = TypeVar("OutputT", bound=BaseModel)

# LLM/provider cagrisi YAPILAN asamalar - saf Python asamalari (Stage 1/2/5)
# bu kumeye DAHIL DEGILDIR ve provider'a hicbir zaman ulasmaz.
SUPPORTED_PROVIDER_STAGE_TYPES: frozenset[AIPipelineStageType] = frozenset(
    {
        AIPipelineStageType.SCENARIO_INTERPRETATION,
        AIPipelineStageType.PERSONA_BEHAVIOR,
        AIPipelineStageType.UX_REPORT,
    }
)

_STAGE_INPUT_TYPES: dict[AIPipelineStageType, type[BaseModel]] = {
    AIPipelineStageType.SCENARIO_INTERPRETATION: ScenarioInterpretationInput,
    AIPipelineStageType.PERSONA_BEHAVIOR: PersonaBehaviorBatchInput,
    AIPipelineStageType.UX_REPORT: UXReportInput,
}
_STAGE_OUTPUT_TYPES: dict[AIPipelineStageType, type[BaseModel]] = {
    AIPipelineStageType.SCENARIO_INTERPRETATION: ScenarioInterpretation,
    AIPipelineStageType.PERSONA_BEHAVIOR: PersonaBehaviorBatchOutput,
    AIPipelineStageType.UX_REPORT: UXReport,
}


def validate_stage_contract(
    *,
    stage_type: AIPipelineStageType,
    input_payload: BaseModel,
    output_schema: type[BaseModel],
) -> None:
    """stage_type desteklenmiyorsa / input_payload beklenen tipte degilse /
    output_schema beklenen tipte degilse `AIProviderError` alt sinifi firlatir."""

    if stage_type not in SUPPORTED_PROVIDER_STAGE_TYPES:
        raise AIProviderUnsupportedStageError(f"desteklenmeyen stage_type: {stage_type.value}")
    expected_input = _STAGE_INPUT_TYPES[stage_type]
    if not isinstance(input_payload, expected_input):
        raise AIProviderError(
            "invalid_input_payload",
            f"{stage_type.value} icin beklenen input tipi {expected_input.__name__}",
        )
    expected_output = _STAGE_OUTPUT_TYPES[stage_type]
    if output_schema is not expected_output:
        raise AIProviderInvalidOutputError(
            f"{stage_type.value} icin beklenen output_schema {expected_output.__name__}"
        )


class ProviderResult(BaseModel, Generic[OutputT]):
    """Provider'dan donen tek, guvenli tasima nesnesi.

    ASLA tasimaz: ham prompt, ham istek govdesi, ham provider cevabi, API
    anahtari/header, system instructions, HTML/DOM, stack trace. Yalnizca
    dogrulanmis `output` + kisa metadata.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    output: OutputT
    provider_name: str = Field(min_length=1, max_length=50)
    model_name: str = Field(min_length=1, max_length=200)
    is_mock: bool
    # Provider'in SEMANTIK yapilandirma kimligi (model + reasoning effort +
    # structured-output modu/surumu + semantik uretim ayarlari) - API key/
    # timeout/connection-pool/secret/pid/timestamp/rastgele deger ASLA
    # icermez (bkz. AIProvider.configuration_fingerprint dokstring'i). Retry
    # sirasinda ayni pinlenmis degerle birebir eslesmelidir.
    configuration_fingerprint: str = Field(default="unspecified", min_length=1, max_length=64)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost: float | None = Field(default=None, ge=0.0)
    request_duration_ms: int = Field(ge=0)
    provider_request_id: str | None = Field(default=None, max_length=200)


def compute_configuration_fingerprint(
    *,
    provider_name: str,
    model_name: str,
    reasoning_effort: str,
    structured_output_mode: str,
    semantic_settings: dict[str, object] | None = None,
) -> str:
    """Bir provider'in SEMANTIK yapilandirma kimligini (Faz 3D.1) deterministik,
    canonical bir SHA-256 hex digest'e (64 karakter) cevirir.

    Kapsanan alanlar: provider adi, model, reasoning effort, structured-output
    modu/surumu, semantik uretim ayarlari (ornegin `temperature`). ASLA
    kapsanmaz (bkz. gorev talimati madde 5): API key, timeout, connection
    pool, secret, process id, timestamp, rastgele deger - bu fonksiyona bu
    alanlar hicbir zaman parametre olarak GECIRILMEMELIDIR.
    """

    return hash_payload(
        {
            "provider_name": provider_name,
            "model_name": model_name,
            "reasoning_effort": reasoning_effort,
            "structured_output_mode": structured_output_mode,
            "semantic_settings": semantic_settings or {},
        }
    )


@runtime_checkable
class AIProvider(Protocol):
    """Bir AI saglayicisinin uymasi gereken sozlesme.

    Gercek bir uzak-LLM adaptoru veya `MockAIProvider` bu protokolu uygular.
    `generate_structured` daima `ProviderResult[OutputT]` dondurur veya bir
    `AIProviderError` alt sinifi firlatir - asla sessizce yari/eksik sonuc
    veya None dondurmez.

    `configuration_fingerprint` (Faz 3D.1): provider olusturulurken BIR KEZ
    hesaplanan, deterministik semantik yapilandirma kimligi (bkz.
    `compute_configuration_fingerprint`) - retry sirasinda ayni provider/model
    ile CAGRILAN her istek icin AYNI kalir; model/reasoning effort/structured-
    output modu degisirse degisir. API key/timeout/secret/pid/timestamp/
    rastgele deger ASLA icermez.
    """

    provider_name: str
    model_name: str
    is_mock: bool
    configuration_fingerprint: str

    async def generate_structured(
        self,
        *,
        stage_type: AIPipelineStageType,
        batch_index: int | None,
        prompt: PromptDescriptor,
        input_payload: BaseModel,
        output_schema: type[OutputT],
    ) -> ProviderResult[OutputT]: ...


__all__ = [
    "OutputT",
    "SUPPORTED_PROVIDER_STAGE_TYPES",
    "validate_stage_contract",
    "ProviderResult",
    "AIProvider",
    "compute_configuration_fingerprint",
]
