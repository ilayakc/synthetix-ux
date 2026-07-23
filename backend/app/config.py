from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Uygulama genelinde kullanilan yapilandirma degerleri.

    Degerler .env dosyasindan veya ortam degiskenlerinden okunur.
    Varsayilanlar, .env dosyasi olmadan (ornegin container disinda
    testleri calistirirken) da makul bir yerel geliştirme ortamina
    dusmeyi saglar.
    """

    environment: str = "development"

    database_url: str = "postgresql+asyncpg://synthetix:devpassword@localhost:5432/synthetix_ux"
    redis_url: str = "redis://localhost:6379/0"

    backend_port: int = 8000

    # --- Kimlik dogrulama / oturum ---
    # Uretimde mutlaka ortam degiskeniyle (JWT_SECRET_KEY) gecersiz kilinmalidir;
    # varsayilan yalnizca .env olmadan yerel gelistirmeyi mumkun kilar.
    jwt_secret_key: str = "dev-insecure-secret-key-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_seconds: int = 30 * 24 * 60 * 60

    # Cookie'lerin `Secure` bayragi: None ise environment'a gore otomatik
    # belirlenir (development disinda True). Yerel http gelistirmede acikca
    # False birakilabilir; gercek bir uretim/staging ortaminda True olmalidir.
    cookie_secure: bool | None = None
    cookie_domain: str | None = None

    # Yalnizca yapilandirilmis frontend origin'ine izin verilir (CORS).
    cors_allowed_origin: str = "http://localhost:5173"

    # Giris denemesi hiz siniri (Redis tabanli, IP+e-posta anahtarli).
    login_rate_limit_max_attempts: int = 5
    login_rate_limit_window_seconds: int = 5 * 60
    login_rate_limit_lockout_seconds: int = 15 * 60

    # Parola sifirlama tokeni: kisa omurlu ve tek kullanimliktir. Bu
    # asamada gercek bir e-posta saglayicisi baglanmadigi icin ham token
    # yalnizca `ENVIRONMENT=development` iken API yanitinda dogrudan
    # dondurulur (bkz. docs/architecture.md).
    password_reset_token_ttl_seconds: int = 60 * 60

    # --- URL analiz servisi (analyzer) ---
    # analyzer, ayri bir container'da calisan, Playwright tabanli, SSRF'e
    # karsi korumali bir sayfa analiz servisidir (bkz. docs/security.md).
    # `analyzer_base_url`, docker compose ag'i icindeki servis adresidir;
    # `analyzer_shared_token`, yalnizca backend/worker'in bu ic servisi
    # cagirabilmesini saglayan paylasilan bir sirdir (uretimde mutlaka
    # degistirilmelidir).
    analyzer_base_url: str = "http://analyzer:8100"
    analyzer_shared_token: str = "dev-insecure-analyzer-token-change-me"
    analyzer_request_timeout_seconds: int = 30
    # network_device_test modulu, tek istekte 4 profili sirayla olcer (bkz.
    # app.services.device_network_analysis); bu nedenle tekil sayfa analizinden
    # daha uzun bir zaman asimi kullanir.
    analyzer_device_network_timeout_seconds: int = 90
    page_analysis_max_attempts: int = 3
    page_analysis_stale_timeout_seconds: int = 120
    page_analysis_screenshot_retention_seconds: int = 24 * 60 * 60
    # Paket 4 Final: tamamlanmis bir Rapor'a bagli PageAnalysis'in ekran
    # goruntusu, baglanti olmayan kisa-omurlu capture'lardan (yukaridaki
    # `page_analysis_screenshot_retention_seconds`) DAHA UZUN sureli
    # saklanir - aksi halde kisa TTL, kullanicinin sonradan actigi bir
    # raporun gorsel katmanini sessizce kaybettirirdi. SURESIZ DEGILDIR
    # (bkz. app.services.page_analysis.purge_expired_screenshots) - sure,
    # raporun OLUSTURULMA zamanindan (Report.created_at) itibaren islenir.
    # Varsayilan (30 gun), bu paket kapsaminda yeni/ayri ve belgelenmis bir
    # ayar olarak eklendi - onceden acikca tanimlanmis bir "rapor saklama
    # suresi" bulunmadigi icin makul, sinirli bir varsayilan secildi.
    report_linked_screenshot_retention_seconds: int = 30 * 24 * 60 * 60
    # Analyzer'in urettigi ekran goruntusu TEK BASINA guvenilmez (bkz.
    # app.services.page_analysis, app.services.image_safety) - saklanmadan
    # once GERCEKTEN decode edilerek bu sinirlara karsi dogrulanir. Sabit
    # Playwright viewport'lari (varsayilan 1366x900, en genis profil 1440x900 -
    # bkz. analyzer/app/browser.py) bu degerlerin cok altinda kaldigi icin
    # sinirlar bilinçli olarak `design_asset_max_*`'den daha dar tutulur.
    page_analysis_screenshot_max_bytes: int = 10 * 1024 * 1024
    page_analysis_screenshot_max_dimension: int = 4000
    page_analysis_screenshot_max_pixels: int = 8_000_000

    # --- Yuklenen tasarim ekran goruntuleri (bkz. app.services.design_assets) ---
    # Yalnizca PNG/JPEG/WebP kabul edilir; boyut/cozunurlum sinirlari asagida.
    # `page_analyses.screenshot_data` ile ayni TTL+purge desenini izler.
    design_asset_retention_seconds: int = 24 * 60 * 60
    # Yukleme (network istegi govdesi) siniri - `app.routers.design_assets`
    # bu deger asilir asilmaz akisi (stream) durdurur, tum dosyayi belege
    # onceden yuklemez.
    design_asset_max_bytes: int = 10 * 1024 * 1024
    # Yeniden encode edilmis (EXIF'i temizlenmis, saklanacak) ciktinin
    # boyut siniri. YUKLEME sinirindan (`design_asset_max_bytes`) KASITLI
    # OLARAK AYRI ve biraz daha genistir: PNG'ler orijinal sikistirma
    # ayarlarini korumadan yeniden encode edildiginde kaynaktan buyuk
    # cikabilir; bu ayri sinir olmasaydi meshru bir yukleme yalnizca
    # yeniden-encode fazlaligi yuzunden reddedilebilirdi. Yine de saklanan
    # veri sinirsiz buyuyemez.
    design_asset_max_stored_bytes: int = 20 * 1024 * 1024
    design_asset_max_dimension: int = 8000
    # Genislik*yukseklik ust siniri - `design_asset_max_dimension`'dan
    # BAGIMSIZ, ek bir savunma katmani (ör. cok uzun/ince gorsellerin
    # tek eksende sinirin altinda kalip toplam piksel sayisinda asiri
    # buyuk olmasini engeller). Pillow'un kendi `Image.MAX_IMAGE_PIXELS`
    # decompression-bomb esigi bu ayardan BAGIMSIZ olarak korunur, devre
    # disi birakilmaz (bkz. app.services.design_assets._decode_and_validate).
    design_asset_max_pixels: int = 25_000_000

    # --- AI destekli açıklama katmanı (bkz. app.services.ai_explanation) ---
    # ONEMLI: gelistiricinin kendi Claude Pro/IDE aboneligi bir API anahtari
    # SAYILMAZ. Varsayilan "none" saglayicisi hicbir anahtar/uzak servis
    # olmadan tamamen deterministik bir sablon aciklamasi uretir; urun bu
    # sekilde eksiksiz calisir. "remote" yalnizca ilgili uc nokta/anahtar da
    # ayarlanmissa aktif olur (bkz. `ai_remote_endpoint`).
    ai_provider: Literal["none", "remote"] = "none"
    ai_remote_endpoint: str | None = None
    ai_remote_api_key: str | None = None
    ai_remote_model_name: str = "unspecified"
    ai_request_timeout_seconds: int = 20
    ai_max_retries: int = 1
    ai_max_output_tokens: int = 800

    # --- AI ile gorsel (tasarim varyanti) uretimi (bkz. app.services.design_generation) ---
    # ONEMLI: yukaridaki `ai_*` metin/aciklama saglayicisindan TAMAMEN AYRI bir
    # yapilandirmadir - bir metin saglayicisinin gorsel uretebilecegi
    # VARSAYILMAZ. Varsayilan "none" saglayicisi HICBIR gorsel uretmez (metin
    # aciklamasinin aksine, "guvenli/deterministik bir sahte gorsel" diye bir
    # sey yoktur); AI ile tasarim varyanti secenegi frontend'de yalnizca
    # "remote" aktif VE gercek bir uc nokta ayarlanmissa gorunur/kullanilabilir
    # olur (bkz. GET /api/design-generations/availability). Gelistiricinin
    # kendi Claude Pro/IDE aboneligi bu API anahtari icin de GECERLI DEGILDIR.
    image_generation_provider: Literal["none", "remote"] = "none"
    image_generation_endpoint: str | None = None
    image_generation_api_key: str | None = None
    image_generation_model_name: str = "unspecified"
    image_generation_request_timeout_seconds: int = 60
    image_generation_max_retries: int = 1
    image_generation_max_prompt_length: int = 1000
    # `app.services.page_analysis` ile ayni "running'de takili kalma" kurtarma
    # deseni (bkz. app.services.design_generation.reap_stale_running); gorsel
    # uretimi bir sayfa analizinden cok daha uzun surebilecegi icin zaman
    # asimi payi kasitli olarak daha genis tutulur.
    design_generation_stale_timeout_seconds: int = 180
    design_generation_max_attempts: int = 3

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("cookie_secure", "cookie_domain", mode="before")
    @classmethod
    def _blank_env_value_means_unset(cls, value: object) -> object:
        # `.env` dosyasinda `COOKIE_SECURE=` gibi bos birakilmis bir
        # degisken, pydantic tarafindan gecersiz bir bool/str olarak
        # degerlendirilmemesi icin `None` (yani "otomatik belirle") olarak ele alinir.
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @property
    def resolved_cookie_secure(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.environment != "development"


settings = Settings()
