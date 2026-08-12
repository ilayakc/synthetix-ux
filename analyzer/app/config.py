from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Analyzer servisinin calisma zamani limitleri ve yapilandirmasi.

    Tum limitler kasitli olarak kucuk/muhafazakar varsayilanlara sahiptir
    (bkz. docs/security.md "Operasyon limitleri"); bu servis, kullanicinin
    girdigi keyfi bir URL'yi gercekten ziyaret eden tek bilesendir ve bu
    yuzden en kisitli sandbox olmalidir.
    """

    environment: str = "development"

    # Backend<->analyzer arasi paylasilan sir; yalnizca bu token'i bilen
    # istemciler /internal/analyze uc noktasini cagirabilir (servis, docker
    # compose ag'i disina host'a acilmaz - bkz. compose.yaml).
    analyzer_shared_token: str = "dev-insecure-analyzer-token-change-me"

    # --- Istek basina limitler ---
    navigation_timeout_seconds: int = 15
    max_redirects: int = 3
    max_response_bytes: int = 10 * 1024 * 1024  # 10 MiB
    max_pages_per_request: int = 1  # bu asamada tek sayfa analizi; coklu sayfa gezinme yok
    viewport_width: int = 1366
    viewport_height: int = 900
    # Uzun sayfalarda tembel yuklenen icerigi yakala; backend'in 4.000 px
    # boyut guvenlik sinirini asmamak icin ekran goruntusunu sinirla.
    screenshot_max_height: int = 4000
    dynamic_content_settle_ms: int = 750
    # Tamamen bos bir DOM/skeleton donen dinamik veya otomasyon-korumali
    # sayfalarda hemen sahte bir "basarili" snapshot uretme. Ayni pasif DOM
    # olcumunu sinirli sayida tekrarla; form/tiklama/reload yapilmaz.
    empty_snapshot_max_checks: int = 3
    empty_snapshot_retry_delay_ms: int = 1000
    # axe-core buyuk/karmasik DOM'larda sayfanin geri kalan analizini
    # engellememeli. Bu sure asilinca temel snapshot korunur ve on kontrol
    # acik bir "skipped" durumu + uyariyla raporlanir.
    accessibility_scan_timeout_seconds: int = 20

    # --- Eszamanlilik ve kaynak sinirlari ---
    max_concurrent_analyses: int = 2

    # --- Ekran goruntusu saklama ---
    screenshot_retention_seconds: int = 24 * 60 * 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
