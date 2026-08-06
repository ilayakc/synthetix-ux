"""Faz 3D.2-LOCAL: yerel Ollama provider adaptoru testleri.

Bu dosyada GERCEK bir Ollama daemon'ina/network'e ASLA baglanilmaz -
`httpx.AsyncClient` her zaman `httpx.MockTransport` ile degistirilir. Kurulu
bir Ollama gerekmez, hicbir ucret olusmaz.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline.evidence import prepare_page_evidence
from app.services.ai_pipeline.mock_provider import MockAIProvider
from app.services.ai_pipeline.ollama_provider import OllamaProvider, _inline_schema_refs
from app.services.ai_pipeline.provider import AIProvider, compute_configuration_fingerprint
from app.services.ai_pipeline.provider_errors import (
    AIProviderConfigurationError,
    AIProviderInvalidOutputError,
    AIProviderInvalidRequestError,
    AIProviderServerError,
    AIProviderTimeoutError,
    AIProviderTransportError,
)
from app.services.ai_pipeline.schemas import PersonaBehaviorBatchOutput, ScenarioInterpretation, UXReport
from app.services.ai_pipeline.stage_inputs import ScenarioInterpretationInput
from app.worker_constants import ARQ_JOB_TIMEOUT_SECONDS, MIN_JOB_TIMEOUT_SAFETY_MARGIN_SECONDS

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


def _provider(**overrides) -> OllamaProvider:
    kwargs = dict(
        base_url="http://127.0.0.1:11434",
        model="qwen3:8b",
        timeout_seconds=600,
        temperature=0.0,
        keep_alive="10m",
        num_ctx=None,
        max_output_tokens=2000,
        max_concurrency=1,
        allow_remote_host=False,
    )
    kwargs.update(overrides)
    return OllamaProvider(**kwargs)


def _install_transport(provider: OllamaProvider, handler) -> None:
    provider._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        follow_redirects=False,
    )


def _success_response(*, content: str, prompt_eval_count: int = 42, eval_count: int = 17) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "qwen3:8b",
            "done": True,
            "message": {"role": "assistant", "content": content},
            "prompt_eval_count": prompt_eval_count,
            "eval_count": eval_count,
            "total_duration": 123,
            "load_duration": 1,
        },
    )


async def _valid_scenario() -> ScenarioInterpretation:
    result = await MockAIProvider().generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=SimpleNamespace(),
        input_payload=_scenario_input(),
        output_schema=ScenarioInterpretation,
    )
    return result.output


# =================================================================================
# CONFIG / READINESS / SSRF
# =================================================================================


def test_default_settings_disable_provider_readiness():
    cfg = Settings()
    assert cfg.ai_report_provider_ready is False


def test_enabled_ollama_with_defaults_is_ready():
    cfg = Settings(ai_report_enabled=True, ai_report_provider="ollama")
    assert cfg.ai_report_provider_ready is True


def test_ollama_blank_model_is_not_ready():
    cfg = Settings(ai_report_enabled=True, ai_report_provider="ollama", ollama_model="   ")
    assert cfg.ai_report_provider_ready is False


def test_ollama_non_positive_timeout_rejected_by_validation():
    with pytest.raises(ValidationError):
        Settings(ollama_timeout_seconds=0)


def test_ollama_non_positive_concurrency_rejected_by_validation():
    with pytest.raises(ValidationError):
        Settings(ollama_max_concurrency=0)


def test_ollama_non_positive_num_ctx_rejected_by_validation():
    with pytest.raises(ValidationError):
        Settings(ollama_num_ctx=0)


def test_ollama_remote_host_rejected_by_default():
    cfg = Settings(
        ai_report_enabled=True, ai_report_provider="ollama", ollama_base_url="http://example.com:11434"
    )
    assert cfg.ai_report_provider_ready is False


def test_ollama_remote_host_accepted_when_explicitly_allowed():
    cfg = Settings(
        ai_report_enabled=True,
        ai_report_provider="ollama",
        ollama_base_url="http://example.com:11434",
        ollama_allow_remote_host=True,
    )
    assert cfg.ai_report_provider_ready is True


def test_ollama_loopback_variants_accepted():
    for host in ("http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"):
        cfg = Settings(ai_report_enabled=True, ai_report_provider="ollama", ollama_base_url=host)
        assert cfg.ai_report_provider_ready is True, host


def test_mock_provider_choice_ready_without_secrets():
    cfg = Settings(ai_report_enabled=True, ai_report_provider="mock")
    assert cfg.ai_report_provider_ready is True


def test_disabled_provider_choice_never_ready():
    cfg = Settings(ai_report_enabled=True, ai_report_provider="disabled")
    assert cfg.ai_report_provider_ready is False


def test_provider_construction_rejects_disallowed_host():
    with pytest.raises(AIProviderConfigurationError):
        _provider(base_url="http://example.com:11434", allow_remote_host=False)


def test_provider_construction_accepts_loopback():
    provider = _provider(base_url="http://127.0.0.1:11434")
    assert provider.provider_name == "ollama"


# =================================================================================
# PROTOCOL / FINGERPRINT
# =================================================================================


def test_ollama_provider_implements_ai_provider_protocol():
    provider = _provider()
    assert isinstance(provider, AIProvider)
    assert provider.model_name == "qwen3:8b"
    assert provider.is_mock is False


def test_fingerprint_deterministic_for_same_config():
    a = _provider()
    b = _provider()
    assert a.configuration_fingerprint == b.configuration_fingerprint


def test_fingerprint_changes_with_model():
    a = _provider(model="qwen3:8b")
    b = _provider(model="qwen3:14b")
    assert a.configuration_fingerprint != b.configuration_fingerprint


def test_fingerprint_changes_with_temperature():
    a = _provider(temperature=0.0)
    b = _provider(temperature=0.5)
    assert a.configuration_fingerprint != b.configuration_fingerprint


def test_fingerprint_changes_with_num_ctx():
    a = _provider(num_ctx=None)
    b = _provider(num_ctx=4096)
    assert a.configuration_fingerprint != b.configuration_fingerprint


def test_fingerprint_unaffected_by_timeout():
    a = _provider(timeout_seconds=600)
    b = _provider(timeout_seconds=60)
    assert a.configuration_fingerprint == b.configuration_fingerprint


def test_fingerprint_unaffected_by_keep_alive():
    a = _provider(keep_alive="10m")
    b = _provider(keep_alive="1h")
    assert a.configuration_fingerprint == b.configuration_fingerprint


def test_fingerprint_unaffected_by_max_concurrency():
    a = _provider(max_concurrency=1)
    b = _provider(max_concurrency=4)
    assert a.configuration_fingerprint == b.configuration_fingerprint


def test_compute_configuration_fingerprint_excludes_volatile_fields():
    fp = compute_configuration_fingerprint(
        provider_name="ollama", model_name="m", reasoning_effort="n/a", structured_output_mode="v1"
    )
    assert isinstance(fp, str) and len(fp) == 64


# =================================================================================
# REQUEST CONSTRUCTION
# =================================================================================


async def test_request_uses_expected_chat_payload_json_mode():
    """Varsayilan mod ("json"): `format="json"` gonderilir, sema PROMPT
    metninde acikca (kanonik JSON olarak) verilir - bkz. Faz 3D.3A.2."""

    scenario = await _valid_scenario()
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return _success_response(content=scenario.model_dump_json())

    provider = _provider()
    assert provider._structured_output_mode == "json"  # type: ignore[attr-defined]
    _install_transport(provider, handler)

    prompt = SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1")
    result = await provider.generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=prompt,
        input_payload=_scenario_input(),
        output_schema=ScenarioInterpretation,
    )

    assert captured["url"].endswith("/api/chat")
    body = captured["body"]
    assert body["model"] == "qwen3:8b"
    assert body["stream"] is False
    # "json" modunda `format` HER ZAMAN sabit "json" dizgesidir - Ollama'ya
    # sema hic GONDERILMEZ; sema yalnizca prompt METNINDE bulunur.
    assert body["format"] == "json"
    assert body["options"]["temperature"] == 0.0
    assert body["options"]["num_predict"] == 2000
    assert body["keep_alive"] == "10m"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert "num_ctx" not in body["options"]

    system_content = body["messages"][0]["content"]
    assert "BEGIN SCHEMA CONTRACT" in system_content
    assert "END SCHEMA CONTRACT" in system_content
    # Kanonik/deterministik serialize edilmis sema metni promptun icinde
    # gercekten mevcut olmali (model, alan adlarini/kisitlari BURADAN okur).
    canonical_schema = json.dumps(
        ScenarioInterpretation.model_json_schema(), sort_keys=True, ensure_ascii=False
    )
    assert canonical_schema in system_content

    assert isinstance(result.output, ScenarioInterpretation)
    assert result.provider_name == "ollama"
    assert result.input_tokens == 42
    assert result.output_tokens == 17
    assert result.estimated_cost == 0.0
    assert result.provider_request_id is None
    assert result.configuration_fingerprint == provider.configuration_fingerprint


async def test_request_uses_expected_chat_payload_json_schema_mode():
    """`json_schema` modu (varsayilan DEGIL, acikca secilmelidir): normalize
    edilmis (referanssiz) sema `format` alaninda gonderilir."""

    scenario = await _valid_scenario()
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _success_response(content=scenario.model_dump_json())

    provider = _provider(structured_output_mode="json_schema")
    _install_transport(provider, handler)

    prompt = SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1")
    await provider.generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=prompt,
        input_payload=_scenario_input(),
        output_schema=ScenarioInterpretation,
    )

    body = captured["body"]
    assert body["format"] == _inline_schema_refs(ScenarioInterpretation.model_json_schema())
    assert "$defs" not in body["format"]
    # json_schema modunda sema `format`da zaten gonderildigi icin promptta
    # yalnizca kisa hatirlatma yeterlidir - kanonik sema metni TEKRAR EDILMEZ.
    assert (
        body["messages"][0]["content"]
        == "sys\n\nYanit YALNIZCA saglanan JSON semasina uyan tek bir JSON nesnesi olmalidir."
    )


async def test_request_includes_num_ctx_when_configured():
    scenario = await _valid_scenario()
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _success_response(content=scenario.model_dump_json())

    provider = _provider(num_ctx=4096)
    _install_transport(provider, handler)
    await provider.generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
        input_payload=_scenario_input(),
        output_schema=ScenarioInterpretation,
    )
    assert captured["body"]["options"]["num_ctx"] == 4096


def test_client_does_not_follow_redirects():
    provider = _provider()
    assert provider._client.follow_redirects is False  # type: ignore[attr-defined]


# =================================================================================
# SCHEMA NORMALIZATION (Faz 3D.3A.1 - $defs/$ref inlining kok neden duzeltmesi)
#
# Kok neden: gercek bir yerel Ollama daemon'ina (0.32.5, qwen3:8b) karsi
# calistirilan kontrollu bir reproduction cagrisinda, ic ice Pydantic
# modellerinin (`$defs`+`$ref`) urettigi HAM sema `format` alaninda
# gonderildiginde Ollama `/api/chat` HTTP 400 ile REDDETTI (baglanti/timeout/
# 5xx/model-not-found DEGIL). `_inline_schema_refs`, yalnizca Ollama'ya
# GONDERILEN semayi normalize eder; cevabin dogrulanmasi ORIJINAL, ic ice
# Pydantic modeliyle (degismeden) devam eder.
# =================================================================================


def test_raw_schema_represents_the_problematic_shape():
    """Kanitlanan kok nedenin temsil ettigi eski (sorunlu) sema seklini kilitler."""

    raw = ScenarioInterpretation.model_json_schema()
    assert "$defs" in raw
    assert raw["$defs"]
    assert '"$ref"' in json.dumps(raw)


def test_normalized_schema_has_no_refs_or_defs():
    raw = ScenarioInterpretation.model_json_schema()
    normalized = _inline_schema_refs(raw)
    assert "$defs" not in normalized
    assert '"$ref"' not in json.dumps(normalized)


def test_normalization_is_deterministic():
    raw = ScenarioInterpretation.model_json_schema()
    assert _inline_schema_refs(raw) == _inline_schema_refs(raw)


def test_normalization_does_not_mutate_input():
    import copy

    raw = ScenarioInterpretation.model_json_schema()
    raw_copy = copy.deepcopy(raw)
    _inline_schema_refs(raw)
    assert raw == raw_copy


def test_normalization_preserves_required_type_and_items_semantics():
    raw = ScenarioInterpretation.model_json_schema()
    normalized = _inline_schema_refs(raw)

    assert normalized["type"] == "object"
    assert set(normalized["required"]) == set(raw["required"])

    steps_prop = normalized["properties"]["steps"]
    assert steps_prop["type"] == "array"
    assert steps_prop["minItems"] == 1
    assert steps_prop["maxItems"] == 15
    assert "$ref" not in json.dumps(steps_prop)

    inlined_task_step = steps_prop["items"]
    assert inlined_task_step["type"] == "object"
    assert set(inlined_task_step["required"]) == {
        "step_id",
        "instruction",
        "success_criterion",
        "evidence_references",
    }
    # Alt-alan kisitlari (pattern/minLength/maxLength) da AYNEN korunmali -
    # normalizasyon yalnizca $ref/$defs'i duzler, baska hicbir seyi degistirmez.
    assert (
        inlined_task_step["properties"]["step_id"]["pattern"]
        == raw["$defs"]["TaskStep"]["properties"]["step_id"]["pattern"]
    )


def test_normalization_is_no_op_for_schema_without_defs():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    assert _inline_schema_refs(schema) == schema


async def test_response_validation_still_uses_original_nested_pydantic_model():
    """`format` normalize edilse de, model cevabi ORIJINAL (ic ice) semayla dogrulanir."""

    scenario = await _valid_scenario()

    async def handler(request: httpx.Request) -> httpx.Response:
        return _success_response(content=scenario.model_dump_json())

    provider = _provider()
    _install_transport(provider, handler)
    result = await provider.generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
        input_payload=_scenario_input(),
        output_schema=ScenarioInterpretation,
    )
    assert result.output == scenario
    assert isinstance(result.output, ScenarioInterpretation)


async def test_invalid_model_output_still_rejected_after_normalization():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps({"bad": "shape"})}})

    with pytest.raises(AIProviderInvalidOutputError):
        await _run_with_handler(_provider(), handler)


def test_fingerprint_differs_between_json_and_json_schema_modes():
    """`structured_output_mode` ("json" vs "json_schema") fingerprint'e girer -
    otomatik fallback olmadigi icin bu iki mod ASLA ayni fingerprint'i uretmemelidir."""

    json_provider = _provider(structured_output_mode="json")
    json_schema_provider = _provider(structured_output_mode="json_schema")
    assert json_provider.configuration_fingerprint != json_schema_provider.configuration_fingerprint


