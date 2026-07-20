"""`/internal/analyze-device-network` uc noktasinin testleri (network_device_test modulu).

`test_main.py` ile ayni desen: gercek bir Chromium baslatilmaz,
`app.main.analyze_device_network` monkeypatch ile sahte bir uygulamaya
baglanir. Amac uc nokta seviyesindeki guvenlik kontrollerini (token, yetki
onayi, bilinmeyen profil, SSRF hata haritalama) ve kismi profil
basarisizliginin tum istegi dusurmedigini dogrulamaktir.
"""

from fastapi.testclient import TestClient

from app import main
from app.browser import AnalysisError, DEVICE_NETWORK_PROFILES
from app.config import settings
from app.schemas import DeviceNetworkAnalysisResponse, DeviceNetworkProfileResult
from app.url_safety import UnsafeUrlError

client = TestClient(main.app)

VALID_HEADERS = {"X-Analyzer-Token": settings.analyzer_shared_token}

VALID_BODY = {
    "url": "https://example.com/",
    "authorization_confirmed": True,
    "profile_keys": ["desktop_broadband"],
}


def test_missing_token_is_rejected():
    response = client.post("/internal/analyze-device-network", json=VALID_BODY)
    assert response.status_code == 401


def test_wrong_token_is_rejected():
    response = client.post(
        "/internal/analyze-device-network", json=VALID_BODY, headers={"X-Analyzer-Token": "wrong-token"}
    )
    assert response.status_code == 401


def test_missing_authorization_confirmation_is_rejected():
    body = dict(VALID_BODY, authorization_confirmed=False)
    response = client.post("/internal/analyze-device-network", json=body, headers=VALID_HEADERS)
    assert response.status_code == 400
    assert "yetki" in response.json()["detail"].lower()


def test_unknown_profile_key_is_rejected():
    body = dict(VALID_BODY, profile_keys=["not_a_real_profile"])
    response = client.post("/internal/analyze-device-network", json=body, headers=VALID_HEADERS)
    assert response.status_code == 400
    assert "profil" in response.json()["detail"].lower()


def test_unsafe_url_is_rejected_with_valid_token_and_authorization(monkeypatch):
    async def _raise_unsafe(_url: str, _profile_keys: list[str]):
        raise UnsafeUrlError("Hostname engellenmis bir IP'ye cozumleniyor")

    monkeypatch.setattr(main, "analyze_device_network", _raise_unsafe)

    body = dict(VALID_BODY, url="http://127.0.0.1/")
    response = client.post("/internal/analyze-device-network", json=body, headers=VALID_HEADERS)
    assert response.status_code == 400


def test_analysis_error_returns_502(monkeypatch):
    async def _raise_analysis_error(_url: str, _profile_keys: list[str]):
        raise AnalysisError("beklenmeyen motor hatasi")

    monkeypatch.setattr(main, "analyze_device_network", _raise_analysis_error)

    response = client.post("/internal/analyze-device-network", json=VALID_BODY, headers=VALID_HEADERS)
    assert response.status_code == 502


def test_all_known_profile_keys_are_accepted_by_schema_validation(monkeypatch):
    # Bilinen tum profil anahtarlari `DEVICE_NETWORK_PROFILES`'ta tanimlidir
    # (bkz. app.browser); uc nokta bunlarin hicbirini "bilinmeyen profil"
    # olarak reddetmemelidir (analyze_device_network monkeypatch'lenir,
    # gercek Playwright calismaz).
    async def _fake_analyze(_url: str, profile_keys: list[str]):
        return DeviceNetworkAnalysisResponse(
            url=_url,
            results=[
                DeviceNetworkProfileResult(
                    profile_key=key,
                    device_label="Test",
                    network_label="Test",
                    succeeded=True,
                    timings=None,
                    accessibility_violation_count=0,
                )
                for key in profile_keys
            ],
            error_rate=0.0,
            warnings=[],
        )

    monkeypatch.setattr(main, "analyze_device_network", _fake_analyze)

    body = dict(VALID_BODY, profile_keys=list(DEVICE_NETWORK_PROFILES.keys()))
    response = client.post("/internal/analyze-device-network", json=body, headers=VALID_HEADERS)
    assert response.status_code == 200
    assert len(response.json()["results"]) == len(DEVICE_NETWORK_PROFILES)


def test_partial_profile_failure_does_not_fail_whole_request(monkeypatch):
    """Sabit profil kumesindeki tek bir profilin basarisiz olmasi (ör. timeout),
    diger profillerin sonuclarini dusurmez - `error_rate` bunu yansitir."""

    async def _fake_analyze(_url: str, profile_keys: list[str]):
        results = [
            DeviceNetworkProfileResult(
                profile_key=profile_keys[0],
                device_label="Masaustu",
                network_label="Genis bant",
                succeeded=True,
                timings=None,
                accessibility_violation_count=0,
            ),
            DeviceNetworkProfileResult(
                profile_key=profile_keys[1] if len(profile_keys) > 1 else "mobile_4g",
                device_label="Mobil",
                network_label="4G",
                succeeded=False,
                error="Sayfa zaman asimina ugradi (15s)",
            ),
        ]
        return DeviceNetworkAnalysisResponse(url=_url, results=results, error_rate=0.5, warnings=["profil basarisiz"])

    monkeypatch.setattr(main, "analyze_device_network", _fake_analyze)

    body = dict(VALID_BODY, profile_keys=["desktop_broadband", "mobile_4g"])
    response = client.post("/internal/analyze-device-network", json=body, headers=VALID_HEADERS)
    assert response.status_code == 200
    payload = response.json()
    assert payload["error_rate"] == 0.5
    assert payload["results"][0]["succeeded"] is True
    assert payload["results"][1]["succeeded"] is False
