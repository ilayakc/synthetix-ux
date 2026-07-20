import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.routers.auth import GENERIC_LOGIN_ERROR

# `client` fixture'i artik tests/conftest.py'de paylasilan altyapidan gelir
# (izole test veritabanina yonlendirilmis, testler arasi TRUNCATE ile
# temizlenen bir TestClient). Birden fazla kimlik gerektiginde asagidaki
# yardimcilar cookie jar'ini anlik goruntuleyip degistirir.

pytestmark = pytest.mark.integration


def _unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


def _register(client: TestClient, *, email: str | None = None, password: str = "CorrectHorse123!"):
    email = email or _unique_email()
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": password,
            "organization_name": f"Org {uuid.uuid4().hex[:8]}",
            "display_name": "Test User",
        },
    )
    return response, email, password


REFRESH_TOKEN_PATH = "/api/auth"


def _test_host_domain(client: TestClient) -> str:
    # Sunucunun gercekte kullandigi (implicit host) cookie etki alanini
    # jar'dan okur; manuel `set()` cagrilarinda ayni etki alani
    # kullanilmazsa httpx farkli bir kayit olusturur ve sunucunun
    # `delete_cookie` cagrisi bu "yetim" kaydi hicbir zaman temizleyemez.
    for cookie in client.cookies.jar:
        return cookie.domain
    return "testserver.local"


def _get_cookie(client: TestClient, name: str) -> str | None:
    # httpx'in `Cookies.get()` metodu, jar'da ayni isimde (farkli yol/etki
    # alaninda) birden fazla kayit varsa `CookieConflict` firlatir; burada
    # jar dogrudan taranarak (en son eslesen deger alinarak) bu kaçınılır.
    path = REFRESH_TOKEN_PATH if name == "refresh_token" else "/"
    matches = [cookie.value for cookie in client.cookies.jar if cookie.name == name and cookie.path == path]
    if not matches:
        matches = [cookie.value for cookie in client.cookies.jar if cookie.name == name]
    return matches[-1] if matches else None


def _set_cookie(client: TestClient, name: str, value: str) -> None:
    path = REFRESH_TOKEN_PATH if name == "refresh_token" else "/"
    client.cookies.set(name, value, domain=_test_host_domain(client), path=path)


def _csrf_headers(client: TestClient) -> dict:
    token = _get_cookie(client, "csrf_token")
    assert token, "csrf_token cookie set olmali"
    return {"X-CSRF-Token": token}


def _snapshot_cookies(client: TestClient) -> list[tuple[str, str, str, str]]:
    return [(cookie.name, cookie.value, cookie.path, cookie.domain) for cookie in client.cookies.jar]


def _restore_cookies(client: TestClient, snapshot: list[tuple[str, str, str, str]]) -> None:
    client.cookies.clear()
    for name, value, path, domain in snapshot:
        client.cookies.set(name, value, domain=domain, path=path)


# --- Kayit -------------------------------------------------------------


def test_register_creates_organization_with_zero_chip_and_two_free_entitlements(client):
    response, email, _ = _register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == email
    assert body["role"] == "owner"

    # Kayit otomatik oturum acar (cookie'ler ayarlanir); ayni istemciyle
    # korumali kullanim ozeti sorgulanabilir olmali.
    summary = client.get("/api/billing/usage-summary")
    assert summary.status_code == 200
    summary_body = summary.json()
    assert summary_body["organization_id"] == body["organization_id"]
    assert summary_body["chip_balance"] == 0

    entitlements_by_key = {e["feature_key"]: e for e in summary_body["entitlements"]}
    assert set(entitlements_by_key) == {"basic_ux_test", "accessibility_precheck"}
    for entitlement in entitlements_by_key.values():
        assert entitlement["status"] == "available"
        assert entitlement["quantity"] == 1


def test_register_rejects_duplicate_email(client):
    _, email, password = _register(client)
    response, _, _ = _register(client, email=email, password=password)
    assert response.status_code == 409


