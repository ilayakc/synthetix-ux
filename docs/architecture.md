# Mimari Notları

## Servisler

Monorepo altı servisten oluşur: `frontend` (React + TypeScript + Vite),
`backend` (FastAPI), `worker` (arka plan görev işleyici), `analyzer`
(Playwright/Chromium tabanlı, SSRF'e karşı korumalı pasif URL analiz
servisi — bkz. [docs/security.md](security.md)), `db` (PostgreSQL) ve
`redis` (kuyruk/cache, oturum hız sınırı). PostgreSQL veri modeli, Alembic
migration'ları, temel veri erişim altyapısı (async SQLAlchemy 2
session/repository), e-posta/parola tabanlı kimlik doğrulama + oturum
katmanı (bkz. "Kimlik doğrulama ve oturum" bölümü), CRUD endpoint'leri,
ücretsiz hak/Chip rezervasyon-tüketim akışı, kalibre edilmemiş,
deterministik bir heuristic sentetik simülasyon motoru (bkz.
[docs/methodology.md](methodology.md)) ve `analyzer` üzerinden çalışan,
sürümlü bir `page_feature_snapshot` üreten gerçek (fakat pasif) sayfa
analizi hazırdır. `network_device_test` gelişmiş modülü (bkz.
[docs/methodology.md](methodology.md) "Gelişmiş moduller") artık
`analyzer`'ı gerçek cihaz/ağ profili ölçümü için gerçekten tüketir
(`app.services.device_network_analysis` → `analyzer/app/browser.py:
analyze_device_network`); `campaign_cta_test` ve
`synthetic_attention_estimate` modülleri ise (baseline motoruyla aynı
şekilde) sentetik `page_feature_snapshot` fixture'ı üzerinden çalışır,
analyzer'a gitmez. Gerçek (kalibre edilmiş) bir simülasyon motoru ve
LLM/AI sağlayıcı entegrasyonu (AI destekli açıklama katmanı hariç, bkz.
`app.services.ai_explanation`) bu aşamanın kapsamı dışındadır.

