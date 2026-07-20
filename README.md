# Synthetix UX

B2B SaaS sentetik UX test platformu için proje iskeleti ve yerel geliştirme
ortamı. Altyapı, PostgreSQL veri modeli/migration'ları, temel veri erişimi,
e-posta/parola tabanlı kimlik doğrulama + oturum katmanı (bkz.
[docs/architecture.md](docs/architecture.md#kimlik-doğrulama-ve-oturum)),
ücretsiz hak/Chip rezervasyon akışı ve **açıklanabilir, deterministik,
kalibre edilmemiş bir heuristic sentetik simülasyon motoru** (bkz.
[docs/methodology.md](docs/methodology.md)) hazırdır. Bu motor gerçek
insan davranışı üretmez; tüm sonuçlar "sentetik senaryo tahmini"dir (bkz.
[docs/scientific-integrity.md](docs/scientific-integrity.md)).

## Bileşenler

- `frontend/` — React + TypeScript + Vite (port 5173)
- `backend/` — FastAPI + SQLAlchemy (async) + asyncpg (port 8000)
- `worker/` — backend ile aynı kod tabanını kullanan `arq` işçisi (bkz.
  [docs/architecture.md](docs/architecture.md))
- `analyzer/` — ayrı bir container'da çalışan, Playwright/Chromium tabanlı,
  SSRF'e karşı korumalı **pasif** URL/sayfa analiz servisi (host'a port
  yayınlamaz; yalnızca backend/worker tarafından iç ağ üzerinden çağrılır —
  bkz. [docs/security.md](docs/security.md))
- `db` — PostgreSQL (named volume: `pgdata`)
- `redis` — Redis

## Gereksinimler

- Windows 10/11
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (WSL2
  backend önerilir)

## Kurulum (Windows + Docker Desktop)

PowerShell içinde proje kök dizininde:

```powershell
copy .env.example .env
docker compose config
docker compose up -d --build
docker compose ps
```

Tüm servisler `healthy`/`running` durumuna geçtikten sonra:

- Backend health: http://localhost:8000/api/health
- Backend readiness (DB + Redis durumu): http://localhost:8000/api/ready
- Frontend: http://localhost:5173

## Testler

Tüm test paketini (backend lint/type/test/coverage, frontend lint/type/
test/coverage/build, frontend-backend şema uyumu, izole bir stack'te E2E)
tek komutla çalıştırmak için kökten:

```powershell
./scripts/verify.ps1
```

Hangi testin neyi koruduğu ve daha hızlı bir yerel döngü için bayraklar
(`-SkipE2E`, `-OnlyBackend`, `-OnlyFrontend`) için bkz.
[docs/testing.md](docs/testing.md).

Ayrı ayrı çalıştırmak isterseniz:

```powershell
docker compose exec backend pytest
docker compose exec analyzer pytest
docker compose exec frontend npm run test
docker compose exec frontend npm run build
```

## Günlükler ve durdurma

```powershell
docker compose logs -f backend
docker compose down
```

Veritabanı verisini de silmek isterseniz (geri alınamaz):

```powershell
docker compose down -v
```

## Ortam değişkenleri

Tüm değişkenler [.env.example](.env.example) dosyasında listelenmiştir.
`.env` dosyası git'e eklenmez; gerçek sırlar asla `.env.example` içine
yazılmamalıdır.

## Veritabanı

```powershell
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade base
docker compose exec backend python -m app.seed   # yalnizca development
```

Bkz. [docs/architecture.md](docs/architecture.md) için ER özeti ve tenant
sınırı.

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

## Kapsam dışı (bu aşamada yok)

Kafka, ChromaDB, herhangi bir LLM/AI sağlayıcı entegrasyonu, ödeme
sağlayıcısı entegrasyonu, gerçek (kalibre edilmiş) bir simülasyon motoru
bu aşamanın kapsamı dışındadır — mevcut heuristic motor kalibre
edilmemiştir ve gerçek insan davranışı üretmez (bkz.
[docs/methodology.md](docs/methodology.md) "Kalibrasyon planı").
`analyzer`'in ürettiği gerçek sayfa analizinin, Prompt 7 motorunun
kullandığı sentetik `page_feature_snapshot` fixture'ının (bkz.
`backend/app/engine/fixtures.py`) yerini alması da bu aşamanın kapsamı
dışındadır — yalnızca ileride bu entegrasyonu mümkün kılacak sürümlü sema
hazırlanmıştır, motor bu snapshot'ı henüz tüketmez. Ayrıca giriş
gerektiren müşteri sayfalarının otomatik gezilmesi ve gerçek kullanıcı
etkileşimi taklidi de kapsam dışıdır. Kimlik doğrulama tarafında da Google
OAuth/SSO, davet e-postası, ödeme ve gerçek parola sıfırlama e-posta
gönderimi (altyapısı hazır, üretim e-posta sağlayıcısı bağlı değil) kapsam
dışıdır.