# --- Giris ---------------------------------------------------------------


def test_login_with_wrong_password_returns_generic_error(client):
    _, email, _ = _register(client)

    response = client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json()["detail"] == GENERIC_LOGIN_ERROR


def test_login_with_unknown_email_returns_same_generic_error(client):
    response = client.post(
        "/api/auth/login",
        json={"email": _unique_email(), "password": "does-not-matter-123"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == GENERIC_LOGIN_ERROR


def test_login_with_correct_password_succeeds_and_me_reflects_session(client):
    _, email, password = _register(client)

    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    assert response.json()["email"] == email

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == email


def test_me_requires_authentication(client):
    previous = _snapshot_cookies(client)
    _restore_cookies(client, [])
    try:
        response = client.get("/api/auth/me")
        assert response.status_code == 401
    finally:
        _restore_cookies(client, previous)


# --- Refresh rotation / reuse tespiti -------------------------------------


def test_refresh_rotates_refresh_token(client):
    _register(client)

    old_refresh_token = _get_cookie(client, "refresh_token")
    assert old_refresh_token

    response = client.post("/api/auth/refresh", headers=_csrf_headers(client))
    assert response.status_code == 200

    new_refresh_token = _get_cookie(client, "refresh_token")
    assert new_refresh_token
    assert new_refresh_token != old_refresh_token

    # Rotasyon sonrasi oturum hala gecerli olmali.
    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200


@pytest.mark.security
def test_refresh_token_reuse_is_detected_and_revokes_session(client):
    _register(client)

    old_refresh_token = _get_cookie(client, "refresh_token")
    first_refresh = client.post("/api/auth/refresh", headers=_csrf_headers(client))
    assert first_refresh.status_code == 200
    rotated_refresh_token = _get_cookie(client, "refresh_token")
    assert rotated_refresh_token != old_refresh_token

    # `first_refresh` csrf_token cookie'sini de dondurdugu icin reuse
    # denemesinden once guncel CSRF header'i tekrar okumak gerekir.
    csrf_token_value = _get_cookie(client, "csrf_token")
    csrf_headers = {"X-CSRF-Token": csrf_token_value}

    # Eski (zaten rotasyona ugramis) tokeni tekrar sunmak reuse sayilmali ve
    # tum oturum zincirini (access/refresh/csrf cookie'leri dahil) iptal
    # etmelidir.
    _set_cookie(client, "refresh_token", old_refresh_token)
    reuse_response = client.post("/api/auth/refresh", headers=csrf_headers)
    assert reuse_response.status_code == 401

    # Reuse tespiti sunucu tarafinda tum cookie'leri temizledigi icin,
    # rotasyonla uretilmis "gecerli" tokenin de artik calismadigini
    # dogrulamak icin CSRF cookie'sini elle geri yukleyip CSRF kapisini
    # gecerek dogrudan refresh-token gecersizligini test ederiz.
    _set_cookie(client, "csrf_token", csrf_token_value)
    _set_cookie(client, "refresh_token", rotated_refresh_token)
    after_reuse_response = client.post("/api/auth/refresh", headers=csrf_headers)
    assert after_reuse_response.status_code == 401


@pytest.mark.security
def test_refresh_without_csrf_header_is_rejected(client):
    _register(client)

    response = client.post("/api/auth/refresh")
    assert response.status_code == 403


# --- Cikis -----------------------------------------------------------------


def test_logout_revokes_session(client):
    _register(client)

    refresh_token = _get_cookie(client, "refresh_token")
    csrf_token_value = _get_cookie(client, "csrf_token")
    csrf_headers = {"X-CSRF-Token": csrf_token_value}

    logout_response = client.post("/api/auth/logout", headers=csrf_headers)
    assert logout_response.status_code == 204

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 401

    # Logout tum cookie'leri (csrf_token dahil) temizler; iptal edilmis
    # refresh tokenin artik gecersiz oldugunu dogrulamak icin CSRF
    # cookie'sini elle geri yukleyip CSRF kapisini geceriz.
    _set_cookie(client, "csrf_token", csrf_token_value)
    _set_cookie(client, "refresh_token", refresh_token)
    refresh_after_logout = client.post("/api/auth/refresh", headers=csrf_headers)
    assert refresh_after_logout.status_code == 401


# --- Parola sifirlama --------------------------------------------------------


def test_password_reset_request_returns_generic_message_regardless_of_email_existence(client):
    _, email, _ = _register(client)

    known_response = client.post("/api/auth/password-reset/request", json={"email": email})
    unknown_response = client.post("/api/auth/password-reset/request", json={"email": _unique_email()})

    assert known_response.status_code == 200
    assert unknown_response.status_code == 200
    assert known_response.json()["message"] == unknown_response.json()["message"]

    # Gelistirme ortaminda yalnizca gercekten var olan bir e-posta icin dev
    # tokeni dondurulur; bilinmeyen e-posta icin hicbir sey uretilmemelidir.
    assert known_response.json()["dev_reset_token"]
    assert unknown_response.json()["dev_reset_token"] is None


def test_password_reset_confirm_changes_password_and_revokes_sessions(client):
    _, email, old_password = _register(client)

    reset_request = client.post("/api/auth/password-reset/request", json={"email": email})
    reset_token = reset_request.json()["dev_reset_token"]
    assert reset_token

    new_password = "BrandNewPassword456!"
    confirm_response = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": reset_token, "new_password": new_password},
    )
    assert confirm_response.status_code == 200

    # Parola sifirlama, guvenlik icin mevcut oturumu (refresh zinciri) da
    # iptal etmelidir.
    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 401

    # Eski parola artik gecersiz, yeni parola calismali.
    old_password_login = client.post("/api/auth/login", json={"email": email, "password": old_password})
    assert old_password_login.status_code == 401

    new_password_login = client.post("/api/auth/login", json={"email": email, "password": new_password})
    assert new_password_login.status_code == 200


def test_password_reset_confirm_rejects_reused_token(client):
    _, email, _ = _register(client)

    reset_request = client.post("/api/auth/password-reset/request", json={"email": email})
    reset_token = reset_request.json()["dev_reset_token"]

    first_confirm = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": reset_token, "new_password": "FirstNewPassword789!"},
    )
    assert first_confirm.status_code == 200

    second_confirm = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": reset_token, "new_password": "SecondNewPassword789!"},
    )
    assert second_confirm.status_code == 400


