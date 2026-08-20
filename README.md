# Synthetix UX

**Synthetix UX**, bir web sayfasının veya yüklenen bir tasarım görselinin
kullanıcı deneyimini (UX) *sentetik* personalarla test etmeye yarayan çok
kiracılı (multi-tenant) bir **B2B SaaS platformudur**. Kullanıcı bir URL ya
da ekran görüntüsü verir; platform bunu açıklanabilir, deterministik bir
**sezgisel (heuristic)** simülasyon motoruyla analiz eder. Sonuç; skorları,
görsel katmanları (sentetik dikkat/CTA overlay'i) ve isteğe bağlı bir AI
açıklama katmanını içeren okunabilir bir rapora dönüşür (motorun kapsamı ve
sınırları için bkz. [docs/methodology.md](docs/methodology.md)).

> **Bilimsel dürüstlük uyarısı.** Bu motor **gerçek insan davranışı
> üretmez**. Tüm çıktılar "sentetik senaryo tahmini"dir ve gerçek
> kullanılabilirlik testinin, A/B testinin veya pazar araştırmasının yerini
> **almaz**. İddia sınırları için bkz.
> [docs/scientific-integrity.md](docs/scientific-integrity.md).

---

## Canlı uygulama

- **Canlı demo:** https://synthetix-ux-ily.onrender.com/
- **Depo:** https://github.com/ilayakc/synthetix-ux

Ana sayfadaki **"Canlı demoyu incele"** düğmesi, parola gerektirmeden ayrı ve
sınırlı bir demo hesabına oturum açar. Uygulama ücretsiz Render planında
barındırıldığı için servis boştayken uykuya geçer; bu nedenle ilk istek
(cold-start) birkaç on saniye sürebilir ve sayfa yanıt verene kadar geçici
olarak 503 dönebilir. Ayrıntılı sınırlamalar için bkz.
[Bilinen sınırlamalar](#bilinen-sınırlamalar).

---

## İçindekiler

- [Özellikler](#özellikler)
- [Mimari ve bileşenler](#mimari-ve-bileşenler)
- [Teknoloji yığını](#teknoloji-yığını)
- [Gereksinimler](#gereksinimler)
- [Hızlı başlangıç (Windows + Docker Desktop)](#hızlı-başlangıç-windows--docker-desktop)
- [Ortam değişkenleri](#ortam-değişkenleri)
- [Veritabanı](#veritabanı)
- [Testler](#testler)
- [URL analiz servisi (analyzer)](#url-analiz-servisi-analyzer)
- [AI katmanları](#ai-katmanları)
- [Chip, haklar ve faturalama](#chip-haklar-ve-faturalama)
- [Yönetici paneli](#yönetici-paneli)
- [Dağıtım](#dağıtım)
- [Günlükler ve durdurma](#günlükler-ve-durdurma)
- [Proje yapısı](#proje-yapısı)
- [Dokümantasyon](#dokümantasyon)
- [Bilinen sınırlamalar](#bilinen-sınırlamalar)
- [Kapsam dışı](#kapsam-dışı)
- [Lisans ve iletişim](#lisans-ve-iletişim)

---

## Özellikler

- **E-posta/parola kimlik doğrulama + oturum katmanı** — argon2id parola
  hash'leme, kısa ömürlü JWT access token + rotasyonlu opak refresh token,
  reuse (tekrar kullanım) tespiti, `HttpOnly` cookie'ler, çift-gönderim CSRF
  koruması, giriş hız sınırı ve parola sıfırlama akışı (bkz.
  [docs/architecture.md](docs/architecture.md#kimlik-doğrulama-ve-oturum)).
- **Çok kiracılı veri modeli** — organizasyon kökü, `memberships` üzerinden
  RBAC (`owner`/`admin`/`analyst`/`viewer`), token'dan türetilen zorunlu
  tenant izolasyonu.
- **Projeler ve Test Sihirbazı** — 5 adımlı sihirbaz (detaylar → tasarım
  kaynağı → personalar → analiz modülleri → özet). Tasarım kaynağı olarak
  bir URL, yüklenen bir ekran görüntüsü veya (A/B'de "Tasarım B" için) AI
  ile üretilmiş bir varyant seçilebilir.
- **Persona ön ayarları** — dağılım editörüyle (yaş, cihaz, uzmanlık vb.)
  1.000 personaya kadar sentetik kitle tanımlama.
- **Analiz modülleri kataloğu** — temel UX testi ve erişilebilirlik ön
  kontrolünün yanında Chip gerektiren gelişmiş modüller (ör. cihaz/ağ testi,
  kampanya CTA testi, sentetik dikkat tahmini, AI etkileşim ısı haritası).
- **Sentetik simülasyon motoru** — deterministik, `deterministic_seed` +
  `model_version` ile tekrarlanabilir; arka planda `arq` worker'ının
  işlettiği bir durum makinesiyle çalışır.
- **Raporlar ve görsel katmanlar** — metrik dayanakları + sentetik dikkat
  overlay'i (`semantic_region` veya OpenCV tabanlı `synthetic_visual_attention`)
  ve CTA overlay'i.
- **AI destekli açıklama / hızlı rapor özeti** — hesaplanmış metrikleri doğal
  dile çeviren isteğe bağlı katman (varsayılan: yerel/deterministik; isteğe
  bağlı OpenAI Responses API).
- **AI etkileşim ısı haritası (AI tıklama tahmini)** — hedef görev ve hedef
  kitle için, yalnızca analyzer'ın doğruladığı gerçek etkileşim adaylarından
  seçim yapan (koordinat üretmeyen) gerçek OpenAI vision tabanlı, Chip
  gerektiren isteğe bağlı modül. Gerçek tıklama veya göz takibi verisi
  değildir; varsayılan olarak kapalıdır.
- **Pasif URL analiz servisi (analyzer)** — Playwright/Chromium ile SSRF'e
  karşı korumalı, salt-okunur sayfa analizi + axe-core erişilebilirlik ön
  kontrolü.
- **Yüklenen tasarım analizi** — ekran görüntüsü yükleme, EXIF temizleme ve
  yerel/deterministik OpenCV görsel analizi (harici vision API'sine görsel
  gönderilmez).
- **Chip cüzdanı ve haklar (entitlements)** — ekle-sadece (append-only) Chip
  defteri, ücretsiz haklar, teklif (quote) hesaplama ve Chip yükleme talepleri.
- **Yönetici paneli** — Chip talepleri, AI işlemleri, denetim (audit)
  kayıtları, platform ayarları ve **Girişler ve Trafik** analitiği.
- **Girişler ve Trafik (ziyaretçi/trafik analitiği)** — yalnızca platform
  yöneticisine açık, KVKK açısından ölçülü, gizlilik dostu bir analitik ekranı:
  sayfa görüntüleme, benzersiz ziyaretçi, kayıt/giriş, şirket ve UTM/referral
  kampanya takibi + first/last-touch edinim ilişkilendirmesi. Ham IP, tam
  user-agent veya parmak izi saklanmaz; anonim ziyaretçi first-party bir çerezle
  temsil edilir ve izin (consent) mekanizması vardır (bkz.
  [docs/security.md](docs/security.md#ziyaretçi-ve-trafik-analitiği-kvkk-açısından-ölçülü)).
- **Denetim günlükleri** — ekle-sadece `audit_logs` tablosu (analitikten ayrı).
- **Yardım merkezi** — uygulama içi kapsamlı yardım/SSS.
- **Yapılandırılmış loglama** — backend + frontend genelinde structured log.

---

## Mimari ve bileşenler

Monorepo aşağıdaki servislerden oluşur (tek `compose.yaml` ile ayağa kalkar):

| Servis     | Teknoloji                              | Port  | Açıklama |
| ---------- | -------------------------------------- | ----- | -------- |
| `frontend` | React + TypeScript + Vite              | 5173  | SPA arayüz |
| `backend`  | FastAPI + SQLAlchemy (async) + asyncpg | 8000  | REST API |
| `worker`   | `arq` (backend kod tabanını paylaşır)  | —     | Simülasyon durum makinesi + arka plan görevleri |
| `analyzer` | FastAPI + Playwright/Chromium          | dahilî| Pasif URL analizi (host'a port yayınlamaz) |
| `db`       | PostgreSQL 16                          | 5432  | Veri (named volume: `pgdata`) |
| `redis`    | Redis 7                                | 6379  | Kuyruk, cache, hız sınırı |

`worker`, ayrı bir kaynak dizini değildir; `backend/` kod tabanını
(`arq app.worker.WorkerSettings`) çalıştıran ayrı bir container'dır.
`analyzer` **kasıtlı olarak** host'a port yayınlamaz; kullanıcının girdiği
keyfi bir URL'yi gerçekten ziyaret eden tek bileşendir ve yalnızca
backend/worker tarafından iç ağ üzerinden çağrılır (bkz.
[docs/security.md](docs/security.md)). Servisler, veri modeli ve tenant
sınırı için bkz. [docs/architecture.md](docs/architecture.md).

---

## Teknoloji yığını

- **Backend:** Python, FastAPI, SQLAlchemy 2 (async), asyncpg, Alembic, arq,
  Redis, Pydantic v2, PyJWT, argon2-cffi, httpx, Pillow, NumPy,
  OpenCV (headless), OpenAI SDK.
- **Frontend:** React 18, TypeScript, Vite 6, React Router, Vitest + Testing
  Library + MSW, ESLint, Prettier.
- **Analyzer:** FastAPI, Playwright/Chromium, axe-core.
- **Altyapı:** Docker Compose, PostgreSQL 16, Redis 7, Playwright E2E.

---

## Gereksinimler

- Windows 10/11
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  (WSL2 backend önerilir)

---

## Hızlı başlangıç (Windows + Docker Desktop)

PowerShell içinde proje kök dizininde:

```powershell
copy .env.example .env
docker compose config
docker compose up -d --build
docker compose ps
```

Tüm servisler `healthy`/`running` durumuna geçtikten sonra:

- Backend health (liveness): http://localhost:8000/api/health
- Backend readiness (DB + Redis durumu): http://localhost:8000/api/ready
- Frontend: http://localhost:5173
- API dokümanı (yalnızca development): http://localhost:8000/docs

---

## Ortam değişkenleri

Tüm değişkenler [.env.example](.env.example) dosyasında listelenmiş ve
açıklanmıştır. `.env` dosyası git'e eklenmez; gerçek sırlar asla
`.env.example` içine yazılmamalıdır. Başlıca gruplar:

- **Çekirdek:** `ENVIRONMENT`, `DATABASE_URL`, `REDIS_URL`,
  `CORS_ALLOWED_ORIGIN`, `ALLOWED_HOSTS`.
- **Kimlik/oturum:** `JWT_SECRET_KEY`, `ACCESS_TOKEN_TTL_SECONDS`,
  `REFRESH_TOKEN_TTL_SECONDS`, `COOKIE_SECURE`, `COOKIE_DOMAIN`.
- **Analyzer:** `ANALYZER_BASE_URL`, `ANALYZER_SHARED_TOKEN`,
  `ANALYZER_REQUEST_TIMEOUT_SECONDS`, saklama süreleri
  (`PAGE_ANALYSIS_SCREENSHOT_RETENTION_SECONDS`,
  `REPORT_LINKED_SCREENSHOT_RETENTION_SECONDS`,
  `DESIGN_ASSET_RETENTION_SECONDS`).
- **AI açıklama:** `AI_PROVIDER` (varsayılan `none`), `AI_REMOTE_*`.
- **AI rapor / OpenAI:** `AI_REPORT_ENABLED`, `AI_REPORT_PROVIDER`,
  `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`,
  `OPENAI_MAX_OUTPUT_TOKENS`.
- **AI etkileşim ısı haritası:** `AI_INTERACTION_HEATMAP_ENABLED` (varsayılan
  `false`), `AI_INTERACTION_HEATMAP_PROVIDER` (`disabled`/`mock`/`openai`,
  varsayılan `disabled`; `openai` seçilirse yukarıdaki `OPENAI_*` kimlik
  bilgilerini yeniden kullanır, ayrı bir anahtar gerekmez).
- **AI tasarım varyantı:** `IMAGE_GENERATION_PROVIDER` (varsayılan `none`).
- **Ziyaretçi/trafik analitiği:** `ANALYTICS_ENABLED` (varsayılan `true`),
  `ANALYTICS_REQUIRE_CONSENT` (varsayılan `true`; gizlilik öncelikli/opt-in),
  `ANALYTICS_RETENTION_DAYS` (varsayılan `180`), `ANALYTICS_COOKIE_TTL_DAYS`,
  `ANALYTICS_COUNTRY_HEADER` (boş bırakılırsa ülke tespiti yapılmaz). Sistemi
  tamamen kapatmak için `ANALYTICS_ENABLED=false` yeterlidir.
- **Loglama:** `LOG_LEVEL`, `LOG_FORMAT`, `LOG_EXCLUDE_PATHS`.

Production sırlarının doğrulaması (fail-closed) için bkz.
[docs/production.md](docs/production.md).

---

## Veritabanı

```powershell
docker compose exec backend alembic upgrade head     # migration'ları uygula
docker compose exec backend alembic downgrade base   # geri al
docker compose exec backend python -m app.seed       # yalnızca development
```

ER özeti, tenant sınırı ve migration/seed davranışı için bkz.
[docs/architecture.md](docs/architecture.md).

---

## Testler

Tüm test paketini (backend lint/type/test/coverage, frontend lint/type/
test/coverage/build, frontend-backend şema uyumu, izole bir stack'te E2E)
tek komutla çalıştırmak için kökten:

```powershell
./scripts/verify.ps1
```

Daha hızlı yerel döngü için bayraklar (`-SkipE2E`, `-OnlyBackend`,
`-OnlyFrontend`) ve hangi testin neyi koruduğu için bkz.
[docs/testing.md](docs/testing.md).

Ayrı ayrı çalıştırmak isterseniz:

```powershell
docker compose exec backend pytest
docker compose exec analyzer pytest
docker compose exec frontend npm run test
docker compose exec frontend npm run build
```

---

## URL analiz servisi (analyzer)

`analyzer`, kullanıcının belirttiği bir URL'yi Playwright/Chromium ile
**pasif** olarak ziyaret edip (form gönderme, giriş yapma, satın alma veya
dosya indirme yapılmaz) viewport ekran görüntüsü, başlık/heading yapısı,
görünür metin istatistikleri, bağlantı/buton/form kontrol sayıları,
yaklaşık element kutuları, temel performans zamanları, renk/kontrast
adayları ve axe-core ile **otomatik bir erişilebilirlik ön kontrolü**
(tam WCAG uygunluk sertifikası değildir) üreten ayrı bir container'dır.
Sonuç, sürümlü bir `page_feature_snapshot` olarak `backend`'e döner ve
`page_analyses` tablosunda saklanır (`GET /api/page-analyses/{id}`); ham
HTML, form değerleri, cookie veya token hiçbir zaman saklanmaz, ekran
görüntüsü ise süreli (varsayılan 24 saat) saklanıp otomatik olarak
temizlenir. SSRF tehdit modeli, DNS/redirect doğrulaması ve operasyon
limitleri için bkz. [docs/security.md](docs/security.md).

---

## AI katmanları

Birbirinden **bağımsız**, ayrı yapılandırılan AI özellikleri vardır; birinin
etkin olması diğerini otomatik açmaz. Hepsi varsayılan olarak kapalı ya da
yerel/deterministik modda çalışır (bkz. [docs/ai-policy.md](docs/ai-policy.md)):

1. **AI destekli açıklama (hızlı rapor özeti)** — zaten hesaplanmış rapor
   metriklerini doğal dile çevirir; **karar vermez ve hiçbir metriği yeniden
   hesaplamaz**. `AI_PROVIDER=none` (varsayılan) ile ağ çağrısı yapılmadan,
   yerel/deterministik bir şablon açıklaması üretilir; ürün bu haliyle
   eksiksiz çalışır. İsteğe bağlı olarak OpenAI Responses API'ye bağlanabilir.
2. **AI ile tasarım varyantı üretimi** — A/B karşılaştırmasında "Tasarım B"
   için isteğe bağlı bir görsel üretim katmanı (`IMAGE_GENERATION_PROVIDER`).
   Gerçek bir görsel üretim sağlayıcısı yapılandırılmadan kullanıcıya
   sunulmaz; hiçbir placeholder/sahte görsel "AI sonucu" gibi gösterilmez.
3. **AI etkileşim ısı haritası (AI tıklama tahmini)** — hedef görev ve hedef
   kitle için sentetik bir tıklama tahmini üretir; Chip gerektiren bir katalog
   modülüdür (`ai_interaction_heatmap`). Gerçek OpenAI provider'ı worker içinde
   çağrılır ve **yalnızca analyzer'ın doğruladığı etkileşim adaylarından seçim
   yapar; koordinat üretmez**. `AI_INTERACTION_HEATMAP_ENABLED=false`
   (varsayılan) veya `AI_INTERACTION_HEATMAP_PROVIDER=disabled` (varsayılan)
   iken modül sihirbazda görünmez, hiçbir istemci/ağ çağrısı ve ücret oluşmaz.
   Gerçek kullanıcı tıklaması veya göz takibi verisi **değildir**.

Hiçbir katman görsel karşılaştırma/analiz motorunun yerini tutmaz. Veri
işleme kuralları, denetim kaydı ve sınırlamalar için bkz.
[docs/ai-policy.md](docs/ai-policy.md).

---

## Chip, haklar ve faturalama

Ticari çerçeve (değişmez kurallar) için bkz.
[docs/product-rules.md](docs/product-rules.md):

- Yeni kaydolan her şirket 0 Chip bakiyesiyle başlar.
- En fazla 1.000 persona içeren bir proje için 1 ücretsiz temel UX testi ve
  1 ücretsiz erişilebilirlik ön kontrolü hakkı tanınır.
- Gelişmiş modüller Chip harcaması gerektirir.

Chip defteri (`chip_ledger_entries`) ekle-sadece bir tablodur; bakiye,
defter satırları toplanarak hesaplanır. Kullanıcılar teklif (quote)
hesaplayabilir ve Chip yükleme talebi oluşturabilir; talepler yönetici
panelinden değerlendirilir.

---

## Yönetici paneli

`/yonetim` altında (yalnızca yönetici oturumu): platform genel bakışı, **Girişler
ve Trafik** (ziyaretçi/trafik analitiği; `/yonetim/trafik`), Chip yükleme
talepleri, AI işlemleri, denetim (audit) kayıtları ve platform ayarları.
Demo/geliştirme için `BOOTSTRAP_ADMIN_*` ve `BOOTSTRAP_USER_*` ortam
değişkenleriyle başlangıç hesapları sağlanabilir.

---

## Dağıtım

- **Yerel / production-benzeri Docker:** ayrı, izole bir compose stack'i için
  bkz. [docs/production.md](docs/production.md) ve `compose.prod.yaml`. Bu bir
  referans kurulumdur, gerçek bir SLA garantisi değildir.
- **Render ücretsiz demo:** tek instance üzerinde nginx + React frontend +
  FastAPI backend + `arq` worker **ve** aynı container içinde loopback'te
  (`127.0.0.1:8100`) çalışan Playwright analyzer; ücretsiz PostgreSQL ve
  Redis-uyumlu Key Value ile sunum/değerlendirme amaçlı sıfır maliyetli bir
  topoloji (`render.yaml`, `Dockerfile.render-free`). Toplam **yalnızca üç
  Render kaynağı** vardır; analyzer ayrı bir web servisi değil, aynı container
  içinde çalışır (bu tasarım tercihinin gerekçesi için bkz.
  [docs/render-free.md](docs/render-free.md)). Render ücretsiz
  planı 512 MB RAM ile sınırlı olduğu için analyzer varsayılan olarak lite
  (viewport tabanlı) modda çalışır. Kurulum adımları, sorun giderme ve ücretsiz
  plan sınırları için bkz. [docs/render-free.md](docs/render-free.md). Render
  kaynakları ücretsiz olsa da OpenAI API kullanımı ayrıca ücretlidir.

---

## Günlükler ve durdurma

```powershell
docker compose logs -f backend
docker compose down
```

Veritabanı verisini de silmek isterseniz (geri alınamaz):

```powershell
docker compose down -v
```

---

## Proje yapısı

```
synthetix-ux/
├─ backend/                # FastAPI + SQLAlchemy (async); worker de bu kodu çalıştırır
│  ├─ app/
│  │  ├─ routers/          # API uç noktaları (auth, projects, simulations, reports, billing, admin, ...)
│  │  ├─ models/           # SQLAlchemy modelleri
│  │  ├─ services/         # İş mantığı (ai_pipeline, design_generation, chip_ledger, ...)
│  │  ├─ engine/           # Sezgisel (heuristic) sentetik simülasyon motoru
│  │  └─ worker.py         # arq WorkerSettings
│  └─ migrations/          # Alembic
├─ frontend/               # React + TypeScript + Vite
│  └─ src/{pages,components,api,auth,...}
├─ analyzer/               # Pasif URL analiz servisi (Playwright/Chromium)
├─ e2e/                    # Playwright uçtan uca testler
├─ deploy/                 # Render ücretsiz demo yardımcıları
├─ docs/                   # Mimari, metodoloji, güvenlik, AI politikası, ...
├─ scripts/                # verify.ps1 ve yardımcı betikler
├─ compose.yaml           # Yerel geliştirme stack'i
├─ compose.prod.yaml      # Production-benzeri izole stack
├─ render.yaml            # Render ücretsiz demo blueprint
└─ .env.example           # Tüm ortam değişkenleri
```

---

## Dokümantasyon

| Belge | İçerik |
| ----- | ------ |
| [docs/architecture.md](docs/architecture.md) | Servisler, veri modeli (ER), tenant sınırı, kimlik/oturum, RBAC |
| [docs/methodology.md](docs/methodology.md) | Sezgisel (heuristic) simülasyon motorunun girdileri, kuralları, varsayımları, sınırları |
| [docs/security.md](docs/security.md) | SSRF tehdit modeli, analyzer/görsel analiz güvenliği, saklama süreleri |
| [docs/ai-policy.md](docs/ai-policy.md) | Bağımsız AI katmanlarının kapsamı, sağlayıcı modeli, denetim |
| [docs/product-rules.md](docs/product-rules.md) | Değişmez ticari/bilimsel dürüstlük kuralları |
| [docs/scientific-integrity.md](docs/scientific-integrity.md) | Bilimsel iddia sınırları |
| [docs/testing.md](docs/testing.md) | Test paketi, `verify.ps1` bayrakları, hangi testin neyi koruduğu |
| [docs/production.md](docs/production.md) | Production-benzeri izole compose stack'i |
| [docs/render-free.md](docs/render-free.md) | Render ücretsiz demo kurulumu ve sınırları |

---

## Bilinen sınırlamalar

Bu, bir portföy/gösterim (showcase) dağıtımıdır; production SLA garantisi
değildir:

- **Ücretsiz Render planı ve cold-start.** Servis boştayken uykuya geçer;
  ilk istek birkaç on saniye sürebilir ve uygulama uyanana kadar geçici 503
  dönebilir. Sonraki istekler normal hızda yanıt verir.
- **512 MB RAM sınırı.** Ölçülen bellek kullanımı nedeniyle ağır sayfalar tek
  instance'ın belleğine sığmayabilir; bu yüzden canlı demoda analyzer
  varsayılan olarak **lite (viewport tabanlı)** modda çalışır. Hafif/orta
  ağırlıktaki sayfalar sorunsuz analiz edilir (bkz.
  [docs/render-free.md](docs/render-free.md)).
- **Bazı URL'ler analiz edilemeyebilir.** Bot korumalı, giriş duvarı arkasında,
  aşırı ağır veya erişimi engellenmiş sayfalar pasif analyzer tarafından
  okunamayabilir; bu beklenen bir sınırdır ve tüm ürünün çalışmadığı anlamına
  gelmez.
- **Sentetik sonuçlar.** Motor kalibre edilmemiş, deterministik bir sezgisel
  (heuristic) simülasyondur; **gerçek kullanıcı tıklaması, gerçek göz takibi verisi veya
  gerçek insan davranışı üretmez** ve gerçek kullanılabilirlik/A-B testinin
  yerine geçmez (bkz. [docs/scientific-integrity.md](docs/scientific-integrity.md)).
- **AI katmanları ayrıca ücretlidir.** Render kaynakları ücretsiz olsa da AI
  açıklama, AI etkileşim ısı haritası ve AI tasarım varyantı özellikleri gerçek
  bir OpenAI API anahtarı gerektirir; anahtar yapılandırılmadığında bu özellikler
  devre dışı kalır, ürünün geri kalanı çalışmaya devam eder.

---

## Kapsam dışı

<details>
<summary>Bu aşamada bilinçli olarak kapsam dışı bırakılanlar (aç/kapa)</summary>

Bu aşamada bulunmayanlar: Kafka, ChromaDB/RAG, serbest sohbet botu, ödeme
sağlayıcısı entegrasyonu ve **gerçek (kalibre edilmiş) bir simülasyon
motoru** — mevcut sezgisel (heuristic) motor kalibre edilmemiştir ve gerçek
insan davranışı üretmez (bkz. [docs/methodology.md](docs/methodology.md)
"Kalibrasyon planı"). Kimlik doğrulama tarafında Google OAuth/SSO, davet
e-postası ve gerçek parola sıfırlama e-posta gönderimi altyapı olarak
hazırdır ancak üretim e-posta sağlayıcısı bağlı değildir. AI katmanları
varsayılan olarak harici bir sağlayıcıya bağlı değildir; hiçbir sentetik
sonuç gerçek insan kullanıcı davranışı olarak sunulamaz.

</details>

---

## Lisans ve iletişim

Bu depo bir portföy/gösterim projesi olarak paylaşılmaktadır. Ayrı bir açık
kaynak lisansı (ör. LICENSE dosyası) tanımlanmamıştır; kod, inceleme ve
değerlendirme amacıyla yayımlanmıştır. Kullanım veya işbirliği için lütfen
iletişime geçin.

- **GitHub:** [github.com/ilayakc](https://github.com/ilayakc)
- **LinkedIn:** [linkedin.com/in/ilayda-akça](https://www.linkedin.com/in/ilayda-ak%C3%A7a-92291b3ab/)
