"""`app.services.device_network_analysis` icin, `test_page_analysis.py` ile ayni
desende `httpx.MockTransport` kullanan servis-katmani testleri (gercek
analyzer/ag erisimi yoktur).
"""

import httpx
import pytest

from app.config import settings
from app.services import device_network_analysis
from app.services.exceptions import ModuleProcessingError

pytestmark = pytest.mark.unit


def _client_with_handler(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


async def test_success_maps_analyzer_response_to_module_result():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/analyze-device-network"
        assert request.headers["X-Analyzer-Token"] == settings.analyzer_shared_token
        return httpx.Response(
            200,
            json={
                "analyzer_version": "device-network-analyzer-2026.1",
                "url": "https://example.com/",
                "disclaimer": "gercek teknik olcum, gercek kullanici deneyimi degildir",
                "results": [
                    {
                        "profile_key": "desktop_broadband",
                        "device_label": "Masaustu",
                        "network_label": "Genis bant",
                        "succeeded": True,
                        "error": None,
                        "timings": {
                            "dom_content_loaded_ms": 100.0,
                            "load_event_ms": 150.0,
                            "total_navigation_ms": 150.0,
                        },
                        "accessibility_violation_count": 0,
                    }
                ],
                "error_rate": 0.0,
                "warnings": [],
            },
        )

    client = _client_with_handler(handler)
    result = await device_network_analysis.run_network_device_test("https://example.com/", client=client)

    assert result["module_key"] == "network_device_test"
    assert result["error_rate"] == 0.0
    assert len(result["profiles"]) == 1
    assert result["disclaimer"]


async def test_non_200_response_raises_module_processing_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "analyzer coktu"})

    client = _client_with_handler(handler)
    with pytest.raises(ModuleProcessingError):
        await device_network_analysis.run_network_device_test("https://example.com/", client=client)


async def test_connection_error_raises_module_processing_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("baglanti reddedildi")

    client = _client_with_handler(handler)
    with pytest.raises(ModuleProcessingError):
        await device_network_analysis.run_network_device_test("https://example.com/", client=client)