def test_default_structured_output_mode_is_json():
    """Ollama/llama.cpp'nin gercek grammar-parser hatasi kanitlandigi icin
    (bkz. modul ici not) "json" varsayilan moddur - acikca secilmeden
    "json_schema"ya DUSULMEZ."""

    cfg = Settings()
    assert cfg.ollama_structured_output_mode == "json"

    provider = _provider()
    assert provider._structured_output_mode == "json"  # type: ignore[attr-defined]


async def test_json_mode_does_not_call_inline_schema_refs(monkeypatch):
    """ "json" modunda `_inline_schema_refs` HIC CAGRILMAMALIDIR - sema Ollama'ya
    hic gonderilmiyor, yalnizca prompt metninde (ham/normalize edilmemis) yer alir."""

    from app.services.ai_pipeline import ollama_provider as module

    call_count = 0
    original = module._inline_schema_refs

    def _spy(schema):
        nonlocal call_count
        call_count += 1
        return original(schema)

    monkeypatch.setattr(module, "_inline_schema_refs", _spy)

    scenario = await _valid_scenario()

    async def handler(request: httpx.Request) -> httpx.Response:
        return _success_response(content=scenario.model_dump_json())

    provider = _provider(structured_output_mode="json")
    _install_transport(provider, handler)
    await provider.generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
        input_payload=_scenario_input(),
        output_schema=ScenarioInterpretation,
    )

    assert call_count == 0


