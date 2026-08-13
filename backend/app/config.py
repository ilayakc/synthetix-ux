from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ollama base URL icin varsayilan olarak izin verilen loopback host'lari
# (bkz. Settings.ollama_allow_remote_host / `_is_allowed_ollama_host`) - SSRF'e
# karsi: acikca `ollama_allow_remote_host=True` verilmeden BASKA hicbir host
# kabul edilmez.
_OLLAMA_ALLOWED_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def _is_allowed_ollama_host(base_url: str, *, allow_remote_host: bool) -> bool:
    """`base_url`in Ollama icin izinli bir host tasiyip tasimadigini dogrular.

    `allow_remote_host=False` (varsayilan) iken YALNIZCA loopback host'lara
    (127.0.0.1/localhost/::1) izin verilir. Bos/gecersiz URL veya http(s)
    disinda bir semaya sahip URL de REDDEDILIR."""

    try:
        parsed = urlsplit(base_url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    if allow_remote_host:
        return True
    return host.lower() in _OLLAMA_ALLOWED_LOOPBACK_HOSTS


# `openai.reasoning={"effort": ...}` icin desteklenen degerler (bkz. kurulu
# `openai` SDK'nin `Reasoning` typed sozlugu) - varsayilan "medium"dur (Faz
# 3D.1 gorev talimati "Resmi OpenAI kararlari").
OpenAIReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    """Uygulama genelinde kullanilan yapilandirma degerleri.

    Degerler .env dosyasindan veya ortam degiskenlerinden okunur.
    Varsayilanlar, .env dosyasi olmadan (ornegin container disinda
    testleri calistirirken) da makul bir yerel geliştirme ortamina
    dusmeyi saglar.
    """

    environment: str = "development"

    # --- Loglama (bkz. app.logging_config, app.logging_utils) ---
    # `log_format` bos/None ise environment'a gore otomatik secilir
    # (production -> json, aksi halde pretty); acikca "json" veya "pretty"
    # verilerek gecersiz kilinabilir.
    log_level: str = "INFO"
    log_format: Literal["json", "pretty"] | None = None
    # Bu esigi (milisaniye) asan istek/is fonksiyonu sureleri WARNING
    # seviyesinde loglanir (bkz. app.logging_utils.log_duration).
    slow_request_threshold_ms: int = 1000
    # Istek loglama middleware'inin (bkz. app.main) atlayacagi, virgulle
    # ayrilmis yol listesi - health-check gibi gurultulu uc noktalar icin.
    log_exclude_paths: str = "/api/health"

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

    # Production'da `TrustedHostMiddleware` icin virgulle ayrilmis host
    # allowlist'i (bkz. app.main, app.config_security). Development'ta bu
    # middleware hic eklenmedigi icin varsayilan "*" zararsizdir; production'da
    # ise "*" veya bos deger fail-closed olarak REDDEDILIR (bkz.
    # app.config_security.validate_production_secrets) - gercek bir host
    # allowlist'i acikca belirtilmeden production baslamaz.
    allowed_hosts: str = "*"

    # Giris denemesi hiz siniri (Redis tabanli, IP+e-posta anahtarli).
    login_rate_limit_max_attempts: int = 5
    login_rate_limit_window_seconds: int = 5 * 60
    login_rate_limit_lockout_seconds: int = 15 * 60

    # Parola sifirlama tokeni: kisa omurlu ve tek kullanimliktir. Bu
    # asamada gercek bir e-posta saglayicisi baglanmadigi icin ham token
    # yalnizca `ENVIRONMENT=development` iken API yanitinda dogrudan
    # dondurulur (bkz. docs/architecture.md).
    password_reset_token_ttl_seconds: int = 60 * 60

    # Internete acik sunum kurulumunda yonetici hesabi icin yalnizca ortam
    # degiskenlerinden verilen kimlik bilgileri kullanilir. Varsayilan olarak
    # bootstrap kapali; sabit/bilinen bir uretim parolasi yoktur.
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: SecretStr | None = None
    bootstrap_user_email: str | None = None
    bootstrap_user_password: SecretStr | None = None

    # Internete acik sunum kurulumunda, ziyaretcilerin PAROLA GIRMEDEN tek
    # tikla demo hesabina (yukaridaki `bootstrap_user_email`) girebilmesini
    # saglayan, VARSAYILAN OLARAK KAPALI bir kapi. Yalnizca `true` iken
    # `POST /api/auth/demo-login` calisir ve HICBIR ZAMAN rastgele bir hesaba
    # degil, YALNIZCA yapilandirilmis (yonetici OLMAYAN) demo kullanicisina
    # oturum acar; kapaliyken uc nokta 404 doner (bkz. app.routers.auth).
    # Demo hesabinin parolasi bu akista hic kullanilmaz - o yuzden bilinen/
    # sabit bir parola yayinlamaya gerek yoktur.
    demo_login_enabled: bool = False
    # Demo girisinin IP basina sabit-pencere hiz siniri (oturum tablosunu
    # sel etmeye karsi; bkz. app.services.rate_limit.is_demo_login_rate_limited).
    demo_login_rate_limit_max_attempts: int = 20
    demo_login_rate_limit_window_seconds: int = 5 * 60

    # Herkese acik "Canli demo" hesabi: `bootstrap_user_email`den (gelistiricinin
    # kendi, dolu demo hesabi) TAMAMEN AYRI, TAZE bir hesaptir. Amaci yalnizca
    # 1 ornek rapor gostermek ve aray uzde gezinmeye izin vermektir. `demo-login`
    # ucu YALNIZCA bu hesaba oturum acar. Parola bu akista hic kullanilmaz;
    # yalnizca hesabin bootstrap'ta olusturulabilmesi (password_hash zorunlulugu)
    # icin gereklidir - o yuzden rastgele/uretilmis bir parola yeterlidir.
    demo_account_email: str | None = None
    demo_account_password: SecretStr | None = None

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
    # --- AI raporu pipeline modulu (`ai_report`) hazirlik/erisim bayragi ---
    # (bkz. app.services.module_catalog `ai_report`, Faz 3C.2B1). GERCEK bir AI
    # rapor saglayicisi henuz YOKTUR; bu bayrak `false` (guvenli varsayilan)
    # oldugu surece `ai_report` modulu sihirbaz katalogunda gizlenir VE launch
    # sunucu tarafinda reddedilir (Chip rezervasyonu/SimulationRun/pipeline
    # OLUSMADAN). `ai_provider="none"` gibi otomatik bir Mock/yedek saglayiciya
    # DUSULMEZ - hazirlik acikca `true` yapilmadikca (yalnizca testlerde acik
    # override) ai_report kullanilamaz. Gercek provider/Mock bu fazda EKLENMEZ.
    ai_report_enabled: bool = False
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

    # --- AI pipeline (`ai_report`) gercek provider secimi (Faz 3D.1) ---
    # ONEMLI: yukaridaki `ai_provider` (ai_explanation icin "none"/"remote")
    # ile KARISTIRILMAMALIDIR - TAMAMEN AYRI bir ozelliktir, bu yuzden ayri bir
    # alan adi (`ai_report_provider`) kullanilir. `"disabled"` (varsayilan):
    # hicbir provider olusturulmaz, hicbir OpenAI client/network YOKTUR -
    # `ai_report_enabled` ile TAMAMEN BAGIMSIZ bir ikinci kapidir (ikisi de
    # acik olmadan gercek provider readiness'i true OLMAZ, bkz. `ai_report_
    # provider_ready`). "openai" DISINDA bir deger EKLENMEZ (bu fazda
    # Anthropic/Gemini/fallback/model picker YOK).
    # "mock" ve "ollama" (Faz 3D.2-LOCAL): ucretli API zorunlulugu olmadan
    # gelistirme/test akisi. "mock" -> `MockAIProvider` (network YOK, tamamen
    # deterministik - otomatik pytest'in DEFAULT'u budur). "ollama" -> yerel
    # makinede calisan bir Ollama daemon'ina (bkz. `ollama_*` alanlari)
    # bagli GERCEK bir provider; yalnizca gelistiricinin ACIKCA sectigi
    # zaman kullanilir, hicbir otomatik fallback YOKTUR. Gemini bu fazda
    # EKLENMEZ.
    ai_report_provider: Literal["disabled", "mock", "openai", "ollama"] = "disabled"
    # Mock'un PRODUCTION'da (environment=="production") gercek bir AI raporu
    # GIBI sunulmasini/chip tuketmesini onlemek icin KASITLI, varsayilan
    # olarak KAPALI bir istisna kapisi (Faz 3D.2.1 madde 3) - varsayilan
    # (False) iken production'da `ai_report_provider="mock"` HEM readiness'i
    # False dondurur HEM DE `app.config_security.validate_production_secrets`
    # acikca REDDEDER (fail-closed, worker/backend HIC BASLAMAZ). Development/
    # diger (production DISI) ortamlarda bu bayragin HICBIR ETKISI YOKTUR -
    # mock zaten serbesttir.
    allow_mock_ai_provider: bool = False
    # SecretStr: repr/log/OpenAPI response'da ASLA duz metin gorunmez (bkz.
    # gorev talimati madde 4). `.get_secret_value()` YALNIZCA gercek OpenAI
    # client'i olustururken (bkz. app.services.ai_pipeline.openai_provider)
    # okunur; hicbir hata mesajina/log satirina eklenmez.
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.6-terra"
    openai_reasoning_effort: OpenAIReasoningEffort = "medium"
    # arq job timeout'undan (bkz. app.worker_constants.ARQ_JOB_TIMEOUT_SECONDS,
    # TEK dogruluk kaynagi) GUVENLI bir marj ile KISA olmalidir - bu, asagidaki
    # `_ensure_provider_timeout_below_job_timeout` alan dogrulayicisi
    # tarafindan ZORLANIR (Faz 3D.2.1 madde 1); varsayilan (120s) bu kurala
    # rahatlikla uyar.
    openai_timeout_seconds: int = Field(default=120, gt=0)
    # Pozitif VE makul bir ust sinirla sinirli (bkz. Faz 3D.2.1 madde 2) -
    # asiri buyuk bir deger, hem provider suresini/maliyetini kontrolsuz
    # buyutur hem de arq job timeout'unu (yukarida) asma riskini artirir.
    openai_max_output_tokens: int = Field(default=2000, gt=0, le=8000)

    # --- Yerel Ollama provider'i (Faz 3D.2-LOCAL / 3D.2.1) ---
    # ONEMLI: `openai_*` alanlarindan TAMAMEN AYRI, bagimsiz bir yapilandirma.
    # Varsayilan `ollama_base_url`, yalnizca loopback'e (127.0.0.1/localhost/
    # ::1) isaret eder - SSRF'e karsi, `ollama_allow_remote_host=True` acikca
    # verilmeden BASKA hicbir host kabul edilmez (bkz. `_is_allowed_ollama_host`,
    # `ai_report_provider_ready`, `OllamaProvider.__init__`).
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    # Faz 3D.2.1: 600s'lik onceki varsayilan, arq job timeout'unu (300s, bkz.
    # app.worker_constants.ARQ_JOB_TIMEOUT_SECONDS) VE stale/reaper esigini
    # (600s, bkz. app.services.ai_pipeline.retry_policy.
    # STALE_RUNNING_TIMEOUT_SECONDS) asiyor/esitliyordu - bu, "provider
    # timeout < arq job timeout < stale lease/reaper suresi" guvenlik sirasini
    # BOZUYORDU. Yeni varsayilan (240s) her iki sinirin da GUVENLI sekilde
    # ALTINDA kalir (bkz. `_ensure_provider_timeout_below_job_timeout`).
    ollama_timeout_seconds: int = Field(default=240, gt=0)
    ollama_temperature: float = Field(default=0.0, ge=0.0)
    ollama_keep_alive: str = "10m"
    # Ayni anda en fazla kac Ollama istegi calisir (bkz.
    # app.services.ai_pipeline.ollama_provider - provider seviyesinde bounded
    # `asyncio.Semaphore`). Yerel makinenin asiri yuklenmemesi icin varsayilan
    # 1'dir (ayni anda IKI model uretimi BASLAMAZ).
    ollama_max_concurrency: int = Field(default=1, gt=0)
    # Opsiyonel baglam penceresi boyutu - verilirse pozitif tam sayi olmalidir.
    ollama_num_ctx: int | None = Field(default=None, gt=0)
    # Pozitif VE makul bir ust sinirla sinirli (bkz. Faz 3D.2.1 madde 2) -
    # `options.num_predict` olarak gonderilir (bkz. OllamaProvider) VE
    # `configuration_fingerprint`e girer (semantik bir uretim ayaridir).
    ollama_max_output_tokens: int = Field(default=2000, gt=0, le=8000)
    # Varsayilan olarak yalnizca loopback host'lara izin verilir; uzak bir
    # Ollama sunucusu YALNIZCA bu bayrak acikca `True` yapilirsa kabul edilir
    # (bkz. gorev talimati madde 3 - "Kullanici acikca ayri bir guvenli ayar
    # vermeden uzak Ollama sunucusu kabul edilmesin").
    ollama_allow_remote_host: bool = False
    # Faz 3D.3A.2: Ollama'ya `/api/chat` `format` alaninda NE gonderilecegini
    # sececer - iki mod birbirini DISLAR, otomatik fallback YOKTUR:
    #   "json"        -> `format="json"` (gevsek, yalnizca "gecerli JSON uret"
    #                     kisitlamasi; sema PROMPT metninde acikca verilir,
    #                     bkz. app.services.ai_pipeline.ollama_provider).
    #                     VARSAYILAN - gercek bir yerel smoke-test kanitladi ki
    #                     (0.32.5, qwen3:8b) `format=<json_schema>` ile
    #                     gonderilen TUM gercek pipeline semalari (ic ice
    #                     $defs/$ref DUZLESTIRILMIS olsa BILE) Ollama'nin kendi
    #                     JSON-Schema->GBNF-grammar donusturucusunun urettigi
    #                     grammar'i, yine kendi grammar parser'i (llama.cpp)
    #                     PARSE EDEMEDIGI icin HTTP 400 ("failed to parse
    #                     grammar") ile REDDEDILIYOR - generation'in KENDISI
    #                     hic baslamiyor.
    #   "json_schema" -> `format=<normalize edilmis JSON schema>` (bkz.
    #                     `_inline_schema_refs`) - yalnizca `$defs`/`$ref`
    #                     duzlestirilir, ama YUKARIDAKI grammar-parser
    #                     sorunu bu haliyle HALA gozlemlenmistir; bu mod
    #                     ileride Ollama/llama.cpp surumu duzeldiginde
    #                     deneysel/opsiyonel olarak denenebilir - varsayilan
    #                     DEGILDIR.
    # Her iki modda da cevap AYNI SEKILDE `output_schema.model_validate_json`
    # ile (orijinal, ic ice Pydantic modeliyle) dogrulanir - bu ayar YALNIZCA
    # giden `format` temsilini degistirir, dogrulama sozlesmesini DEGISTIRMEZ.
    ollama_structured_output_mode: Literal["json", "json_schema"] = "json"

    # --- AI etkilesim isi haritasi (`ai_interaction_heatmap`) ------------------
    # `ai_report`tan TAMAMEN AYRI, BAGIMSIZ iki kapi (bkz. app.services.
    # module_catalog / app.services.ai_interaction_heatmap):
    #   `ai_interaction_heatmap_enabled` -> modulu sihirbaz katalogunda gosterir
    #     VE launch'a izin verir (varsayilan False -> modul gizli, launch reddedilir).
    #   `ai_interaction_heatmap_provider` -> worker'in HANGI secici (selector) ile
    #     calisacagi. "disabled" (varsayilan): hicbir selector olusturulmaz,
    #     hicbir provider/network YOKTUR (heatmap job'lari QUEUED kalir). "openai":
    #     GERCEK OpenAI Responses API (mevcut `openai_*` kimlik bilgilerini
    #     YENIDEN KULLANIR - ayri bir key gerekmez). "mock": network'suz,
    #     deterministik bir gelistirme/test secicisi; production'da (environment==
    #     "production") gercek AI GIBI kullanilmasi `allow_mock_ai_provider` ile
    #     ACIKCA izin verilmedikce ENGELLENIR (ai_report mock'uyla ayni fail-closed
    #     kural). Hicbir dal digerine OTOMATIK DUSMEZ; provider yoksa deterministik
    #     cikti sessizce "AI" gibi sunulmaz - job QUEUED/RUNNING/FAILED durumunda
    #     acikca gorunur (bkz. app.routers.reports interaction heatmap bolumu).
    ai_interaction_heatmap_enabled: bool = False
    ai_interaction_heatmap_provider: Literal["disabled", "mock", "openai"] = "disabled"

    # --- Ziyaretci / trafik analitigi (bkz. app.services.analytics, ---------------
    # app.routers.analytics, docs/security.md "Ziyaretci ve trafik analitigi") ---
    # KVKK acisindan olculu, gizlilik dostu bir sistem: HAM IP / tam user-agent /
    # token / hassas query saklanmaz, fingerprinting yapilmaz.
    #   analytics_enabled -> ana anahtar. False iken hicbir olay kaydedilmez,
    #     public ingestion ucu no-op (204) doner, hicbir analitik cerez set edilmez.
    #     Sistemi TAMAMEN kapatmak icin bu bayragi False yapmak yeterlidir.
    #   analytics_require_consent -> True iken pazarlama analitigi (page_view /
    #     visitor oturumu / visitor cerezi / edinim kaynagi) YALNIZCA istemci
    #     acikca izin (consent) bildirdiginde islenir; izin yoksa yalnizca
    #     sistemin calismasi/guvenligi icin gerekli sunucu-tarafli is olaylari
    #     (signup/login/organizasyon) pazarlama attribution'i OLMADAN tutulur.
    #     Gizlilik-oncelikli varsayilan olarak True'dur (opt-in).
    #   analytics_retention_days -> analitik satirlarinin saklama suresi (gun);
    #     bu sureyi asan kayitlar guvenli cleanup cron'uyla silinir.
    #   analytics_cookie_ttl_days -> HttpOnly `analytics_vid` (visitor) cerezinin
    #     omru (gun).
    analytics_enabled: bool = True
    analytics_require_consent: bool = True
    analytics_retention_days: int = Field(default=180, gt=0, le=3650)
    analytics_cookie_ttl_days: int = Field(default=180, gt=0, le=3650)
    # Ulke tespiti icin GUVENILEN reverse-proxy header'i (ornegin Cloudflare
    # "CF-IPCountry"). Bos (varsayilan) iken ulke alani HER ZAMAN bos birakilir -
    # IP'den ulke tespiti icin yeni/gereksiz bir ucuncu taraf servise veri
    # GONDERILMEZ.
    analytics_country_header: str = ""
    # Public ingestion ucunun IP basina sabit-pencere hiz siniri.
    analytics_ingest_rate_limit_max_events: int = Field(default=120, gt=0)
    analytics_ingest_rate_limit_window_seconds: int = Field(default=60, gt=0)

    @field_validator("openai_timeout_seconds", "ollama_timeout_seconds")
    @classmethod
    def _ensure_provider_timeout_below_job_timeout(cls, value: int, info: ValidationInfo) -> int:
        """Provider request timeout'u daima `ARQ_JOB_TIMEOUT_SECONDS - MIN_
        JOB_TIMEOUT_SAFETY_MARGIN_SECONDS`den KUCUK/ESIT olmalidir (Faz
        3D.2.1 madde 1 - "provider request timeout < arq job timeout < stale
        lease/reaper suresi"). Bu sabitler `app.worker_constants`de TEK bir
        yerde tanimlidir - burada KOPYALANMAZ."""

        from app.worker_constants import ARQ_JOB_TIMEOUT_SECONDS, MIN_JOB_TIMEOUT_SAFETY_MARGIN_SECONDS

        max_allowed = ARQ_JOB_TIMEOUT_SECONDS - MIN_JOB_TIMEOUT_SAFETY_MARGIN_SECONDS
        if value > max_allowed:
            raise ValueError(
                f"{info.field_name}={value}, arq job timeout'undan ({ARQ_JOB_TIMEOUT_SECONDS}s) "
                f"guvenli bir marjla ({MIN_JOB_TIMEOUT_SAFETY_MARGIN_SECONDS}s) kucuk/esit "
                f"olmalidir (en fazla {max_allowed}s)."
            )
        return value

    @property
    def ai_report_provider_ready(self) -> bool:
        """`ai_report` icin GERCEK/mock bir provider olusturulabilir mi?

        `ai_report_provider`e gore dallanir - hicbir dal digerine OTOMATIK
        DUSMEZ (bkz. gorev talimati madde 4, Faz 3D.2-LOCAL madde 1):
        - "disabled": daima False.
        - "mock": `ai_report_enabled=True` gerekir; EK OLARAK, `environment==
          "production"` ise `allow_mock_ai_provider=True` ACIKCA verilmedigi
          surece False doner (bkz. Faz 3D.2.1 madde 3 - mock'un production'da
          gercek bir AI raporu GIBI sessizce calismasi ENGELLENIR).
        - "openai": mevcut (Faz 3D.1) davranis - API key + model + timeout.
        - "ollama": API key GEREKMEZ; model adi bos degil, timeout/
          concurrency pozitif VE base URL izinli bir host'a (varsayilan
          yalnizca loopback) isaret etmelidir.
        """

        if not self.ai_report_enabled:
            return False
        if self.ai_report_provider == "disabled":
            return False
        if self.ai_report_provider == "mock":
            if self.environment == "production" and not self.allow_mock_ai_provider:
                return False
            return True
        if self.ai_report_provider == "openai":
            key = self.openai_api_key
            if key is None or not key.get_secret_value().strip():
                return False
            if not self.openai_model.strip():
                return False
            return self.openai_timeout_seconds > 0
        if self.ai_report_provider == "ollama":
            if not self.ollama_model.strip():
                return False
            if self.ollama_timeout_seconds <= 0 or self.ollama_max_concurrency <= 0:
                return False
            return _is_allowed_ollama_host(
                self.ollama_base_url, allow_remote_host=self.ollama_allow_remote_host
            )
        return False

    @property
    def interaction_heatmap_provider_ready(self) -> bool:
        """`ai_interaction_heatmap` icin GERCEK/mock bir secici olusturulabilir mi?

        `ai_report_provider_ready` ile AYNI fail-closed desen ama BAGIMSIZ
        bayraklar:
        - `ai_interaction_heatmap_enabled=False` -> daima False.
        - "disabled": daima False.
        - "openai": mevcut OpenAI kimlik bilgilerini yeniden kullanir (key + model
          + timeout>0). Ayri bir key GEREKMEZ.
        - "mock": `environment=="production"` ise `allow_mock_ai_provider=True`
          acikca verilmedikce False (production'da mock gercek AI gibi sunulmaz).
        """

        if not self.ai_interaction_heatmap_enabled:
            return False
        if self.ai_interaction_heatmap_provider == "disabled":
            return False
        if self.ai_interaction_heatmap_provider == "mock":
            if self.environment == "production" and not self.allow_mock_ai_provider:
                return False
            return True
        if self.ai_interaction_heatmap_provider == "openai":
            key = self.openai_api_key
            if key is None or not key.get_secret_value().strip():
                return False
            if not self.openai_model.strip():
                return False
            return self.openai_timeout_seconds > 0
        return False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_render_postgres_url(cls, value: object) -> object:
        """Render'in yonetilen Postgres URL'ini async SQLAlchemy lehcesine cevirir.

        Render `postgresql://...` dondurur; uygulamanin async engine'i ise
        acikca `postgresql+asyncpg://...` ister. Mevcut Docker/.env URL'leri
        zaten dogru lehcede oldugu icin degistirilmez.
        """

        if not isinstance(value, str):
            return value
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        return value

    @field_validator("cookie_secure", "cookie_domain", "log_format", mode="before")
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
