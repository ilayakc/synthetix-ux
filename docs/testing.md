# Test Stratejisi

Bu belge, Synthetix UX'te hangi test/kalite kapısının **neyi koruduğunu**
başlangıç seviyesinde anlatır. Amaç, testleri "neden var" sorusuyla birlikte
okumak; her katman, ürünün değişmez kurallarının (bkz.
[docs/product-rules.md](product-rules.md)) veya güvenlik varsayımlarının
(bkz. [docs/security.md](security.md)) koda sızmasını erken yakalar.

Tek komutla tüm paketi çalıştırmak için kökten:

```powershell
./scripts/verify.ps1
```

Adım adım ne yaptığı aşağıda açıklanıyor. Daha hızlı bir yerel döngü için
`-SkipE2E`, `-OnlyBackend` veya `-OnlyFrontend` bayrakları kullanılabilir.

## 1. Backend testleri (`backend/tests/`)

### Veri izolasyonu

Testler, geliştirme veritabanından (`synthetix_ux`) tamamen ayrı bir test
veritabanına (`synthetix_ux_test`, `backend/tests/conftest.py` tarafından
otomatik oluşturulur ve migrate edilir) karşı çalışır. Bu yüzden
`pytest` komutunu çalıştırmak **asla** `docker compose` geliştirme verisini
etkilemez veya silmez:

- Doğrudan `session` fixture'ını kullanan testler, kendi transaction'larını
  açar ve test sonunda daima geri alır (rollback) — hiçbir satır kalıcı
  olarak yazılmaz.
- `TestClient` (`client` fixture) üzerinden gerçek HTTP isteği yapan testler
  gerçekten commit eder (üretim davranışının aynısı test edilir), ama her
  test sonunda tüm tablolar `TRUNCATE` ile temizlenir.

Sonuç: testler herhangi bir sırada, paralel çalıştırmalarda veya tekrar
tekrar çalıştırıldığında birbirini etkilemez (flaky'lik kaynağı ortadan
kalkar).

### Marker'lar

```powershell
docker compose exec backend pytest -m unit          # DB/ağ olmayan saf mantık
docker compose exec backend pytest -m integration    # DB/TestClient akışları
docker compose exec backend pytest -m security        # bkz. asagida
```

- **`unit`**: Fiyatlandırma hesaplamaları, motor (engine) yardımcı
  fonksiyonları, OpenAPI şema üretimi gibi veritabanı/ağ gerektirmeyen saf
  mantık.
- **`integration`**: Kayıt/giriş, proje/sihirbaz akışı, simülasyon motoru,
  raporlar gibi veritabanı ve/veya `TestClient` üzerinden uçtan uca akışlar.
- **`security`**: Aşağıdakileri kanıtlayan testler:
  - **Kiracı (tenant) izolasyonu**: sahte `X-Organization-Id` başlığı veya
    başka bir organizasyonun kimliğiyle veri erişimi denemesi her zaman
    404/403 döner.
  - **SSRF koruması**: `analyzer`'a gönderilen URL'lerin özel/loopback/
    metadata IP aralıklarına çözülmesi reddedilir.
  - **Ledger idempotency ve eşzamanlılık**: aynı `idempotency_key` ile
    tekrar deneme yeni bir yan etki yaratmaz; yarışan iki rezervasyon
    isteğinden yalnızca biri başarılı olur (bakiye asla negatife düşmez).
  - **Tek kullanımlık ücretsiz hak**: bir kez tüketilen hak ikinci kez
    rezerve edilemez.
  - **Hız sınırı (rate limit)** ve **oturum/refresh token yeniden kullanım
    tespiti**.

### Kritik iş kuralları: %100 branch coverage

`scripts/check_critical_coverage.py`, aşağıdaki dosyalarda **her dalın**
(if/else, erken dönüş, hata yolu dahil) en az bir testle kanıtlanmış
olmasını zorunlu kılar — yalnızca genel kapsama yüzdesi yeterli değildir:

| Dosya | Neden kritik |
|---|---|
| `app/services/chip_ledger.py` | Chip bakiyesi asla negatife düşmemeli; idempotency ve kilit (concurrency) mantığı burada. |
| `app/services/entitlements.py` | "1 ücretsiz temel UX testi + 1 ücretsiz erişilebilirlik ön kontrolü, tek kullanımlık" kuralı burada uygulanır. |
| `app/services/quotes.py` | 1.000 persona sınırının ücretsiz hakkı ne zaman geçersiz kıldığı burada hesaplanır. |
| `app/services/pricing.py` | Sürümlenmiş Chip fiyatlandırması; yanlış sürüm/modül anahtarı sessizce yanlış fiyat üretmemeli. |
| `app/dependencies.py` | Kiracı bağlamının **yalnızca** imzalı JWT'den türetildiği, hiçbir istemci girdisinden değil. |
| `app/engine/baseline.py` | Bilimsel dürüstlük: yasaklı iddia filtresi ve belirsizlik aralığı hesaplaması. |

Genel proje için gerçekçi bir başlangıç eşiği kullanılır
(`backend/pyproject.toml`, `[tool.coverage.report] fail_under = 75`) — her
satırın test edilmesi zorlanmaz, ama yukarıdaki kritik dosyalar istisnasız
%100 dal kapsamı ister.

### Statik kapılar

```powershell
docker compose exec backend ruff format --check app tests   # bicim
docker compose exec backend ruff check app tests             # lint
docker compose exec backend mypy app                          # tip kontrolu
```

`ruff`/`mypy`/`pytest-cov`/`freezegun`, üretim Docker imajına dahil
edilmez (`backend/requirements-dev.txt`); yalnızca yerel doğrulama/CI'da
kurulur.

## 2. Frontend testleri (`frontend/src/**/*.test.tsx`)

Vitest + Testing Library ile kritik kullanıcı akışları test edilir: kayıt
(başarı/hata), giriş, şifre sıfırlama, sihirbaz (persona sınırı
doğrulaması, ücretsiz hak/Chip ile başlatma, 402 yetersiz bakiye hatası),
Kullanım & Chip sayfası (hak durumları), Raporlar/Rapor Detayı (yükleme/hata
durumları, AI açıklaması), Projeler (hata durumları).

```powershell
docker compose exec frontend npm run test            # testler
docker compose exec frontend npm run test:coverage    # + kapsama esigi
docker compose exec frontend npm run lint              # ESLint
docker compose exec frontend npm run format:check       # Prettier
docker compose exec frontend npm run typecheck           # tsc --noEmit
docker compose exec frontend npm run build                # production build
```

Kapsama eşiği (`frontend/vite.config.ts`) bu aşamada fiilen ulaşılan
kapsamanın biraz altına ayarlanmıştır (gerçekçi bir regresyon kapısı);
büyük, henüz test edilmemiş sayfalar (`PersonaPresets`, `ProjectDetail`,
`Simulations`) test kapsamına alındıkça eşik kademeli olarak yükseltilebilir.

## 3. Contract testi: frontend-backend şema uyumu

`scripts/contract_check.py`, backend'in `app.openapi()` şemasını üretir ve
`frontend/src/api/client.ts` içindeki her `apiFetch(...)` çağrısının gerçek
istek yolunu (path parametreleri normalize edilerek) bu şemayla karşılaştırır.
Frontend'in çağırdığı ama backend'de artık var olmayan bir uç nokta varsa
(örneğin bir router yanlışlıkla kaldırıldı/yeniden adlandırıldı), bu script
non-zero exit ile hemen yakalar — frontend ile backend'in birbirinden
habersiz "sessizce" birbirinden kopması engellenir.

