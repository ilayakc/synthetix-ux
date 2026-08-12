import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.services.ai_interaction_heatmap.openai_selector import InteractionHeatmapOpenAISelector
from app.services.ai_interaction_heatmap.schemas import (
    METHOD_OPENAI_SELECTION,
    METHOD_OPENAI_VISION_SELECTION,
    AIHotspotSelection,
    AIInteractionOutput,
    InteractionCandidate,
)


def _png_bytes(width: int = 400, height: int = 800) -> bytes:
    img = Image.new("RGB", (width, height), (238, 238, 238))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeResponses:
    def __init__(self) -> None:
        self.kwargs = None

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            status="completed",
            output=[],
            output_parsed=AIInteractionOutput(
                summary="Hedef görev için uygun aday.",
                hotspots=[
                    AIHotspotSelection(
                        candidate_id="candidate-1",
                        score=0.9,
                        confidence="high",
                        reason="Görevle doğrudan eşleşiyor.",
                        task_relevance="direct",
                    )
                ],
            ),
            usage=SimpleNamespace(input_tokens=100, output_tokens=30),
        )


def _selector(model: str = "gpt-5.6-terra"):
    selector = InteractionHeatmapOpenAISelector.__new__(InteractionHeatmapOpenAISelector)
    selector.model_name = model
    selector._reasoning_effort = "medium"
    selector._max_output_tokens = 1000
    responses = _FakeResponses()
    selector._client = SimpleNamespace(responses=responses)
    return selector, responses


def _candidate() -> InteractionCandidate:
    return InteractionCandidate(
        candidate_id="candidate-1",
        label="Hesap oluştur",
        interaction_kind="cta_link",
        role="link",
        x=0.6,
        y=0.1,
        w=0.2,
        h=0.05,
        above_fold=True,
    )


@pytest.mark.asyncio
async def test_gpt_terra_receives_screenshot_and_persona_distribution():
    selector, responses = _selector()
    result = await selector.select(
        candidates=[_candidate()],
        target_task="Hesap oluştur",
        target_audience="Yeni ziyaretçiler",
        persona_distribution={"mobile": 0.7, "desktop": 0.3},
        confirmed_candidate_id=None,
        screenshot_data=b"fake-png",
        screenshot_content_type="image/png",
    )

    assert result.method == METHOD_OPENAI_VISION_SELECTION
    content = responses.kwargs["input"][1]["content"]
    assert any(item["type"] == "input_image" for item in content)
    text_item = next(item for item in content if item["type"] == "input_text")
    assert '"persona_distribution": {"mobile": 0.7, "desktop": 0.3}' in text_item["text"]


@pytest.mark.asyncio
async def test_vision_request_includes_clean_and_numbered_overlay_images():
    """Test 1/2: gorsel destekli modele GERCEK iki goruntu gonderilir - temiz
    ekran goruntusu + numarali aday overlay - ve candidate view marker_number icerir.
    (URL ve screenshot kaynagi ayni kod yolunu kullanir; fark aday etiketleridir.)"""

    selector, responses = _selector()  # gpt-5.6-terra -> gorsel destekli
    result = await selector.select(
        candidates=[_candidate()],
        target_task="Hesap oluştur",
        target_audience=None,
        persona_distribution=None,
        confirmed_candidate_id=None,
        screenshot_data=_png_bytes(),
        screenshot_content_type="image/png",
    )

    assert result.method == METHOD_OPENAI_VISION_SELECTION
    content = responses.kwargs["input"][1]["content"]
    images = [item for item in content if item["type"] == "input_image"]
    assert len(images) == 2, "temiz goruntu + numarali overlay gonderilmeli"
    # Ikinci goruntu numarali overlay PNG'sidir.
    assert images[1]["image_url"].startswith("data:image/png;base64,")
    text = next(item for item in content if item["type"] == "input_text")["text"]
    assert '"marker_number": 1' in text


@pytest.mark.asyncio
async def test_unsupported_model_does_not_claim_or_send_image_analysis():
    selector, responses = _selector("legacy-text-only")
    result = await selector.select(
        candidates=[_candidate()],
        target_task="Hesap oluştur",
        target_audience=None,
        persona_distribution=None,
        confirmed_candidate_id=None,
        screenshot_data=b"fake-png",
        screenshot_content_type="image/png",
    )

    assert result.method == METHOD_OPENAI_SELECTION
    content = responses.kwargs["input"][1]["content"]
    assert not any(item["type"] == "input_image" for item in content)
