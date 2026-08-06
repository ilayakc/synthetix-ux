"""Faz 3D.1: gercek OpenAI Responses API provider adaptoru testleri.

Bu dosyada GERCEK bir OpenAI API cagrisi YAPILMAZ - `AsyncOpenAI.responses.
parse` her zaman sahte/patch edilmis bir istemciyle degistirilir. Gercek bir
`OPENAI_API_KEY` gerekmez, ucret olusmaz.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings, settings
from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline.evidence import prepare_page_evidence
from app.services.ai_pipeline.mock_provider import MockAIProvider
from app.services.ai_pipeline.openai_pricing import estimate_cost_usd, get_model_pricing
from app.services.ai_pipeline.openai_provider import OpenAIProvider, _derive_safety_identifier
from app.services.ai_pipeline.provider import AIProvider, compute_configuration_fingerprint
from app.services.ai_pipeline.provider_errors import (
    AIProviderAuthenticationError,
    AIProviderInvalidOutputError,
    AIProviderInvalidRequestError,
    AIProviderPermissionError,
    AIProviderRateLimitError,
    AIProviderRefusalError,
    AIProviderServerError,
    AIProviderTimeoutError,
    AIProviderTransportError,
)
from app.services.ai_pipeline.schemas import PersonaBehaviorBatchOutput, ScenarioInterpretation, UXReport
from app.services.ai_pipeline.stage_hashing import provider_stage_execution_idempotency_key
from app.services.ai_pipeline.stage_inputs import ScenarioInterpretationInput

pytestmark = pytest.mark.unit


def _evidence():
    return prepare_page_evidence(
        source_type="sim", metrics={"task_completion_probability": {"point_estimate": 0.7}}
    )


def _scenario_input():
    return ScenarioInterpretationInput(
        evidence=_evidence(),
        target_task="Sepete ekle",
        test_name="t",
        test_description="d",
        methodology_context="m",
    )


def _provider(**overrides):
    kwargs = dict(
        api_key="sk-test-not-real",
        model="gpt-5.6-terra",
        reasoning_effort="medium",
        timeout_seconds=120,
        max_output_tokens=2000,
    )
    kwargs.update(overrides)
    return OpenAIProvider(**kwargs)


# =================================================================================
# 1-7: CONFIG / SECRET
# =================================================================================


def test_default_settings_disable_provider_readiness():
    cfg = Settings()
    assert cfg.ai_report_enabled is False
    assert cfg.ai_report_provider_ready is False


def test_enabled_openai_with_key_is_ready():
    cfg = Settings(
        ai_report_enabled=True, ai_report_provider="openai", openai_api_key=SecretStr("sk-real-looking-key")
    )
    assert cfg.ai_report_provider_ready is True


def test_enabled_without_key_is_not_ready():
    cfg = Settings(ai_report_enabled=True, ai_report_provider="openai", openai_api_key=None)
    assert cfg.ai_report_provider_ready is False


def test_api_key_never_leaks_in_repr_or_str():
    cfg = Settings(
        ai_report_enabled=True, ai_report_provider="openai", openai_api_key=SecretStr("super-secret-value")
    )
    assert "super-secret-value" not in repr(cfg.openai_api_key)
    assert "super-secret-value" not in str(cfg.openai_api_key)
    assert "super-secret-value" not in repr(cfg)


def test_blank_model_is_not_ready():
    cfg = Settings(
        ai_report_enabled=True,
        ai_report_provider="openai",
        openai_api_key=SecretStr("sk-x"),
        openai_model="   ",
    )
    assert cfg.ai_report_provider_ready is False


def test_non_positive_timeout_rejected_by_validation():
    with pytest.raises(ValidationError):
        Settings(openai_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(openai_timeout_seconds=-5)


def test_disabled_provider_choice_never_ready_even_with_key():
    cfg = Settings(ai_report_enabled=True, ai_report_provider="disabled", openai_api_key=SecretStr("sk-x"))
    assert cfg.ai_report_provider_ready is False


# =================================================================================
# 8-19: PROTOCOL / FINGERPRINT
# =================================================================================


def test_openai_provider_implements_ai_provider_protocol():
    provider = _provider()
    assert isinstance(provider, AIProvider)
    assert provider.provider_name == "openai"
    assert provider.model_name == "gpt-5.6-terra"
    assert provider.is_mock is False


def test_fingerprint_deterministic_for_same_config():
    a = _provider()
    b = _provider()
    assert a.configuration_fingerprint == b.configuration_fingerprint


def test_fingerprint_changes_with_model():
    a = _provider(model="gpt-5.6-terra")
    b = _provider(model="gpt-5.7-terra")
    assert a.configuration_fingerprint != b.configuration_fingerprint


def test_fingerprint_changes_with_reasoning_effort():
    a = _provider(reasoning_effort="medium")
    b = _provider(reasoning_effort="high")
    assert a.configuration_fingerprint != b.configuration_fingerprint


def test_fingerprint_unaffected_by_timeout():
    a = _provider(timeout_seconds=120)
    b = _provider(timeout_seconds=60)
    assert a.configuration_fingerprint == b.configuration_fingerprint


def test_fingerprint_unaffected_by_api_key():
    a = _provider(api_key="sk-one")
    b = _provider(api_key="sk-two-totally-different")
    assert a.configuration_fingerprint == b.configuration_fingerprint


def test_mock_provider_fingerprint_deterministic():
    assert MockAIProvider().configuration_fingerprint == MockAIProvider().configuration_fingerprint


def test_compute_configuration_fingerprint_excludes_volatile_fields():
    # Fonksiyon imzasi API key/timeout/pid/timestamp KABUL ETMEZ - yalnizca
    # semantik alanlari kabul eder (statik sozlesme kontrolu).
    fp = compute_configuration_fingerprint(
        provider_name="openai", model_name="m", reasoning_effort="medium", structured_output_mode="v1"
    )
    assert isinstance(fp, str) and len(fp) == 64


def test_execution_key_changes_when_configuration_fingerprint_changes():
    run_id = uuid.uuid4()
    common = dict(
        simulation_run_id=run_id,
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt_version="v1",
        prompt_hash="a" * 64,
        provider="openai",
        model_name="gpt-5.6-terra",
        input_hash="b" * 64,
    )
    key_a = provider_stage_execution_idempotency_key(
        **common, provider_configuration_fingerprint="fingerprint-a"
    )
    key_b = provider_stage_execution_idempotency_key(
        **common, provider_configuration_fingerprint="fingerprint-b"
    )
    assert key_a != key_b


def test_execution_key_stable_for_same_fingerprint():
    run_id = uuid.uuid4()
    common = dict(
        simulation_run_id=run_id,
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt_version="v1",
        prompt_hash="a" * 64,
        provider="openai",
        model_name="gpt-5.6-terra",
        input_hash="b" * 64,
        provider_configuration_fingerprint="same-fp",
    )
    assert provider_stage_execution_idempotency_key(**common) == provider_stage_execution_idempotency_key(
        **common
    )


# =================================================================================
# REQUEST CONSTRUCTION (sahte AsyncOpenAI client - network YOK)
# =================================================================================


class _FakeContent:
    def __init__(self, *, type_: str, parsed=None):
        self.type = type_
        self.parsed = parsed


class _FakeOutputItem:
    def __init__(self, *, type_: str = "message", content=()):
        self.type = type_
        self.content = content


class _FakeUsage:
    def __init__(self, *, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, *, status="completed", output=(), output_parsed=None, usage=None, id_="resp_1"):
        self.status = status
        self.output = output
        self.output_parsed = output_parsed
        self.usage = usage
        self.id = id_


class _FakeResponsesResource:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._response


class _FakeAsyncClient:
    def __init__(self, response=None, exc=None):
        self.responses = _FakeResponsesResource(response=response, exc=exc)
        self.closed = False

    async def close(self):
        self.closed = True


def _install_fake_client(provider: OpenAIProvider, fake_client: _FakeAsyncClient) -> None:
    provider._client = fake_client  # type: ignore[attr-defined]


async def _valid_scenario() -> ScenarioInterpretation:
    # Karmasik cross-field validasyonlu ScenarioInterpretation'i elle kurmak
    # yerine, zaten dogrulanmis MockAIProvider ciktisini yeniden kullanir.
    result = await MockAIProvider().generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=SimpleNamespace(),
        input_payload=_scenario_input(),
        output_schema=ScenarioInterpretation,
    )
    return result.output


async def _successful_scenario_response():
    scenario = await _valid_scenario()
    return _FakeResponse(
        status="completed",
        output=[_FakeOutputItem(content=[_FakeContent(type_="output_text", parsed=scenario)])],
        output_parsed=scenario,
        usage=_FakeUsage(input_tokens=42, output_tokens=17),
    )


async def test_request_uses_responses_parse_with_expected_kwargs():
    from app.services.ai_pipeline.prompts import get_prompt

    provider = _provider()
    fake = _FakeAsyncClient(response=await _successful_scenario_response())
    _install_fake_client(provider, fake)

    prompt = get_prompt(AIPipelineStageType.SCENARIO_INTERPRETATION)
    result = await provider.generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=prompt,
        input_payload=_scenario_input(),
        output_schema=ScenarioInterpretation,
    )

    assert len(fake.responses.calls) == 1
    call = fake.responses.calls[0]
    assert call["model"] == "gpt-5.6-terra"
    assert call["text_format"] is ScenarioInterpretation
    assert call["store"] is False
    assert call["reasoning"] == {"effort": "medium"}
    assert call["max_output_tokens"] == 2000
    assert "tools" not in call
    assert "stream" not in call
    assert "previous_response_id" not in call
    assert isinstance(call["safety_identifier"], str) and len(call["safety_identifier"]) == 64
    assert call["input"][0]["role"] == "system"
    assert call["input"][1]["role"] == "user"

    assert isinstance(result.output, ScenarioInterpretation)
    assert result.provider_name == "openai"
    assert result.model_name == "gpt-5.6-terra"
    assert result.is_mock is False
    assert result.input_tokens == 42
    assert result.output_tokens == 17
    assert result.configuration_fingerprint == provider.configuration_fingerprint
    assert result.provider_request_id == "resp_1"


async def test_safety_identifier_stable_for_same_run_differs_across_runs():
    id_a1 = _derive_safety_identifier(_scenario_input())
    id_a2 = _derive_safety_identifier(_scenario_input())
    assert id_a1 == id_a2

    other_evidence_input = ScenarioInterpretationInput(
        evidence=prepare_page_evidence(
            source_type="sim", metrics={"task_completion_probability": {"point_estimate": 0.99}}
        ),
        target_task="Sepete ekle",
        test_name="t",
        test_description="d",
        methodology_context="m",
    )
    id_b = _derive_safety_identifier(other_evidence_input)
    assert id_b != id_a1


async def test_estimated_cost_uses_decimal_pricing_table():
    provider = _provider()
    fake = _FakeAsyncClient(response=await _successful_scenario_response())
    _install_fake_client(provider, fake)
    prompt_result = await provider.generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
        input_payload=_scenario_input(),
        output_schema=ScenarioInterpretation,
    )
    expected = estimate_cost_usd(model="gpt-5.6-terra", input_tokens=42, output_tokens=17)
    assert prompt_result.estimated_cost == pytest.approx(float(expected))


def test_unknown_model_pricing_returns_none_not_fabricated():
    assert get_model_pricing("some-unknown-model") is None
    assert estimate_cost_usd(model="some-unknown-model", input_tokens=100, output_tokens=100) is None


# =================================================================================
# SUCCESS / STRUCTURED OUTPUT PARSE (persona/ux report ayrica)
# =================================================================================


async def test_persona_behavior_output_parsed_correctly():
    from app.services.ai_pipeline import batching as batching_module
    from app.services.ai_pipeline.schemas import PersonaContext
    from app.services.ai_pipeline.stage_inputs import PersonaBehaviorBatchInput

    persona = PersonaContext(persona_id=uuid.uuid4(), index=0, label="P0", attributes={}, population_weight=1)
    (batch,) = batching_module.build_persona_batches((persona,), batch_size=15)
    scenario = await _valid_scenario()
    behavior_input = PersonaBehaviorBatchInput(batch=batch, evidence=_evidence(), scenario=scenario)

    # Zaten dogrulanmis Mock ciktisini yeniden kullanir (karmasik cross-field
    # validasyonu elle tekrar yazmak yerine).
    mock_result = await MockAIProvider().generate_structured(
        stage_type=AIPipelineStageType.PERSONA_BEHAVIOR,
        batch_index=0,
        prompt=SimpleNamespace(),
        input_payload=behavior_input,
        output_schema=PersonaBehaviorBatchOutput,
    )
    output = mock_result.output

    provider = _provider()
    fake = _FakeAsyncClient(
        response=_FakeResponse(
            status="completed",
            output=[_FakeOutputItem(content=[_FakeContent(type_="output_text", parsed=output)])],
            output_parsed=output,
            usage=_FakeUsage(input_tokens=1, output_tokens=1),
        )
    )
    _install_fake_client(provider, fake)
    result = await provider.generate_structured(
        stage_type=AIPipelineStageType.PERSONA_BEHAVIOR,
        batch_index=0,
        prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
        input_payload=behavior_input,
        output_schema=PersonaBehaviorBatchOutput,
    )
    assert result.output is output


async def test_ux_report_output_parsed_correctly():
    from app.services.ai_pipeline import aggregation as aggregation_module
    from app.services.ai_pipeline import batching as batching_module
    from app.services.ai_pipeline.schemas import PersonaContext
    from app.services.ai_pipeline.stage_inputs import PersonaBehaviorBatchInput, UXReportInput

    persona = PersonaContext(persona_id=uuid.uuid4(), index=0, label="P0", attributes={}, population_weight=1)
    (batch,) = batching_module.build_persona_batches((persona,), batch_size=15)
    scenario = await _valid_scenario()
    behavior_input = PersonaBehaviorBatchInput(batch=batch, evidence=_evidence(), scenario=scenario)
    behavior_result = await MockAIProvider().generate_structured(
        stage_type=AIPipelineStageType.PERSONA_BEHAVIOR,
        batch_index=0,
        prompt=SimpleNamespace(),
        input_payload=behavior_input,
        output_schema=PersonaBehaviorBatchOutput,
    )
    aggregation = aggregation_module.aggregate_persona_behavior(
        personas=(persona,), behavior_results=behavior_result.output.persona_results, scenario=scenario
    )
    report_input = UXReportInput(evidence=_evidence(), aggregation=aggregation, methodology_context="m")
    report_result = await MockAIProvider().generate_structured(
        stage_type=AIPipelineStageType.UX_REPORT,
        batch_index=None,
        prompt=SimpleNamespace(),
        input_payload=report_input,
        output_schema=UXReport,
    )
    report = report_result.output

    provider = _provider()
    fake = _FakeAsyncClient(
        response=_FakeResponse(
            status="completed",
            output=[_FakeOutputItem(content=[_FakeContent(type_="output_text", parsed=report)])],
            output_parsed=report,
            usage=_FakeUsage(input_tokens=2, output_tokens=2),
        )
    )
    _install_fake_client(provider, fake)
    result = await provider.generate_structured(
        stage_type=AIPipelineStageType.UX_REPORT,
        batch_index=None,
        prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
        input_payload=report_input,
        output_schema=UXReport,
    )
    assert result.output is report


# =================================================================================
# ERRORS / REFUSAL / INCOMPLETE
# =================================================================================


async def _call_provider_expecting_error(fake_exc=None, fake_response=None):
    provider = _provider()
    fake = _FakeAsyncClient(response=fake_response, exc=fake_exc)
    _install_fake_client(provider, fake)
    return provider, fake


async def test_refusal_raises_typed_non_retryable_error():
    provider, fake = await _call_provider_expecting_error(
        fake_response=_FakeResponse(
            status="completed",
            output=[_FakeOutputItem(content=[_FakeContent(type_="refusal", parsed=None)])],
            output_parsed=None,
        )
    )
    with pytest.raises(AIProviderRefusalError) as exc_info:
        await provider.generate_structured(
            stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
            batch_index=None,
            prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
            input_payload=_scenario_input(),
            output_schema=ScenarioInterpretation,
        )
    assert exc_info.value.retryable is False


async def test_parsed_none_raises_invalid_output_error():
    provider, fake = await _call_provider_expecting_error(
        fake_response=_FakeResponse(status="completed", output=[], output_parsed=None)
    )
    with pytest.raises(AIProviderInvalidOutputError):
        await provider.generate_structured(
            stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
            batch_index=None,
            prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
            input_payload=_scenario_input(),
            output_schema=ScenarioInterpretation,
        )


async def test_incomplete_status_raises_invalid_output_error():
    provider, fake = await _call_provider_expecting_error(
        fake_response=_FakeResponse(status="incomplete", output=[], output_parsed=None)
    )
    with pytest.raises(AIProviderInvalidOutputError):
        await provider.generate_structured(
            stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
            batch_index=None,
            prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
            input_payload=_scenario_input(),
            output_schema=ScenarioInterpretation,
        )


def _fake_httpx_response(status_code: int):
    import httpx

    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return httpx.Response(status_code=status_code, request=request, json={"error": {"message": "x"}})


@pytest.mark.parametrize(
    ("exc_cls_name", "status_code", "expected_error", "expected_retryable"),
    [
        ("RateLimitError", 429, AIProviderRateLimitError, True),
        ("AuthenticationError", 401, AIProviderAuthenticationError, False),
        ("PermissionDeniedError", 403, AIProviderPermissionError, False),
        ("BadRequestError", 400, AIProviderInvalidRequestError, False),
        ("NotFoundError", 404, AIProviderInvalidRequestError, False),
        ("InternalServerError", 500, AIProviderServerError, True),
    ],
)
async def test_openai_status_errors_mapped_to_typed_domain_errors(
    exc_cls_name, status_code, expected_error, expected_retryable
):
    import openai

    exc_cls = getattr(openai, exc_cls_name)
    exc = exc_cls("boom", response=_fake_httpx_response(status_code), body=None)
    provider, fake = await _call_provider_expecting_error(fake_exc=exc)
    with pytest.raises(expected_error) as exc_info:
        await provider.generate_structured(
            stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
            batch_index=None,
            prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
            input_payload=_scenario_input(),
            output_schema=ScenarioInterpretation,
        )
    assert exc_info.value.retryable is expected_retryable
    assert "boom" not in str(exc_info.value)


async def test_openai_timeout_error_mapped_retryable():
    import httpx
    import openai

    exc = openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    provider, fake = await _call_provider_expecting_error(fake_exc=exc)
    with pytest.raises(AIProviderTimeoutError) as exc_info:
        await provider.generate_structured(
            stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
            batch_index=None,
            prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
            input_payload=_scenario_input(),
            output_schema=ScenarioInterpretation,
        )
    assert exc_info.value.retryable is True


async def test_openai_connection_error_mapped_retryable():
    import httpx
    import openai

    exc = openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    provider, fake = await _call_provider_expecting_error(fake_exc=exc)
    with pytest.raises(AIProviderTransportError) as exc_info:
        await provider.generate_structured(
            stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
            batch_index=None,
            prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
            input_payload=_scenario_input(),
            output_schema=ScenarioInterpretation,
        )
    assert exc_info.value.retryable is True


# =================================================================================
# LIFECYCLE (startup/shutdown ctx injection) - network YOK
# =================================================================================


async def test_startup_injects_provider_only_when_ready(monkeypatch):
    from app import worker as app_worker
    from app.services.ai_pipeline.scheduling import AI_PIPELINE_PROVIDER_CTX_KEY

    monkeypatch.setattr(settings, "ai_report_enabled", True)
    monkeypatch.setattr(settings, "ai_report_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("sk-fake-not-real"))
    monkeypatch.setattr(app_worker, "validate_production_secrets", lambda: None)

    ctx: dict = {}
    await app_worker.on_startup(ctx)
    provider = ctx[AI_PIPELINE_PROVIDER_CTX_KEY]
    assert isinstance(provider, OpenAIProvider)
    assert provider.model_name == settings.openai_model

    await app_worker.on_shutdown(ctx)


async def test_startup_does_not_inject_provider_when_disabled(monkeypatch):
    from app import worker as app_worker
    from app.services.ai_pipeline.scheduling import AI_PIPELINE_PROVIDER_CTX_KEY

    monkeypatch.setattr(settings, "ai_report_enabled", False)
    monkeypatch.setattr(app_worker, "validate_production_secrets", lambda: None)

    ctx: dict = {}
    await app_worker.on_startup(ctx)
    assert ctx[AI_PIPELINE_PROVIDER_CTX_KEY] is None

    await app_worker.on_shutdown(ctx)  # no-op, provider yok


async def test_shutdown_closes_openai_client(monkeypatch):
    from app import worker as app_worker
    from app.services.ai_pipeline.scheduling import AI_PIPELINE_PROVIDER_CTX_KEY

    provider = _provider()
    fake = _FakeAsyncClient()
    _install_fake_client(provider, fake)
    ctx = {AI_PIPELINE_PROVIDER_CTX_KEY: provider}
    await app_worker.on_shutdown(ctx)
    assert fake.closed is True


def test_no_network_import_side_effects():
    import inspect

    from app.services.ai_pipeline import openai_provider as module

    source = inspect.getsource(module)
    for forbidden in ("os.environ", "os.getenv"):
        assert forbidden not in source
