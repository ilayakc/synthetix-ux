"""Worker dayaniklilik regresyonu: eksik/opsiyonel bir AI SDK'si (ozellikle
`openai`) TUM worker'i DUSURMEMELIDIR.

Kok neden (bu dosyanin dogruladigi production hatasi): `app.worker`, `ai_report`
kapali olsa bile `OpenAIProvider`i import eder. `openai_provider` daha once
`openai`yi MODUL SEVIYESINDE import ediyordu; deploy edilen worker imaji `openai`
icermiyorsa (eski/eksik build) `arq app.worker.WorkerSettings` import ANINDA
cokup crash-loop'a giriyor, boylece simulasyon islemcisi (`process_queued_
simulations`) VE stale-run reaper (`reap_stale_simulations`) hic calismiyor -
retry sonrasi RUNNING'e gecen bir run "Gelismis moduller isleniyor" (%40)
asamasinda sonsuza kadar takili kaliyordu.

Duzeltme: `openai` artik LAZILY import edilir (bkz. openai_provider modul basi
notu); ayrica `app.worker.on_startup` provider baslatma hatasinda TUM worker'i
dusurmek yerine ACIK bir ERROR loglayip AI'yi devre disi birakir (simulasyon
isleme etkilenmez).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from pydantic import SecretStr

from app.config import settings

pytestmark = pytest.mark.integration


def test_worker_imports_and_simulation_functions_survive_without_openai():
    """`openai` kurulu DEGILKEN bile `app.worker` import edilebilmeli ve
    simulasyon isleyici + reaper cron fonksiyonlari kayitli kalmali; ayrica
    `OpenAIProvider` INSTANTIATE edilince acik `ModuleNotFoundError` firlatmali
    (sessizce yutulmaz - cagiran on_startup bunu yakalar)."""

    script = textwrap.dedent(
        """
        import sys
        # Eski/eksik imaji taklit et: 'import openai' basarisiz olsun.
        sys.modules['openai'] = None

        import app.services.ai_pipeline.openai_provider as prov
        import app.worker as w

        names = {f.__name__ for f in w.WorkerSettings.functions}
        assert 'process_queued_simulations' in names, names
        assert 'reap_stale_simulations' in names, names
        assert 'fail_simulations_blocked_by_page_analysis' in names, names
        print('IMPORT_OK')

        try:
            prov.OpenAIProvider(
                api_key='test-key-not-real',
                model='m',
                reasoning_effort='low',
                timeout_seconds=30,
                max_output_tokens=100,
            )
        except ModuleNotFoundError:
            print('INSTANTIATE_RAISED_ModuleNotFoundError')
        else:
            raise AssertionError('openai yokken OpenAIProvider hata firlatmaliydi')
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "IMPORT_OK" in result.stdout, result.stdout
    assert "INSTANTIATE_RAISED_ModuleNotFoundError" in result.stdout, result.stdout


async def test_on_startup_degrades_when_ai_provider_init_fails(monkeypatch):
    """AI provider baslatma (ornegin `openai` eksik) BASARISIZ olsa bile
    `on_startup` exception FIRLATMAMALI, provider'i None birakmali ve worker
    (simulasyon isleme) ayakta kalmali."""

    from app import worker as app_worker
    from app.services.ai_pipeline.scheduling import AI_PIPELINE_PROVIDER_CTX_KEY

    monkeypatch.setattr(settings, "ai_report_enabled", True)
    monkeypatch.setattr(settings, "ai_report_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("test-key-not-real"))
    monkeypatch.setattr(app_worker, "validate_production_secrets", lambda: None)

    # Gercek `OpenAIProvider` bir SINIFTIR; monkeypatch de sinif olmali ki
    # `on_shutdown`daki `OpenAIProvider | OllamaProvider` tip birlesimi gecerli
    # kalsin. `__init__` `openai` eksikligini taklit ederek hata firlatir.
    class _BoomProvider:
        def __init__(self, **_kwargs) -> None:
            raise ModuleNotFoundError("No module named 'openai'")

    monkeypatch.setattr(app_worker, "OpenAIProvider", _BoomProvider)

    ctx: dict = {}
    # on_startup exception FIRLATMAMALI (aksi halde arq worker cokerdi).
    await app_worker.on_startup(ctx)

    assert ctx[AI_PIPELINE_PROVIDER_CTX_KEY] is None
    await app_worker.on_shutdown(ctx)  # None provider ile guvenli no-op