async def test_json_schema_mode_calls_inline_schema_refs(monkeypatch):
    """ "json_schema" modunda `_inline_schema_refs` TAM OLARAK bir kez cagrilir."""

    from app.services.ai_pipeline import ollama_provider as module

    call_count = 0
    original = module._inline_schema_refs

    def _spy(schema):
        nonlocal call_count
        call_count += 1
        return original(schema)

    monkeypatch.setattr(module, "_inline_schema_refs", _spy)

    scenario = await _valid_scenario()

    async def handler(request: httpx.Request) -> httpx.Response:
        return _success_response(content=scenario.model_dump_json())

    provider = _provider(structured_output_mode="json_schema")
    _install_transport(provider, handler)
    await provider.generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
        input_payload=_scenario_input(),
        output_schema=ScenarioInterpretation,
    )

    assert call_count == 1


def test_openai_provider_structured_output_mode_unaffected():
    from app.services.ai_pipeline.openai_provider import STRUCTURED_OUTPUT_MODE as OPENAI_MODE

    assert OPENAI_MODE == "openai-responses.parse+pydantic-v1"


async def test_mock_provider_unaffected_by_ollama_schema_normalization():
    result = await MockAIProvider().generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=SimpleNamespace(),
        input_payload=_scenario_input(),
        output_schema=ScenarioInterpretation,
    )
    assert isinstance(result.output, ScenarioInterpretation)
    assert result.is_mock is True