# --- Hiz siniri (rate limit) ------------------------------------------------


@pytest.mark.security
def test_login_rate_limit_locks_out_after_max_attempts(client):
    _, email, password = _register(client)

    for _attempt in range(settings.login_rate_limit_max_attempts):
        response = client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
        assert response.status_code == 401

    locked_response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert locked_response.status_code == 429


# --- Kiraci (tenant) izolasyonu ----------------------------------------------


@pytest.mark.security
def test_cannot_access_another_organizations_billing_data(client):
    response_a, _, _ = _register(client)
    org_a_id = response_a.json()["organization_id"]
    snapshot_a = _snapshot_cookies(client)

    response_b, _, _ = _register(client)
    org_b_id = response_b.json()["organization_id"]

    assert org_a_id != org_b_id

    # Artik organizasyon baglami bir istemci basligindan degil, yalnizca
    # kimlik dogrulama cookie'sinden turetiliyor; sahte bir
    # X-Organization-Id basligi baska bir organizasyonun verisine erisim
    # saglamamalidir.
    forged_response = client.get("/api/billing/usage-summary", headers={"X-Organization-Id": org_a_id})
    assert forged_response.status_code == 200
    assert forged_response.json()["organization_id"] == org_b_id

    # Org A'nin oturumuna donuldugunde de yalnizca kendi verisi gorunmeli.
    _restore_cookies(client, snapshot_a)
    org_a_response = client.get("/api/billing/usage-summary")
    assert org_a_response.status_code == 200
    assert org_a_response.json()["organization_id"] == org_a_id