Ayrıca, kullanıcının doğrudan bir tasarım ekran görüntüsü **yükleyebildiği**
bağımsız bir altyapı (`app.services.design_assets`/`app.routers.design_assets`)
mevcuttur; tehdit modeli ve limitler için bkz.
[docs/security.md](security.md#güvenlik-yüklenen-tasarım-ekran-görüntüleri-design-assets).
İkili veri, `page_analyses` ile aynı desende Postgres `LargeBinary`'de
saklanır — ayrı bir obje depolama (MinIO/S3) servisi bu aşamada **yoktur**,
bu bilinçli bir MVP kararıdır. Yeni Test sihirbazının 2. adımı (Tasarım
Kaynağı, bkz. `frontend/src/pages/wizard/DesignSourcePicker.tsx`), hem
"mevcut site: temel UX testi" türünde tek bir tasarım kaynağı için, hem de
A/B karşılaştırmasında **her iki tarafta da bağımsız olarak** (`current_*`/
"Tasarım A" ve `new_*`/"Tasarım B", bkz.
`frontend/src/pages/wizard/DesignBSourcePicker.tsx`), kullanıcının bir URL
yerine yüklediği bir `DesignAsset`'i (`current_source_type`/`new_source_type
="screenshot"` + ilgili `*_design_asset_id`, bkz. `app.services.test_wizard`)
taslağa kaydedebilmesini sağlar; bu alanlar yalnızca aynı organizasyona ait,
aktif, ikili verisi hâlâ mevcut ve saklama süresi dolmamış bir asset'i kabul
eder (`app.services.test_wizard.validate_screenshot_asset_ownership`). A/B
karşılaştırmasında "Tasarım B" tarafı ayrıca AI ile Tasarım A'dan üretilen
bir varyant da olabilir (`new_source_type="ai_generated"` +
`new_design_asset_id` + `new_ai_generation_id`, bkz.
`app.services.design_generation` ve [docs/ai-policy.md](ai-policy.md) "AI
ile Tasarım Varyantı Üretimi").

**Paket 4 Final itibarıyla bu kaynaklarla gerçek test başlatılabilir**
(önceki, artık kaldırılmış `SCREENSHOT_LAUNCH_BLOCKED_MESSAGE`/
`AB_VISUAL_SOURCE_LAUNCH_BLOCKED_MESSAGE` engeli — bkz. `app.services
.test_wizard._revalidate_launch_sources`): `launch_draft`, her tarafın
kaynağını (URL/screenshot/AI) launch anında bağımsız olarak yeniden
doğrular (asset silinmiş/expired/cross-tenant veya kabul edilmemiş bir AI
işi ise hiçbir side effect üretmeden 400 döner), ardından her varyant için
sunucu tarafında bir `PageAnalysis` capture'i oluşturur:

- URL kaynağı → gerçek `analyzer` (Playwright/DOM) analizi,
  `feature_source="dom"`.
- Screenshot/AI (DesignAsset) kaynağı → `analyzer`'a HİÇBİR istek gitmez;
  tamamen yerel/deterministik OpenCV analizi (bkz.
  [docs/security.md](security.md#güvenlik-ekran-görüntüsü-yerel-görsel-analizi-paket-4c4d)),
  `feature_source="visual_heuristic"`.

Motorun girdi sözleşmesi de buna göre genelleştirilmiştir: `input_snapshot
["url"]` yalnızca GERÇEK bir URL varsa doludur; DesignAsset kaynaklarında
`None`'dur ve motorun "boş olmayan kimlik" gereksinimi ayrı, açık bir
`input_snapshot["source_reference"]` alanıyla (ör. `design-asset:<id>`,
skorlamaya hiç girmez) karşılanır — bkz. `app.engine.baseline
.run_baseline_simulation`, `app.engine.advanced_modules
._require_source_identity`. Erişilebilirlik ön kontrolü hâlâ yalnızca URL
kabul eder (DOM/sayfa yapısı gerektirir).

### Rapor görsel katmanları: sentetik dikkat overlay'i ve CTA overlay'i

`GET /api/reports/{id}` yanıtındaki `heatmap` alanı iki YAPISAL OLARAK
FARKLI `overlay_kind` değeri taşıyabilir:

- `"semantic_region"` (`feature_source="dom"`) — URL/DOM kaynağı, 5 sabit
  isimli bölge (`ust_navigasyon`/`hero_baslik`/`birincil_cta`/`govde_metni`/
  `alt_bilgi`), `regions`/percent-koordinat sözleşmesi (değişmedi).
- `"synthetic_visual_attention"` (`feature_source="visual_heuristic"`) —
  screenshot/AI kaynağı, GERÇEK OpenCV 8×6 piksel-tabanlı grid
  (`visual_cells`, fraksiyon [0,1] x/y/w/h/intensity) — `PageAnalysis
  .features.synthetic_attention_estimate.cells`'ten doğrudan okunur, hiçbir
  modül seçimine bağlı DEĞİLDİR (bu veri PageAnalysis işlenirken her zaman
  üretilir). Modül seçilmiş DOM tarafındaki 5-bölge heuristiği burada ASLA
  kullanılmaz.

Ayrıca `cta_overlay` alanı, `dom_interactive_candidate`/
`visual_cta_candidate`/`user_confirmed_cta` kutularını tek, normalize
edilmiş (fraksiyon [0,1]) bir listede döner; kullanıcı onaylı bir CTA bir
adayın onaylanmasından geldiyse (`selection_source="candidate_confirmation"`)
o adayın index'i (`source_candidate_index`) listeden dedupe edilir — aynı
kutu iki kez çizilmez. Her iki overlay de `app.routers.reports
._build_heatmap`/`_build_cta_overlay` tarafından, `Report.content`
üzerinden salt-okunur türetilir; hiçbir yeniden hesaplama/model çağrısı
yapılmaz.

Saklama: bkz. [docs/security.md](security.md#paket-4-final-hardening-rapor-bağlı-snapshot-saklama-süresi)
"Paket 4 Final Hardening: rapor-bağlı snapshot saklama süresi".

## Veri modeli (ER özeti) ve tenant sınırı

Platform çok kiracılı (multi-tenant) bir B2B SaaS'tir. Tüm kimlikler UUID,
tüm zaman damgaları UTC'dir (`created_at`/`updated_at`, `TIMESTAMP WITH TIME
ZONE`).

```
organizations 1──* memberships *──1 users
users         1──* refresh_tokens
organizations 1──* projects
projects      1──* test_definitions
test_definitions 1──* test_variants
organizations 1──* persona_presets
test_variants 1──* simulation_runs *──0..1 persona_presets
simulation_runs 1──* reports
organizations 1──* entitlements
organizations 1──* chip_ledger_entries
organizations 1──* audit_logs *──0..1 users (actor)
```

**Tenant sınırı:** `organizations` kiracı (tenant) köküdür. `users` global bir
kimliktir (e-posta genelinde benzersizdir, case-insensitive; `email_normalized`
alanı üzerinden) ve bir organizasyona `memberships` (rol tasıyan
çok-a-çok ilişki) üzerinden bağlanır — bu, ileride bir kullanıcının birden
fazla organizasyona üye olabilmesini mümkün kılar. Kiracıya ait diğer tüm
tablolar (`projects`, `test_definitions`, `test_variants`, `persona_presets`,
`simulation_runs`, `reports`, `entitlements`, `chip_ledger_entries`,
`audit_logs`) doğrudan bir `organization_id` (NOT NULL, indekslenmiş, `ON
DELETE CASCADE`) taşır; bir organizasyon silindiğinde tüm verisi kademeli
olarak silinir.

Sorgu katmanında tenant izolasyonu artik hem şema hem de uygulama
düzeyinde zorunludur: `app.dependencies.get_organization_id`, organizasyon
kimliğini istemcinin gönderdiği herhangi bir header/body alanından değil,
yalnızca sunucu tarafında imzalanmış access token'dan (`Principal.
organization_id`) türetir. Böylece bir istemci `X-Organization-Id` gibi bir
başlığı değiştirerek başka bir organizasyonun verisine erişemez (bkz.
"Kimlik doğrulama ve oturum"); bu dependency'yi kullanan tüm router'lar
(`app.routers.billing` dahil) değişmeden aynı garantiden yararlanır.

`simulation_runs`, deterministik/tekrarlanabilir çalıştırmalar için
`deterministic_seed` ve `model_version` alanlarını zorunlu tutar;
`calibration_status` varsayılanı `uncalibrated`'dır ve girdi anlık görüntüsü
`input_snapshot` (JSON) olarak saklanır. `chip_ledger_entries` ve
`audit_logs` ekle-sadece (append-only) tablolardır: `updated_at` alanları
yoktur ve güncel bir bakiye hücresi tutulmaz — bakiye, defter satırları
toplanarak hesaplanır (hesaplama mantığı sonraki bir aşamaya bırakılmıştır).

## Migration ve seed

Migration'lar Alembic ile (`backend/migrations/`) async SQLAlchemy 2 motoru
üzerinden çalışır; bağlantı adresi hard-code edilmez, her ortamda
`DATABASE_URL` ortam değişkeninden (`app.config.settings` üzerinden) okunur.
`docker compose exec backend alembic upgrade head` ile uygulanır.

`python -m app.seed`, yalnızca `ENVIRONMENT=development` iken çalışan, tekrar
çalıştırılabilir (idempotent) küçük bir geliştirici kolaylığıdır; üretimde
otomatik olarak tetiklenmez ve `ENVIRONMENT != development` durumunda hata
verip çıkar.

## Arka plan kuyruğu seçimi: Celery yerine arq

Backend zaten `asyncio` tabanlı (FastAPI + SQLAlchemy async + `asyncpg`) bir
altyapı üzerine kuruludur. Celery, kökeni itibarıyla senkron bir işçi modeline
dayanır; asyncio ile birlikte kullanmak için ekstra köprü kütüphaneleri (ör.
`celery[asyncio]` deneysel desteği veya ayrı thread/event loop yönetimi)
gerekir ve bu da basit bir "ping" görevi için bile gereksiz karmaşıklık ve
bağımlılık (kombu, billiard, ayrı bir sonuç backend'i vb.) katar. `arq`
("async Redis queue") ise doğrudan `asyncio` ve Redis üzerine yazılmıştır: aynı
`redis.asyncio` istemcisini ve aynı `app/` kod tabanını backend ile birebir
paylaşır, ekstra broker soyutlaması gerektirmez ve konfigürasyonu tek bir
`WorkerSettings` sınıfından ibarettir.

Worker, Redis kuyruğuna bağlanabildiğini kanıtlayan zararsız bir
`ping_redis` görevinin yanı sıra, sentetik simülasyon motorunun durum
makinesini de işletir (bkz. `app.services.simulation_worker` ve
[docs/methodology.md](methodology.md)): `process_queued_simulations`
cron'u (~3 saniyede bir) bekleyen `simulation_runs` satırlarını
`SELECT ... FOR UPDATE SKIP LOCKED` ile alıp heuristic motoru
(`app.engine.baseline`) çalıştırır; `reap_stale_simulations` cron'u
(15 saniyede bir) worker çökmesi nedeniyle "running"de takılı kalan işleri
kurtarır. Bu, gerçek zamanlı bir kuyruk tüketicisi değil, basit ve
gözlemlenebilir bir polling cron'udur. Docker healthcheck'i worker'ın
Redis bağlantısını kontrol ederek "healthy" durumuna geçmesini sağlar.
İleride iş kuyruğu büyüdükçe (ör. yeniden deneme politikaları, öncelik
kuyrukları) `arq`'nin sınırları zorlanırsa Celery'ye geçiş, aynı Redis
altyapısı korunarak yapılabilir; bu aşamada ise sadelik ve backend ile
ortak kod tabanı önceliklidir.

## Kimlik doğrulama ve oturum

E-posta + parola ile kayıt/giriş, iki token türü üzerine kuruludur:

- **Access token**: kısa ömürlü (`ACCESS_TOKEN_TTL_SECONDS`, varsayılan 15
  dakika), durum tutmayan (stateless) bir JWT (`app.security`). İçinde
  `sub` (user id), `org` (organization id) ve `role` bulunur; her istekte
  veritabanına gitmeden doğrulanır. Organizasyon bağlamı (`organization_id`)
  **yalnızca** bu imzalı token'dan türetilir (`app.dependencies.
  get_organization_id`) — istemcinin gönderdiği hiçbir header/body alanına
  güvenilmez; bu, tenant izolasyonunun uygulama katmanındaki temelidir.
- **Refresh token**: uzun ömürlü (`REFRESH_TOKEN_TTL_SECONDS`, varsayılan 30
  gün), JWT değil, yüksek entropili opak bir dizgedir
  (`secrets.token_urlsafe`). Veritabanında (`refresh_tokens` tablosu) yalnızca
  sha256 hash'i saklanır; bir DB sızıntısı tek başına oturumları ele
  geçirmeye yetmez.

Parolalar argon2id (memory-hard) ile hashlenir (`app.security.hash_password`,
`argon2-cffi`); ne düz metin parola ne de token değeri hiçbir yerde loglanır
veya saklanır. Kullanıcı var/yok ayrımını sızdırmamak için hem "e-posta
bulunamadı" hem de "parola yanlış" durumları aynı genel hatayı
(`E-posta veya parola hatalı`) döndürür; kullanıcı bulunamadığında bile sabit
maliyetli bir hash doğrulaması çalıştırılarak zamanlama yan kanalı (timing
side-channel) ile kullanıcı varlığının sızdırılması engellenir.

### Refresh token rotasyonu ve tekrar kullanım (reuse) tespiti

Her `/api/auth/refresh` çağrısı sunulan refresh tokeni **döndürür
(rotate)**: eski token `revoked_at` + `replaced_by_id` ile iptal edilir ve
aynı rotasyon zincirini (`session_id`) taşıyan yeni bir satır oluşturulur.
Zaten iptal edilmiş (dolayısıyla daha önce rotasyona uğramış) bir token
tekrar sunulursa, bu bir refresh token çalınması/tekrar kullanımı (reuse)
belirtisi sayılır ve o oturumun **tüm zinciri** (`session_id`) güvenlik
önlemi olarak derhal iptal edilir (bkz. `app.services.auth.
rotate_refresh_token`). `/api/auth/logout` da aynı şekilde tüm zinciri iptal
eder.

### Cookie'ler ve CSRF

Access/refresh token'lar tarayıcıda **localStorage'da değil**, `HttpOnly`
cookie'lerde tutulur (XSS ile token hırsızlığına karşı): `access_token`
(path `/`), `refresh_token` (path `/api/auth` — yalnızca refresh/logout
isteklerinde gönderilir). Ayrıca `HttpOnly` **olmayan** bir `csrf_token`
cookie'si, çift-gönderim (double-submit) CSRF deseni için kullanılır:
frontend bu cookie'nin değerini okuyup her durum değiştiren (refresh/logout
ve ileride eklenecek diğer yazma) isteğinde `X-CSRF-Token` header'ı olarak
gönderir; sunucu ikisinin eşleştiğini doğrular (`app.dependencies.
verify_csrf`). Saldırgan bir site, çapraz-orijin bir istekte bu cookie
değerini JavaScript ile okuyup header'a ekleyemeyeceği için (aynı-orijin
politikası) bu istek reddedilir.

Cookie bayrakları ortama göre değişir (`app.cookies`, `app.config.Settings.
resolved_cookie_secure`):

- **Yerel geliştirme** (`ENVIRONMENT=development`, http://localhost):
  `Secure=False`, `SameSite=Lax`. Tarayıcılar `Secure` olmayan cookie'lerde
  `SameSite=None`'a izin vermediği için `Lax` kullanılır; frontend/backend
  aynı üst düzey site (localhost) altında farklı portlarda çalıştığından bu
  yeterlidir.
- **Üretim/staging** (`ENVIRONMENT != development`): `Secure=True` zorunludur
  (https arkasında); frontend ve backend farklı origin'lerdeyse
  `SameSite=None` gerekir (bu, `Secure=True` ile birlikte otomatik seçilir).
  Gerekirse `COOKIE_DOMAIN` ortam değişkeniyle üst alan adı paylaşılabilir.

CORS yalnızca tek, yapılandırılmış bir frontend origin'ine izin verir
(`CORS_ALLOWED_ORIGIN`) ve `allow_credentials=True` ile çalışır (cookie
tabanlı kimlik doğrulama için zorunlu); bu ayarla `allow_origins=["*"]`
tarayıcılar tarafından zaten reddedilir.

### Parola sıfırlama

`/api/auth/password-reset/request` ve `/api/auth/password-reset/confirm`,
kısa ömürlü (`PASSWORD_RESET_TOKEN_TTL_SECONDS`, varsayılan 1 saat), tek
kullanımlık, sha256 ile hash'lenmiş bir token (`password_reset_tokens`
tablosu) üzerine kuruludur. Bu aşamada gerçek bir e-posta sağlayıcısı
**bağlı değildir**: `request` uç noktası, e-posta kayıtlı olsun ya da
olmasın (kullanıcı varlığını sızdırmamak için) her zaman aynı genel mesajı
döner; yalnızca `ENVIRONMENT=development` iken üretilen ham token doğrudan
yanıtta (`dev_reset_token`) döndürülür, böylece yerel geliştirmede gerçek bir
e-posta göndermeden akış uçtan uca test edilebilir. Üretimde bu alan her
zaman `null`'dır ve token yalnızca veritabanında saklanır; gerçek gönderim
(e-posta sağlayıcısı entegrasyonu) sonraki bir aşamaya bırakılmıştır.
`confirm`, parolayı değiştirdikten sonra güvenlik amacıyla kullanıcının tüm
refresh oturumlarını iptal eder ve isteği yapan tarayıcıdaki auth
cookie'lerini de temizler.

### Giriş denemesi hız sınırı

`/api/auth/login`, Redis üzerinde IP+e-posta anahtarlı sabit-pencere bir hız
sınırına tabidir (`app.services.rate_limit`): varsayılan olarak 5 başarısız
deneme sonrası hesap 15 dakika kilitlenir. Kilitliyken hem yanlış hem doğru
parolayla yapılan denemeler aynı `429` yanıtını alır; bu, kullanıcı
varlığının hız sınırı davranışından sızmasını da engeller.

## Roller ve yetkiler (RBAC)

Her `memberships` satırı bir rol taşır; roller artan yetki sırasına göre:

| Rol      | Açıklama                                          |
| -------- | -------------------------------------------------- |
| `owner`  | Organizasyonu ilk oluşturan üye; tam yetki.        |
| `admin`  | Organizasyon yönetimi (üye/rol yönetimi vb.).       |
| `analyst`| Test/simülasyon/rapor oluşturma ve Chip harcama.    |
| `viewer` | Yalnızca okuma (raporları, kullanım özetini görme). |

Temel yetki matrisi (bu aşamada var olan/planlanan başlıca eylemler için):

| Eylem                                   | viewer | analyst | admin | owner |
| ---------------------------------------- | :----: | :-----: | :---: | :---: |
| Kullanım özeti / Chip defterini görüntüle |   ✅   |   ✅    |  ✅   |  ✅   |
| Teklif (quote) hesapla                   |   ❌   |   ✅    |  ✅   |  ✅   |
| Proje/test/simülasyon oluştur (ileride)  |   ❌   |   ✅    |  ✅   |  ✅   |
| Organizasyon üyelerini yönet (ileride)   |   ❌   |   ❌    |  ✅   |  ✅   |
| Organizasyonu sil / faturalama (ileride) |   ❌   |   ❌    |  ❌   |  ✅   |

Uygulama mekanizması `app.dependencies.require_roles(*roles)` dependency
üreticisidir; bir router, ilgili endpoint'e `Depends(require_roles("owner",
"admin"))` ekleyerek bu matrisi zorlayabilir. Bu aşamada kayıt sırasında
oluşturulan tek rol `owner`'dır ve mevcut endpoint'lerin (billing) hiçbiri
rol bazlı bir kısıtlama gerektirmez (tüm üyeler kendi organizasyonlarının
kullanım/Chip özetini görebilir); matris, üye yönetimi ve yazma ağırlıklı
endpoint'ler eklendikçe ilgili router'larda uygulanacaktır.