# =================================================================================
# JSON MODE CIKTI DOGRULAMASI (Faz 3D.3A.2)
#
# "json" modunda `format="json"` YALNIZCA "gecerli JSON uret" der - hicbir
# alan/sema kisitlamasi tasimaz. Bu yuzden modelin dondurdugu icerik, HER
# ZAMAN orijinal `output_schema.model_validate_json` ile dogrulanir; asagidaki
# sekiller KESINLIKLE basarili KABUL EDILMEMELIDIR (domain semasi Ollama icin
# GEVSETILMEZ).
# =================================================================================


async def test_missing_required_field_rejected():
    # `limitations` (zorunlu) eksik.
    incomplete = {
        "scenario_version": "scenario-v1",
        "steps": [
            {
                "step_id": "s1",
                "instruction": "x",
                "success_criterion": "y",
                "evidence_references": ["e1"],
            }
        ],
        "success_criteria": ["done"],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps(incomplete)}})

    with pytest.raises(AIProviderInvalidOutputError):
        await _run_with_handler(_provider(), handler)


async def test_wrong_type_rejected():
    # `steps` bir dizi degil (yanlis tip) - ScenarioInterpretation'da enum
    # alani olmadigi icin (bu stage'in gercek semasinda enum yok) tip
    # uyusmazligi ile ayni sinifta bir dogrulama ihlali test edilir.
    wrong_type = {
        "scenario_version": "scenario-v1",
        "steps": "not-a-list",
        "success_criteria": ["done"],
        "limitations": "yok",
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps(wrong_type)}})

    with pytest.raises(AIProviderInvalidOutputError):
        await _run_with_handler(_provider(), handler)


