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

    # Tum analiz (navigasyon + scroll + screenshot + axe) icin toplam sure
    # butcesi. Ayri ayri navigation/axe timeout'larina EK olarak, hicbir tekil
    # analizin bu sureyi asmamasini garanti eder (finally cleanup her zaman
    # calisir). Backend'in analyzer_request_timeout'undan (varsayilan 180s)
    # KISA olmalidir ki backend timeout'a dusmeden analyzer typed hata donsun.
    analysis_total_timeout_seconds: int = 150

    # --- Bellek koruma (Render ucretsiz 512 MB) ---
    # Guard'i tamamen kapatmak icin (ör. bellek limiti bol olan ortamlar) tek
    # anahtar. Acikken cgroup limiti tespit edilir; edilemezse fallback kullanilir.
    memory_guard_enabled: bool = True
    # Container bellek limiti tespit edilemezse (cgroup 'max' dondururse, ör.
    # yerel Docker'da --memory verilmemisse) kullanilacak geri-donus limiti.
    container_memory_limit_mb: int = 512
    # ADMISSION: Chromium ACILMADAN once container'da en az bu kadar BOS bellek
    # yoksa analiz baslatilmaz - hemen retryable typed hata (analyzer_memory_
    # pressure) donulur. Hafif bir sayfanin baslamasina izin verecek kadar kucuk,
    # ama container zaten yukluyken (es zamanli worker/OpenCV, onceki analiz)
    # yeni bir Chromium acmayi engelleyecek kadar buyuk secilir.
    memory_admission_min_free_mb: int = 110
    # WATCHDOG: analiz SIRASINDA container bellegi limitin bu kesrini asarsa
    # (kernel OOM-kill'den ONCE) browser derhal kapatilir ve analiz retryable
    # typed hatayla iptal edilir - boylece agir bir sayfa TUM container'i (API+
    # worker+nginx) cokertmez. Kalan ~%12 marj, iptalin OOM'dan once tamamlanmasi
    # icin gerekli emniyet payidir.
    # 0.83: agir bir sayfada anon bellek limitin %83'unu (512'de ~425 MB) asinca
    # tripler. Hafif sayfalarin anon'u ~315 MB oldugu icin onlar ETKILENMEZ;
    # kalan ~%17 (~87 MB) marj, iki poll arasindaki ani anon artisinin (image
    # decode) kernel OOM-kill'e ulasmadan yakalanmasi icin emniyet payidir.
    # 512 MB'lik full container'da (API+worker+analyzer+nginx) OOM-kill/restart
    # OLMADAN kontrollu iptal olctuldu (restart_count=0, oom_killed=false).
    memory_guard_trip_pct: float = 0.83
    memory_guard_poll_ms: int = 80

    # --- Ekran goruntusu saklama ---
    screenshot_retention_seconds: int = 24 * 60 * 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
