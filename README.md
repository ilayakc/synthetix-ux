# Synthetix UX

Çok kiracılı (multi-tenant) bir **B2B SaaS sentetik UX test platformu**.
Kullanıcılar bir web sayfası URL'si ya da yükledikleri bir tasarım ekran
görüntüsü üzerinden persona tabanlı sentetik simülasyonlar çalıştırır;
platform bunları **açıklanabilir, deterministik, kalibre edilmemiş bir
heuristic simülasyon motoruyla** (bkz. [docs/methodology.md](docs/methodology.md))
skorlar, görsel katmanlarla (sentetik dikkat/CTA overlay'i) raporlar ve
isteğe bağlı bir AI açıklama/raporlama katmanıyla doğal dile çevirir.

> **Bilimsel dürüstlük uyarısı.** Bu motor **gerçek insan davranışı
> üretmez**. Tüm çıktılar "sentetik senaryo tahmini"dir ve gerçek
> kullanılabilirlik testinin, A/B testinin veya pazar araştırmasının yerini
> **almaz**. İddia sınırları için bkz.
> [docs/scientific-integrity.md](docs/scientific-integrity.md).

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
- [Kapsam dışı](#kapsam-dışı)

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
  kampanya CTA testi, sentetik dikkat tahmini).
- **Sentetik simülasyon motoru** — deterministik, `deterministic_seed` +
  `model_version` ile tekrarlanabilir; arka planda `arq` worker'ının
  işlettiği bir durum makinesiyle çalışır.
- **Raporlar ve görsel katmanlar** — metrik dayanakları + sentetik dikkat
  overlay'i (`semantic_region` veya OpenCV tabanlı `synthetic_visual_attention`)
  ve CTA overlay'i.
- **AI destekli açıklama / hızlı rapor özeti** — hesaplanmış metrikleri doğal
  dile çeviren isteğe bağlı katman (varsayılan: yerel/deterministik; isteğe
  bağlı OpenAI Responses API).
- **Pasif URL analiz servisi (analyzer)** — Playwright/Chromium ile SSRF'e
  karşı korumalı, salt-okunur sayfa analizi + axe-core erişilebilirlik ön
  kontrolü.
- **Yüklenen tasarım analizi** — ekran görüntüsü yükleme, EXIF temizleme ve
  yerel/deterministik OpenCV görsel analizi (harici vision API'sine görsel
  gönderilmez).
- **Chip cüzdanı ve haklar (entitlements)** — ekle-sadece (append-only) Chip
  defteri, ücretsiz haklar, teklif (quote) hesaplama ve Chip yükleme talepleri.
- **Yönetici paneli** — Chip talepleri, AI işlemleri, denetim (audit)
  kayıtları ve platform ayarları.
- **Denetim günlükleri** — ekle-sadece `audit_logs` tablosu.
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
- **AI tasarım varyantı:** `IMAGE_GENERATION_PROVIDER` (varsayılan `none`).
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

İki **bağımsız**, ayrı yapılandırılan AI özelliği vardır; birinin etkin
olması diğerini otomatik açmaz (bkz. [docs/ai-policy.md](docs/ai-policy.md)):

1. **AI destekli açıklama (hızlı rapor özeti)** — zaten hesaplanmış rapor
   metriklerini doğal dile çevirir; **karar vermez ve hiçbir metriği yeniden
   hesaplamaz**. `AI_PROVIDER=none` (varsayılan) ile ağ çağrısı yapılmadan,
   yerel/deterministik bir şablon açıklaması üretilir; ürün bu haliyle
   eksiksiz çalışır. İsteğe bağlı olarak OpenAI Responses API'ye bağlanabilir.
2. **AI ile tasarım varyantı üretimi** — A/B karşılaştırmasında "Tasarım B"
   için isteğe bağlı bir görsel üretim katmanı (`IMAGE_GENERATION_PROVIDER`).
   Gerçek bir görsel üretim sağlayıcısı yapılandırılmadan kullanıcıya
   sunulmaz; hiçbir placeholder/sahte görsel "AI sonucu" gibi gösterilmez.

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

`/yonetim` altında (yalnızca yönetici oturumu): platform genel bakışı, Chip
yükleme talepleri, AI işlemleri, denetim (audit) kayıtları ve platform
ayarları. Demo/geliştirme için `BOOTSTRAP_ADMIN_*` ve `BOOTSTRAP_USER_*`
ortam değişkenleriyle başlangıç hesapları sağlanabilir.

---

## Dağıtım

- **Yerel / production-benzeri Docker:** ayrı, izole bir compose stack'i için
  bkz. [docs/production.md](docs/production.md) ve `compose.prod.yaml`. Bu bir
  referans kurulumdur, gerçek bir SLA garantisi değildir.
- **Render ücretsiz demo:** tek instance üzerinde nginx + React frontend +
  FastAPI backend + `arq` worker, ayrı bir token korumalı analyzer, ücretsiz
  PostgreSQL ve Redis-uyumlu Key Value ile sunum/değerlendirme amaçlı sıfır
  maliyetli bir topoloji (`render.yaml`, `Dockerfile.render-free`). Kurulum
  adımları ve ücretsiz plan sınırları için bkz.
  [docs/render-free.md](docs/render-free.md). Render kaynakları ücretsiz olsa
  da OpenAI API kullanımı ayrıca ücretlidir.

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
│  │  ├─ engine/           # Heuristic sentetik simülasyon motoru
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
| [docs/methodology.md](docs/methodology.md) | Heuristic simülasyon motorunun girdileri, kuralları, varsayımları, sınırları |
| [docs/security.md](docs/security.md) | SSRF tehdit modeli, analyzer/görsel analiz güvenliği, saklama süreleri |
| [docs/ai-policy.md](docs/ai-policy.md) | İki bağımsız AI katmanının kapsamı, sağlayıcı modeli, denetim |
| [docs/product-rules.md](docs/product-rules.md) | Değişmez ticari/bilimsel dürüstlük kuralları |
| [docs/scientific-integrity.md](docs/scientific-integrity.md) | Bilimsel iddia sınırları |
| [docs/testing.md](docs/testing.md) | Test paketi, `verify.ps1` bayrakları, hangi testin neyi koruduğu |
| [docs/production.md](docs/production.md) | Production-benzeri izole compose stack'i |
| [docs/render-free.md](docs/render-free.md) | Render ücretsiz demo kurulumu ve sınırları |

---

## Kapsam dışı

Bu aşamada bulunmayanlar: Kafka, ChromaDB/RAG, serbest sohbet botu, ödeme
sağlayıcısı entegrasyonu ve **gerçek (kalibre edilmiş) bir simülasyon
motoru** — mevcut heuristic motor kalibre edilmemiştir ve gerçek insan
davranışı üretmez (bkz. [docs/methodology.md](docs/methodology.md)
"Kalibrasyon planı"). Kimlik doğrulama tarafında Google OAuth/SSO, davet
e-postası ve gerçek parola sıfırlama e-posta gönderimi altyapı olarak
hazırdır ancak üretim e-posta sağlayıcısı bağlı değildir. AI katmanları
varsayılan olarak harici bir sağlayıcıya bağlı değildir; hiçbir sentetik
sonuç gerçek insan kullanıcı davranışı olarak sunulamaz.