async def test_markdown_fenced_json_rejected():
    scenario = await _valid_scenario()
    fenced_content = f"```json\n{scenario.model_dump_json()}\n```"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": True, "message": {"content": fenced_content}})

    with pytest.raises(AIProviderInvalidOutputError):
        await _run_with_handler(_provider(), handler)


async def test_free_text_before_and_after_json_rejected():
    scenario = await _valid_scenario()
    chatty_content = f"Iste istenen JSON:\n{scenario.model_dump_json()}\nUmarim yardimci olmustur!"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": True, "message": {"content": chatty_content}})

    with pytest.raises(AIProviderInvalidOutputError):
        await _run_with_handler(_provider(), handler)


async def test_truncated_json_rejected():
    scenario = await _valid_scenario()
    full_json = scenario.model_dump_json()
    truncated = full_json[: len(full_json) // 2]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": True, "message": {"content": truncated}})

    with pytest.raises(AIProviderInvalidOutputError):
        await _run_with_handler(_provider(), handler)


async def test_no_hidden_second_call_on_invalid_output():
    """Provider, gecersiz cikti/HTTP hatasinda SESSIZCE ikinci bir istek
    ATMAMALIDIR - retry otoritesi mevcut stage/attempt/lease sistemidir."""

    call_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"done": True, "message": {"content": "not-json"}})

    with pytest.raises(AIProviderInvalidOutputError):
        await _run_with_handler(_provider(), handler)

    assert call_count == 1


async def test_invalid_model_content_never_leaks_into_exception_message():
    sensitive_marker = "internal-model-output-should-never-leak-77"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": True, "message": {"content": sensitive_marker}})

    with pytest.raises(AIProviderInvalidOutputError) as exc_info:
        await _run_with_handler(_provider(), handler)

    assert sensitive_marker not in str(exc_info.value)


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

    mock_result = await MockAIProvider().generate_structured(
        stage_type=AIPipelineStageType.PERSONA_BEHAVIOR,
        batch_index=0,
        prompt=SimpleNamespace(),
        input_payload=behavior_input,
        output_schema=PersonaBehaviorBatchOutput,
    )
    output = mock_result.output

    async def handler(request: httpx.Request) -> httpx.Response:
        return _success_response(content=output.model_dump_json())

    provider = _provider()
    _install_transport(provider, handler)
    result = await provider.generate_structured(
        stage_type=AIPipelineStageType.PERSONA_BEHAVIOR,
        batch_index=0,
        prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
        input_payload=behavior_input,
        output_schema=PersonaBehaviorBatchOutput,
    )
    assert result.output == output


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

    async def handler(request: httpx.Request) -> httpx.Response:
        return _success_response(content=report.model_dump_json())

    provider = _provider()
    _install_transport(provider, handler)
    result = await provider.generate_structured(
        stage_type=AIPipelineStageType.UX_REPORT,
        batch_index=None,
        prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
        input_payload=report_input,
        output_schema=UXReport,
    )
    assert result.output == report


