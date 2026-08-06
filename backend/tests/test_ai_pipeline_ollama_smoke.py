"""Faz 3D.2-LOCAL: manuel Ollama smoke-test entrypoint'inin bayrak/provider
kapilama testleri.

Bu dosya `OllamaProvider.generate_structured`i GERCEKTEN cagirmaz - yalnizca
`--confirm-local-live` ve `AI_REPORT_PROVIDER` kapilarinin dogru
calistigini, ve provider'in TAM OLARAK bir kez cagrildigini dogrular (bkz.
gorev talimati madde 11/13)."""

from __future__ import annotations

import pytest

from app.config import settings
from app.services.ai_pipeline import ollama_smoke

pytestmark = pytest.mark.unit


async def test_missing_confirm_flag_never_calls_provider(monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(
        ollama_smoke.OllamaProvider,
        "generate_structured",
        lambda self, **kwargs: calls.append(kwargs) or pytest.fail("cagrilmamali"),
    )

    exit_code = await ollama_smoke._run(confirm_local_live=False)
    assert exit_code == 2
    assert calls == []


async def test_wrong_provider_never_calls_provider(monkeypatch):
    monkeypatch.setattr(settings, "ai_report_provider", "mock")
    calls: list[object] = []
    monkeypatch.setattr(
        ollama_smoke.OllamaProvider,
        "generate_structured",
        lambda self, **kwargs: calls.append(kwargs) or pytest.fail("cagrilmamali"),
    )

    exit_code = await ollama_smoke._run(confirm_local_live=True)
    assert exit_code == 2
    assert calls == []


async def test_confirmed_ollama_run_calls_provider_exactly_once(monkeypatch):
    monkeypatch.setattr(settings, "ai_report_provider", "ollama")

    call_count = 0
    closed = False

    class _FakeResult:
        input_tokens = 1
        output_tokens = 1
        request_duration_ms = 5
        estimated_cost = 0.0

    async def fake_generate_structured(self, **kwargs):
        nonlocal call_count
        call_count += 1
        return _FakeResult()

    async def fake_aclose(self):
        nonlocal closed
        closed = True

    monkeypatch.setattr(ollama_smoke.OllamaProvider, "generate_structured", fake_generate_structured)
    monkeypatch.setattr(ollama_smoke.OllamaProvider, "aclose", fake_aclose)

    exit_code = await ollama_smoke._run(confirm_local_live=True)
    assert exit_code == 0
    assert call_count == 1
    assert closed is True


def test_main_requires_explicit_flag(monkeypatch, capsys):
    monkeypatch.setattr(settings, "ai_report_provider", "disabled")
    exit_code = ollama_smoke.main([])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "--confirm-local-live" in captured.err