`backend/tests/test_openapi_contract.py` ayrıca şemanın hatasız üretildiğini
ve kritik uç noktaların (kayıt/giriş/proje/sihirbaz/simülasyon/rapor) şemada
var olduğunu ucuz bir birim testiyle kanıtlar (bir router'ın yanlışlıkla
kaldırıldığı durumu contract script'inden bile önce yakalar).

## 4. E2E (uçtan uca tarayıcı) testleri (`e2e/`)

Playwright ile gerçek bir tarayıcıda, gerçek bir (izole) backend/frontend
stack'ine karşı çalışır. **Hiçbir gerçek dış internet adresine bağımlı
değildir**: sihirbazın URL alanına yazılan değer, `e2e/fixtures/site/`
altındaki bağımsız, küçük bir yerel statik sunucuya işaret eder (bugün bu
URL motor tarafından hiç ziyaret edilmez — bkz. `backend/app/engine/fixtures.py`
— ama bu, gerçek dış bağımlılığı baştan sıfıra indirir ve gelecekte analyzer
entegre olsa bile testin kırılmamasını sağlar). Bu yerel sunucu yalnızca
Playwright'in kendi `globalSetup`'ında başlar; **production SSRF korumasına
hiçbir şekilde dokunulmaz veya gevşetilmez**.

Senaryolar (`e2e/tests/`), her biri hangi ürün kuralını kanıtladığını
doğrudan test eder:

| Senaryo | Kanıtladığı kural |
|---|---|
| `01-registration.spec.ts` | Yeni şirket 0 Chip bakiyesiyle başlar; 2 ücretsiz hak (temel UX testi + erişilebilirlik ön kontrolü) otomatik tanımlanır. |
| `02-golden-path.spec.ts` | 1.000 persona ile bir temel UX testi, ücretsiz hak kullanılarak (Chip harcamadan) başlatılabilir, simülasyon tamamlanır ve rapor (bilimsel dürüstlük uyarısı dahil) görüntülenebilir. |
| `03-persona-limit-exceeded.spec.ts` | 1.000 persona sınırının üzerindeki bir test, 0 Chip bakiyesiyle reddedilir (yetersiz bakiye). |
| `04-second-free-use-rejected.spec.ts` | Ücretsiz temel UX testi hakkı **tek kullanımlıktır**; tüketildikten sonra ikinci bir ücretsiz deneme reddedilir. |

### İzolasyon: ayrı proje, ayrı portlar, ayrı veritabanı

E2E testleri, geliştirme stack'inizi **hiç etkilemeyen** tamamen ayrı bir
`docker compose` çalıştırmasına karşı çalışır:

```powershell
docker compose --env-file compose.e2e.env -p synthetix-ux-e2e -f compose.yaml up -d --build
```

`compose.e2e.env`, aynı `compose.yaml`'ı (ayrı bir overlay YAML dosyasına
gerek kalmadan — her değer zaten `${VAR:-varsayilan}` biçiminde
tanımlıdır) farklı bir proje adı (`synthetix-ux-e2e`), farklı host portları
(`5273`/`8100`/`5532`/`6479`) ve **ayrı bir mantıksal veritabanı**
(`synthetix_ux_e2e`) ile başlatır. Test bitince (`docker compose ... down -v`)
bu stack tamamen ve güvenle silinir — geliştirme veritabanınızda hiçbir iz
bırakmaz. Bu izole ortam, Playwright'i host'ta Node.js kurulumu gerektirmeden
resmi `mcr.microsoft.com/playwright` Docker imajıyla, aynı compose ağına
katılarak çalıştırır (bkz. `scripts/verify.ps1`).

## 5. Kapsam dışı

CI (GitHub Actions vb.) bu aşamada eklenmemiştir — yalnızca yerel
`scripts/verify.ps1` vardır. Hiçbir adım gizli anahtar veya dış servise veri
göndermez; Kafka/ChromaDB eklenmemiştir (bkz. [README.md](../README.md)
"Kapsam dışı").