# =================================================================================
# CONCURRENCY / SEMAPHORE
# =================================================================================


async def test_max_concurrency_one_serializes_two_calls():
    scenario = await _valid_scenario()
    active = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return _success_response(content=scenario.model_dump_json())

    provider = _provider(max_concurrency=1)
    _install_transport(provider, handler)

    async def _call():
        return await provider.generate_structured(
            stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
            batch_index=None,
            prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
            input_payload=_scenario_input(),
            output_schema=ScenarioInterpretation,
        )

    await asyncio.gather(_call(), _call())
    assert peak == 1


async def test_semaphore_released_after_error():
    async def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider = _provider(max_concurrency=1)
    _install_transport(provider, failing_handler)

    with pytest.raises(AIProviderTransportError):
        await provider.generate_structured(
            stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
            batch_index=None,
            prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
            input_payload=_scenario_input(),
            output_schema=ScenarioInterpretation,
        )

    assert provider._semaphore._value == 1  # type: ignore[attr-defined]


async def test_semaphore_released_after_cancellation():
    async def slow_handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        raise AssertionError("should have been cancelled before completing")

    provider = _provider(max_concurrency=1)
    _install_transport(provider, slow_handler)

    task = asyncio.create_task(
        provider.generate_structured(
            stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
            batch_index=None,
            prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
            input_payload=_scenario_input(),
            output_schema=ScenarioInterpretation,
        )
    )
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider._semaphore._value == 1  # type: ignore[attr-defined]


# =================================================================================
# ERRORS
# =================================================================================


async def _run_with_handler(provider: OllamaProvider, handler):
    _install_transport(provider, handler)
    return await provider.generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
        input_payload=_scenario_input(),
        output_schema=ScenarioInterpretation,
    )


async def test_connection_refused_mapped_retryable():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(AIProviderTransportError) as exc_info:
        await _run_with_handler(_provider(), handler)
    assert exc_info.value.retryable is True


async def test_timeout_mapped_retryable():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(AIProviderTimeoutError) as exc_info:
        await _run_with_handler(_provider(), handler)
    assert exc_info.value.retryable is True


async def test_server_error_mapped_retryable():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(AIProviderServerError) as exc_info:
        await _run_with_handler(_provider(), handler)
    assert exc_info.value.retryable is True
    assert "boom" not in str(exc_info.value)


async def test_model_not_found_mapped_non_retryable():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    with pytest.raises(AIProviderInvalidRequestError) as exc_info:
        await _run_with_handler(_provider(), handler)
    assert exc_info.value.retryable is False


async def test_other_4xx_mapped_non_retryable():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad request"})

    with pytest.raises(AIProviderInvalidRequestError) as exc_info:
        await _run_with_handler(_provider(), handler)
    assert exc_info.value.retryable is False


async def test_4xx_body_never_leaks_into_exception_message():
    """Ham 4xx govdesi (ornegin sema hata metni) hicbir sekilde exception
    mesajina sizmamalidir - `_map_ollama_error` yalnizca TIPE gore (message
    string arama YAPMADAN) sabit, guvenli bir aciklama uretir."""

    sensitive_marker = "internal-schema-detail-should-never-leak-42"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": f"schema compile failed: {sensitive_marker}"})

    with pytest.raises(AIProviderInvalidRequestError) as exc_info:
        await _run_with_handler(_provider(), handler)

    assert exc_info.value.error_code == "provider_invalid_request"
    assert sensitive_marker not in str(exc_info.value)
    assert exc_info.value.retryable is False


async def test_done_false_raises_invalid_output_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": False, "message": {"content": "{}"}})

    with pytest.raises(AIProviderInvalidOutputError):
        await _run_with_handler(_provider(), handler)


async def test_empty_content_raises_invalid_output_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": True, "message": {"content": ""}})

    with pytest.raises(AIProviderInvalidOutputError):
        await _run_with_handler(_provider(), handler)


async def test_invalid_json_content_raises_invalid_output_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": True, "message": {"content": "not-json"}})

    with pytest.raises(AIProviderInvalidOutputError):
        await _run_with_handler(_provider(), handler)


async def test_invalid_json_body_raises_invalid_output_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-a-json-body")

    with pytest.raises(AIProviderInvalidOutputError):
        await _run_with_handler(_provider(), handler)


async def test_schema_mismatch_raises_invalid_output_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps({"unrelated": 1})}})

    with pytest.raises(AIProviderInvalidOutputError):
        await _run_with_handler(_provider(), handler)


# =================================================================================
# LIFECYCLE (startup/shutdown ctx injection) - network YOK
# =================================================================================


async def test_startup_injects_ollama_provider_only_when_ready(monkeypatch):
    from app import worker as app_worker
    from app.services.ai_pipeline.scheduling import AI_PIPELINE_PROVIDER_CTX_KEY

    monkeypatch.setattr(settings, "ai_report_enabled", True)
    monkeypatch.setattr(settings, "ai_report_provider", "ollama")
    monkeypatch.setattr(app_worker, "validate_production_secrets", lambda: None)

    ctx: dict = {}
    await app_worker.on_startup(ctx)
    provider = ctx[AI_PIPELINE_PROVIDER_CTX_KEY]
    assert isinstance(provider, OllamaProvider)
    assert provider.model_name == settings.ollama_model

    await app_worker.on_shutdown(ctx)


async def test_startup_injects_mock_provider(monkeypatch):
    from app import worker as app_worker
    from app.services.ai_pipeline.scheduling import AI_PIPELINE_PROVIDER_CTX_KEY

    monkeypatch.setattr(settings, "ai_report_enabled", True)
    monkeypatch.setattr(settings, "ai_report_provider", "mock")
    monkeypatch.setattr(app_worker, "validate_production_secrets", lambda: None)

    ctx: dict = {}
    await app_worker.on_startup(ctx)
    provider = ctx[AI_PIPELINE_PROVIDER_CTX_KEY]
    assert isinstance(provider, MockAIProvider)

    await app_worker.on_shutdown(ctx)  # no-op (MockAIProvider'da aclose yok)


async def test_shutdown_closes_ollama_client(monkeypatch):
    from app import worker as app_worker
    from app.services.ai_pipeline.scheduling import AI_PIPELINE_PROVIDER_CTX_KEY

    provider = _provider()
    ctx = {AI_PIPELINE_PROVIDER_CTX_KEY: provider}
    await app_worker.on_shutdown(ctx)
    assert provider._client.is_closed  # type: ignore[attr-defined]


def test_no_network_import_side_effects():
    import inspect

    from app.services.ai_pipeline import ollama_provider as module

    source = inspect.getsource(module)
    for forbidden in ("os.environ", "os.getenv"):
        assert forbidden not in source


def test_settings_local_env_cannot_force_real_provider_without_explicit_enable():
    """Yerel bir `.env`de yanlislikla AI_REPORT_PROVIDER=ollama/openai birakilmis
    olsa bile, `ai_report_enabled` acikca True yapilmadan hicbir provider hazir
    SAYILMAZ (bkz. Settings.ai_report_provider_ready) - testler bu nedenle
    gercek bir provider'a asla yanlislikla dusmez."""

    cfg = Settings(ai_report_enabled=False, ai_report_provider="ollama")
    assert cfg.ai_report_provider_ready is False
    cfg = Settings(ai_report_enabled=False, ai_report_provider="openai")
    assert cfg.ai_report_provider_ready is False


# =================================================================================
# FAZ 3D.2.1: TIMEOUT HIYERARSISI (provider timeout < arq job timeout < stale)
# =================================================================================


def test_default_ollama_timeout_is_below_arq_job_timeout():
    cfg = Settings()
    assert cfg.ollama_timeout_seconds == 240
    assert cfg.ollama_timeout_seconds < ARQ_JOB_TIMEOUT_SECONDS


def test_default_openai_timeout_is_below_arq_job_timeout():
    cfg = Settings()
    assert cfg.openai_timeout_seconds < ARQ_JOB_TIMEOUT_SECONDS


def test_worker_job_timeout_matches_single_source_of_truth():
    from app.worker import WorkerSettings

    assert WorkerSettings.job_timeout == ARQ_JOB_TIMEOUT_SECONDS


def test_ollama_timeout_exceeding_job_timeout_margin_is_rejected():
    max_allowed = ARQ_JOB_TIMEOUT_SECONDS - MIN_JOB_TIMEOUT_SAFETY_MARGIN_SECONDS
    Settings(ollama_timeout_seconds=max_allowed)  # sinirda kabul edilir
    with pytest.raises(ValidationError):
        Settings(ollama_timeout_seconds=max_allowed + 1)


def test_openai_timeout_exceeding_job_timeout_margin_is_rejected():
    max_allowed = ARQ_JOB_TIMEOUT_SECONDS - MIN_JOB_TIMEOUT_SAFETY_MARGIN_SECONDS
    Settings(openai_timeout_seconds=max_allowed)  # sinirda kabul edilir
    with pytest.raises(ValidationError):
        Settings(openai_timeout_seconds=max_allowed + 1)


async def test_semaphore_released_after_timeout():
    async def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _provider(max_concurrency=1)
    _install_transport(provider, timeout_handler)

    with pytest.raises(AIProviderTimeoutError):
        await provider.generate_structured(
            stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
            batch_index=None,
            prompt=SimpleNamespace(system_instructions="sys", prompt_key="k", prompt_version="v1"),
            input_payload=_scenario_input(),
            output_schema=ScenarioInterpretation,
        )

    assert provider._semaphore._value == 1  # type: ignore[attr-defined]


# =================================================================================
# FAZ 3D.2.1: OUTPUT TOKEN SINIRI (ollama_max_output_tokens / num_predict)
# =================================================================================


def test_ollama_max_output_tokens_must_be_positive():
    with pytest.raises(ValidationError):
        Settings(ollama_max_output_tokens=0)
    with pytest.raises(ValidationError):
        Settings(ollama_max_output_tokens=-1)


def test_ollama_max_output_tokens_rejects_excessively_large_value():
    Settings(ollama_max_output_tokens=8000)  # sinirda kabul edilir
    with pytest.raises(ValidationError):
        Settings(ollama_max_output_tokens=999_999)


def test_fingerprint_changes_with_max_output_tokens():
    a = _provider(max_output_tokens=2000)
    b = _provider(max_output_tokens=4000)
    assert a.configuration_fingerprint != b.configuration_fingerprint


async def test_done_reason_length_raises_invalid_output_error():
    """Output token sinirina ulasilinca Ollama `done_reason="length"` doner -
    icerik SEMAYA tesaduf UYSA BILE yarim/kesik olarak reddedilmelidir."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "length",
                "message": {"content": json.dumps({"unrelated": 1})},
            },
        )

    with pytest.raises(AIProviderInvalidOutputError):
        await _run_with_handler(_provider(), handler)


async def test_done_reason_stop_is_accepted():
    scenario = await _valid_scenario()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"content": scenario.model_dump_json()},
                "prompt_eval_count": 1,
                "eval_count": 1,
            },
        )

    result = await _run_with_handler(_provider(), handler)
    assert isinstance(result.output, ScenarioInterpretation)


# =================================================================================
# FAZ 3D.2.1: MOCK PROVIDER PRODUCTION GUVENLIGI
# =================================================================================


def test_mock_ready_in_development_environment():
    cfg = Settings(environment="development", ai_report_enabled=True, ai_report_provider="mock")
    assert cfg.ai_report_provider_ready is True


def test_mock_not_ready_in_production_by_default():
    cfg = Settings(environment="production", ai_report_enabled=True, ai_report_provider="mock")
    assert cfg.ai_report_provider_ready is False


def test_mock_ready_in_production_with_explicit_override():
    cfg = Settings(
        environment="production",
        ai_report_enabled=True,
        ai_report_provider="mock",
        allow_mock_ai_provider=True,
    )
    assert cfg.ai_report_provider_ready is True


def test_openai_and_ollama_provider_selection_unaffected_by_mock_guard():
    cfg = Settings(environment="production", ai_report_enabled=True, ai_report_provider="ollama")
    # allow_mock_ai_provider varsayilan False iken bile ollama/openai etkilenmez.
    assert cfg.ai_report_provider_ready is True
