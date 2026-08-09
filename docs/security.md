# Güvenlik: URL Analiz Servisi (analyzer)

Bu belge, `analyzer` servisinin (Playwright/Chromium tabanlı, kullanıcının
belirttiği URL'leri **pasif** olarak ziyaret eden servis) SSRF (Server-Side
Request Forgery) tehdit modelini ve operasyon limitlerini açıklar. Diğer
kimlik doğrulama/oturum güvenliği konuları için bkz.
[docs/architecture.md](architecture.md#kimlik-doğrulama-ve-oturum).

## Neden bir tehdit modeli gerekir

`analyzer`, tanım gereği kullanıcının kontrol ettiği (ve dolayısıyla
kötüye kullanabileceği) bir URL'yi gerçekten ziyaret eden **tek**
bileşendir. Bir saldırgan, bu URL'yi kendi altyapısındaki bir sunucuya
değil, dahili ağdaki bir hedefe (ör. bulut metadata servisi, dahili bir
API, veritabanı yönetim paneli) işaret ettirmeye çalışabilir. Bu sınıf
saldırıya SSRF denir; `analyzer`'ın tüm mimarisi bunu engellemek üzere
tasarlanmıştır.

## Savunma katmanları

### 1. Ağ izolasyonu (container sınırı)

`analyzer`, ayrı bir container'da çalışır ve **host'a hiçbir port
yayınlamaz** (bkz. `compose.yaml`); yalnızca docker compose'un iç ağı
üzerinden, `ANALYZER_BASE_URL` aracılığıyla backend/worker tarafından
erişilebilir. Ayrıca `/internal/analyze` uç noktası, paylaşılan bir sır
(`ANALYZER_SHARED_TOKEN`, `X-Analyzer-Token` başlığı, sabit zamanlı
karşılaştırma ile doğrulanır) gerektirir — bu, ağ izolasyonu bir şekilde
atlatılsa bile ikinci bir savunma katmanıdır.

**Üretim önerisi (bu aşamanın kapsamı dışında, operasyonel bir not):**
`analyzer` container'ının egress (giden) ağ erişimi, bir güvenlik
grubu/network policy ile dahili IP aralıklarına (RFC1918, link-local vb.)
erişemeyecek şekilde ayrıca kısıtlanmalıdır. Uygulama katmanındaki
kontroller (aşağıda) birincil savunmadır, ancak container/ağ düzeyinde bir
ikinci sınır, uygulama katmanında keşfedilmemiş bir baypası da engeller
(derinlemesine savunma).

### 2. URL sözdizimi ve şema denetimi (`app/url_safety.py`)

Hem backend (`backend/app/services/url_safety.py`, hızlı/kullanıcı dostu
400 hatası için) hem de analyzer (`analyzer/app/url_safety.py`, otoriter
denetim için) aynı mantığı taşıyan bağımsız bir modülde şunları reddeder:

- `http`/`https` dışındaki şemalar (`file://`, `data:`, `javascript:`,
  `ftp://` vb.)
- URL içinde kimlik bilgisi (`http://user:pass@host/`)
- Hostname'i olmayan URL'ler
- Bilinen bulut metadata hostname'leri (ör. `metadata.google.internal`)

### 3. DNS çözümleme ve IP engelleme

`validate_public_url`, hostname'i çözümler ve **tüm** dönen IP'lerin
herkese açık (public) olduğunu doğrular; aşağıdaki aralıklardan biri
engellenir (hem IPv4 hem IPv6):

- Loopback (`127.0.0.0/8`, `::1`)
- Private/RFC1918 ve RFC4193 (`10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`, `fc00::/7`)
- Link-local (`169.254.0.0/16` — **bulut metadata IP'si
  `169.254.169.254` dahil**, `fe80::/10`)
- Reserved, multicast, unspecified (`0.0.0.0`)
- IPv4-mapped IPv6 adresleri (`::ffff:127.0.0.1` gibi), eşdeğer IPv4
  adresine indirgenerek aynı kontrolden geçirilir

Çözümlenen IP'lerden **herhangi biri** engellenmişse tüm URL reddedilir
(bir hostname'in hem genel hem özel bir IP döndürerek kontrolü atlatmaya
çalışmasına karşı).

### 4. DNS rebinding ve TOCTOU

Klasik bir SSRF bypass tekniği **DNS rebinding**'dir: saldırgan, kontrol
ettiği bir hostname için kısa TTL'li bir DNS kaydı sunar; güvenlik
kontrolü sırasında genel (public) bir IP döner, ancak kontrol ile gerçek
bağlantı arasındaki kısa pencerede DNS kaydı özel bir IP'ye değiştirilir
(TOCTOU — time-of-check to time-of-use).

`analyzer`'ın savunması:

1. Ana navigasyon hedefi için hostname **bir kez** çözümlenir ve
   doğrulanan IP, Chromium'a `--host-resolver-rules=MAP <hostname> <ip>`
   launch bayrağıyla **sabitlenir (pinned)**. Böylece doğrulama ile gerçek
   bağlantı arasında hostname tekrar çözümlenmez; rebinding penceresi bu
   yol için tamamen kapatılır.
2. Sayfadaki tüm istekler (`page.route("**/*")`) izlenir. Ana hostname'den
   farklı her yeni hostname (yönlendirme hedefi veya alt kaynak — resim,
   script, XHR), istek gönderilmeden önce aynı DNS + IP-engelleme
   kontrolünden geçirilir; başarısız olursa istek `abort()` edilir.
3. Yönlendirme (redirect) sayısı `max_redirects` (varsayılan 3) ile
   sınırlanır; navigasyonun **nihai** URL'si de ayrıca doğrulanır.

**Bilinen kalan risk:** (2)'deki ikincil hostname'ler (birincil navigasyon
hedefi dışındakiler) için Chromium kendi DNS çözümlemesini kullanır —
bunlar `--host-resolver-rules` ile sabitlenemez (bu bayrak süreç
başlatılırken sabittir). Dolayısıyla bu ikincil hostname'ler için, bizim
kontrolümüz ile Chromium'un gerçek bağlantısı arasında çok kısa bir
TOCTOU penceresi kalır. Bu, dokümante edilmiş bilinçli bir kapsam
sınırıdır; üretim sertleştirmesi olarak container'ın egress ağ
kısıtlaması (yukarıdaki "Ağ izolasyonu" notu) bu kalan riski de kapatır.

### 5. Yetki onayı (kullanıcı beyanı)

`POST /api/page-analyses`, `authorization_confirmed: true` gönderilmeden
hiçbir iş oluşturmaz (403). Bu, bu aşamada **kullanıcının kendi beyanına
dayalı** bir onay kutusudur — alan adı sahipliğinin kriptografik olarak
doğrulanması (ör. DNS TXT kaydı veya well-known dosyası ile) bu aşamanın
kapsamı dışındadır ve ileride eklenebilir.

### 6. Yıkıcı etkileşim yasağı

`analyzer`, yalnızca **navigasyon + pasif okuma** yapar: DOM okuma, ekran
görüntüsü, performans zamanlaması, axe-core çalıştırma. Form gönderme,
giriş yapma, satın alma, dosya indirme veya herhangi bir tıklama/yazma
etkileşimi **yapılmaz**; bu, kod tabanında hiçbir `click()`/`fill()`/
`submit()` çağrısı bulunmamasıyla yapısal olarak garanti edilir.

## Operasyon limitleri

| Limit                          | Varsayılan            | Nerede uygulanır                              |
| ------------------------------- | ---------------------- | ---------------------------------------------- |
| Navigasyon zaman aşımı           | 15 saniye               | `analyzer` (`NAVIGATION_TIMEOUT_SECONDS`)       |
| Maksimum yönlendirme (redirect)  | 3                       | `analyzer` (`MAX_REDIRECTS`)                    |
| Maksimum yanıt boyutu            | 10 MiB                  | `analyzer`, `Content-Length` başlığı üzerinden (bkz. altta not) |
| Sayfa/tab sayısı                 | 1 (tek sayfa)           | `analyzer` (`MAX_PAGES_PER_REQUEST`)            |
| Eşzamanlı analiz sayısı          | 2                       | `analyzer` içi semafor (`MAX_CONCURRENT_ANALYSES`) |
| Container bellek/CPU             | 2 GiB / 1.5 CPU         | `compose.yaml` (`mem_limit`, `cpus`)            |
| Backend->analyzer istek zaman aşımı | 30 saniye            | `backend` (`ANALYZER_REQUEST_TIMEOUT_SECONDS`)  |
| Ekran görüntüsü saklama süresi   | 24 saat                 | `backend`, purge cron'u (`page_analysis_screenshot_retention_seconds`) |
| Analiz denemesi (crash recovery) | 3                       | `backend` (`PAGE_ANALYSIS_MAX_ATTEMPTS`)        |

**Yanıt boyutu notu:** Sınır, `Content-Length` başlığı üzerinden en iyi
çaba (best-effort) ile uygulanır; bu başlığı göndermeyen "chunked"
yanıtlar yalnızca navigasyon zaman aşımı ile sınırlanır. Tam, akış
(streaming) düzeyinde bayt sayacı bu aşamanın kapsamı dışındadır.

## Veri saklama ve redaksiyon

- **Hiçbir zaman saklanmaz:** ham HTML, form alan değerleri, çerezler
  (cookie), tokenlar veya diğer kişisel veriler. `analyzer`'ın ürettiği
  `page_feature_snapshot` şeması bunları hiçbir zaman içermez (bkz.
  `analyzer/app/schemas.py`); backend'in `_extract_features` fonksiyonu
  yalnızca bilinen, sabit bir alan kümesini (başlık, başlıklar, metin
  istatistikleri, kontrol sayıları, yaklaşık element kutuları, performans
  zamanları, kontrast adayları, axe-core özeti) saklar.
- **Süreli saklanır, otomatik silinir:** viewport ekran görüntüsü (PNG).
  `page_analyses.screenshot_expires_at` alanı doldurulur ve bir arq
  cron'u (`purge_expired_page_analysis_screenshots`, 10 dakikada bir)
  süresi dolmuş ikili veriyi siler; iş kaydının (metadata, türetilmiş
  özellikler) kendisi silinmez.
- **Kalıcı saklanır:** iş durumu, URL, türetilmiş özellikler,
  `analyzer_version`/`snapshot_version`/`source` (kaynağın izlenebilirliği
  için).

## Erişilebilirlik ön kontrolü hakkında not

axe-core ile üretilen erişilebilirlik sonucu, API yanıtında ve
depolanan veride açıkça **"otomatik ön kontrol" (accessibility
precheck)** olarak adlandırılır ve bir uyarı metni eşlik eder: bu, tam bir
WCAG uygunluk sertifikası veya yasal uygunluk değerlendirmesi **değildir**
— otomatik araçlar erişilebilirlik sorunlarının yalnızca bir kısmını
tespit edebilir ve manuel inceleme ile gerçek kullanıcı testi gerektirir
(bkz. [docs/scientific-integrity.md](scientific-integrity.md) ile aynı
"aşırı iddiada bulunma" ilkesi).

## Güvenlik: Yüklenen Tasarım Ekran Görüntüleri (design assets)

`analyzer` yukarıdaki tehdit modeline sahip **tek** URL-ziyaret eden
bileşenken, kullanıcının doğrudan bir dosya **yüklediği** ayrı bir akış da
vardır (`backend/app/services/design_assets.py` + `backend/app/routers/design_assets.py`).
Bu akışın tehdit modeli SSRF değil, **kötü amaçlı/bozuk dosya işleme**
(decompression-bomb, format sahteciliği, aşırı kaynak tüketimi) ve
**kiracı (tenant) izolasyonudur**. Bu özellik henüz sihirbaz/analyzer/ısı
haritası akışlarına **bağlı değildir** — bağımsız, kendi başına test
edilebilir bir altyapı katmanıdır (bkz. aşağıdaki "Kapsam" notu).

### Kabul edilen biçimler

Yalnızca **PNG, JPEG, WebP** kabul edilir. **SVG asla kabul edilmez**
(script içerebileceği için) ve **animasyonlu/çok kareli** dosyalar
(animasyonlu WebP/APNG, GIF) da reddedilir — yalnızca statik, tek kareli
tasarım ekran görüntüleri desteklenir. `Content-Type` başlığına veya dosya
uzantısına **tek başına güvenilmez**; dosya Pillow ile **gerçekten decode
edilerek** doğrulanır (ör. `.png` uzantılı ama içeriği SVG olan bir dosya
decode aşamasında reddedilir).

### Boyut/kaynak sınırları (derinlemesine savunma)

| Sınır | Varsayılan | Ayar | Not |
| --- | --- | --- | --- |
| Yükleme (network gövdesi) boyutu | 10 MiB | `design_asset_max_bytes` | İstek gövdesi sınırlı parçalar (chunk) halinde okunur; sınır aşılır aşılmaz akış durdurulur — dosyanın tamamı önceden belleğe alınmaz. İstemcinin bildirdiği `Content-Length`'e tek başına güvenilmez. |
| Saklanan (yeniden encode edilmiş) çıktı boyutu | 20 MiB | `design_asset_max_stored_bytes` | Yükleme sınırından **kasıtlı olarak ayrı**: EXIF temizleme sonrası yeniden encode, özellikle PNG'lerde kaynaktan büyük çıkabilir. |
| Genişlik/yükseklik (piksel, her eksen) | 8000×8000 | `design_asset_max_dimension` | |
| Toplam piksel sayısı (genişlik×yükseklik) | 25.000.000 | `design_asset_max_pixels` | `design_asset_max_dimension`'dan **bağımsız** ek bir katman (çok uzun/ince görselleri de kapsar). |
| Pillow decompression-bomb eşiği | Pillow varsayılanı (`Image.MAX_IMAGE_PIXELS`, ~89M piksel) | — | **Devre dışı bırakılmaz.** Eşiğin 1×-2× arası normalde yalnızca bir *uyarı*dır (Pillow işlemeye devam eder); bu servis uyarıyı açıkça hataya çevirir, böylece riskli bir görsel sessizce işlenmeye devam etmez. |

### Metadata temizliği

EXIF/ICC-profile gibi tüm metadata, görsel **piksel tamponunun**
(`tobytes`/`frombytes`) yeniden encode edilmesiyle tamamen düşürülür (EXIF
döndürme bilgisi, atılmadan önce `ImageOps.exif_transpose` ile piksellere
gömülür — aksi halde görsel yanlış yönde görünür). 90/270 derecelik
döndürmeler genişlik/yüksekliği değiştirebileceği için, veritabanına
**orijinal yüklenen dosyanın değil, gerçekten saklanan (sanitized) çıktının**
ölçüleri yazılır.

### Dosya kimliği ve depolama

Kullanıcının gönderdiği orijinal dosya adı **hiçbir zaman saklanmaz**
(yalnızca isteğe bağlı bir kullanıcı etiketi); rastgele bir UUID, güvenli
dosya kimliği olarak kullanılır. İkili veri, `page_analyses.screenshot_data`
ile aynı desende, **Postgres `LargeBinary`** sütununda saklanır — bu, MVP
için bilinçli bir karardır (ayrı bir obje depolama/MinIO/S3 servisi henüz
yoktur, bkz. [docs/architecture.md](architecture.md)); hacim/performans
sorun olursa bu servis katmanının arkasında sessizce değiştirilebilir.

### Saklama süresi ve erişimin kesilmesi

Her yüklenen görselin bir `expires_at` değeri vardır (varsayılan 24 saat,
`design_asset_retention_seconds`). Süresi dolduğunda:

- Bir purge cron'u (`purge_expired_design_assets`, `page_analyses` ile aynı
  10 dakikalık döngü deseninde) ikili veriyi siler; metadata satırı kalır.
- **Preview uç noktası, purge cron'unun bir sonraki çalışmasını beklemez**:
  `expires_at` geçmişte olan bir kayıt, ikili veri DB'de hâlâ mevcut olsa
  bile önizleme uç noktası tarafından hemen 404 ile reddedilir.

### Kiracı (tenant) izolasyonu ve bilgi sızdırmama

Her sorguda `organization_id` filtrelenir (`get_owned_asset`). Sahiplik
kontrolü **her zaman** süre dolma kontrolünden **önce** çalışır; böylece
"kayıt var ama başka bir organizasyona ait" ile "kaydın süresi dolmuş"
durumları **aynı** 404 yanıtını üretir — yanıt, hedef kaydın var olup
olmadığı veya süresinin dolup dolmadığı hakkında hiçbir bilgi sızdırmaz
(bkz. `app.routers.page_analysis` ile aynı "mevcut değil veya süresi
doldu" konvansiyonu).

### Önizleme yanıtı güvenlik başlıkları

`GET /api/design-assets/{id}/preview` yanıtı şu başlıkları taşır:

- `X-Content-Type-Options: nosniff` — tarayıcının içerik türünü
  "koklayarak" (sniff) farklı yorumlamasını engeller.
- `Cache-Control: private, no-store` — kimlik doğrulama/tenant kontrolüne
  tabi, kullanıcıya özel bir görsel hiçbir ara/paylaşımlı önbellek
  (proxy/CDN) veya disk önbelleği tarafından saklanmaz.
- `Content-Disposition: inline; filename="design-asset.<uzantı>"` —
  kullanıcının gönderdiği dosya adı **asla** kullanılmaz; uzantı yalnızca
  doğrulanmış içerik türünden türetilir.

Görsel hiçbir zaman genel (public) bir URL'den servis edilmez; her istek
cookie tabanlı oturum + `organization_id` kontrolünden geçer.

### Loglama

Diğer akışlarla aynı kural geçerlidir: loglara hiçbir zaman ikili veri,
base64 kodlu görsel içeriği veya hassas dosya içeriği yazılmaz.

### Kapsam notu

Bu altyapı, test sihirbazının hem tekil URL/ekran görüntüsü akışına (bkz.
"Ekran görüntüsü kaynağı") hem de A/B karşılaştırmasının her iki tarafına
(Tasarım A ve Tasarım B, bağımsız olarak) bağlıdır. **URL kaynaklı** launch'lar
Paket 4B itibarıyla `analyzer`/`page_analysis` akışına ve motor girdisine
bağlıdır (bkz. "Prompt 7 motoruyla ilişki" bölümü); Paket 4C+4D itibarıyla
ekran görüntüsü kaynağı da yerel görsel analiz akışına bağlıdır (bkz. altta
"Ekran Görüntüsü Yerel Görsel Analizi" bölümü) — ancak **ekran görüntüsü/AI
kaynaklı** (URL dışı) bir kaynakla gerçek bir test hâlâ BAŞLATILAMAZ (bkz.
"AI ile Tasarım Varyantı Üretimi" bölümü ve
`app.services.test_wizard.launch_draft`) — bu, ayrı bir sonraki uygulama
paketinin kapsamındadır.

## Güvenlik: Ekran Görüntüsü Yerel Görsel Analizi (Paket 4C+4D)

Bir DesignAsset kaynaklı `PageAnalysis` işi işlenirken (`app.services.
page_analysis._process_design_asset_source`), güvenli snapshot kopyalandıktan
SONRA `app.services.image_visual_analysis.analyze_screenshot` çağrılır - bu
tamamen yerel/deterministik bir OpenCV (`opencv-python-headless`) + numpy
işlemidir.

**Harici çağrı yapılmaz**: bu analiz hiçbir zaman bir vision API'sine veya
üçüncü bir tarafa görsel/istek göndermez; tüm işlem `backend`/`worker`
container'ı içinde bellek içinde yapılır. `analyzer` HTTP servisi bu yolda
HİÇBİR ZAMAN çağrılmaz (test: `test_process_analysis_design_asset_never_calls_analyzer`).

**Girdi güveni**: bu fonksiyon yalnızca `app.services.image_safety` ile
ÖNCEDEN gerçek decode/format/byte-limiti/genişlik-yükseklik/toplam-piksel/
çoklu-kare kontrolünden geçmiş bytes üzerinde çalışır - kendi format/boyut
kontrolünü tekrar yapmaz, ama çalışma çözünürlüğünü (`MAX_WORKING_DIMENSION`,
1600px) BAĞIMSIZ olarak ayrıca sınırlar (CPU/bellek tüketimini üst sınırlama
- saklanan boyutlara kör güvenilmez).

**Determinizm ve hata güvenliği**: aynı bytes → bit-bir-bit aynı JSON sonucu
(algoritma sürümü `visual-analysis-1` ile birlikte saklanır). NaN/Infinity
değerler reddedilir; geçici dosya kullanılmaz (`cv2.imdecode` bellek içi);
binary/piksel verisi hiçbir yerde loglanmaz. OpenCV hata verirse
(`VisualAnalysisError`) iş güvenli biçimde `FAILED` olur - `features` hiçbir
zaman kısmi yazılmaz, analyzer HTTP servisi çağrılmaz, fixture fallback
yapılmaz, iç hata ayrıntısı kullanıcıya sızmaz (yalnızca WARNING seviyeli
log'da tutulur).

**Kullanıcı CTA onayı (`user_confirmed_cta`)**: `PATCH /api/tests/drafts/{id}`
üzerinden `current_cta_annotation`/`new_cta_annotation` alanlarına yazılır
(bkz. `app.services.test_wizard.resolve_cta_annotation_patch`). Sunucu
tarafında zorunlu kılınan kurallar: x/y/w/h `isfinite` ve 0-1 aralığında
olmalı, sıfır/anlamsız küçük alan reddedilir, aşırı büyük/tam ekran seçimde
sert reddetme yerine uyarı (`cta_annotation_covers_full_image`) döner,
`design_asset_id` o tarafın (current/new) GÜNCEL asset'iyle eşleşmeli (aksi
halde reddedilir), başka bir tenant'ın asset'ine erişim aynı genel
mesajla (bilgi sızdırmadan) reddedilir, `verified_content_sha256` HER ZAMAN
sunucuda `DesignAsset.checksum_sha256`'dan yeniden hesaplanır - client'ın
gönderdiği herhangi bir hash değeri sessizce yok sayılır. Bir tarafın
`design_asset_id`'si değiştiğinde O TARAFIN annotation'ı sunucu tarafında
otomatik olarak temizlenir (`invalidate_stale_cta_annotations`) - diğer
taraf ve diğer taslaklar etkilenmez; annotation bu nedenle DesignAsset'in
global bir özelliği değil, draft+taraf bağlamına özgü bir seçimdir - ayrı
bir tablo/migration yerine mevcut `TestWizardDraft.payload` JSON sözleşmesi
genişletilmiştir. Silinmiş/süresi dolmuş bir asset için yeni annotation
kaydedilmez; AI ile üretilip kabul edilmiş bir DesignAsset, bu doğrulama
zincirinde normal bir yüklenen asset gibi davranır (özel muamele görmez).

### Paket 4 Final Hardening: rapor-bağlı snapshot saklama süresi

`PageAnalysis.screenshot_data` için iki ayrı, yapılandırılabilir saklama
penceresi vardır (bkz. `.env.example`, `app.config.settings`):

- `page_analysis_screenshot_retention_seconds` (varsayılan 24 saat) —
  rapora/tamamlanmış bir run'a henüz bağlanmamış (veya hiç bağlanmayacak)
  capture'lar için.
- `report_linked_screenshot_retention_seconds` (varsayılan 30 gün) — bir
  `PageAnalysis`, tamamlanmış bir `Report`'a bağlıysa (bkz.
  `app.services.page_analysis.purge_expired_screenshots`), kısa süre yerine
  bu daha uzun pencere uygulanır; süre `Report.created_at`'ten itibaren
  işlenir. **Süresiz saklama YOKTUR** — bu pencere de dolduğunda ikili veri
  purge edilir; yalnızca rapor/metadata satırı kalır, kullanıcı raporunda
  görselin artık mevcut olmadığı açıkça belirtilir (heatmap/CTA overlay
  bölümleri `screenshot_url=null`, `coordinates_unavailable_reason` ile
  erişilebilir tablo/liste alternatifine düşer — sahte bir görsel asla
  üretilmez).

Tenant (organizasyon) silindiğinde, o organizasyona ait TÜM `PageAnalysis`
satırları (`organization_id` FK, `ondelete="CASCADE"`) otomatik temizlenir —
ayrı bir purge adımı gerekmez. Purge cron'u (`purge_expired_screenshots`)
yalnızca süresi dolmuş satırları hedefler; `organization_id` filtresi
sorgunun kendisinde olmadığı için tüm organizasyonlara eşit uygulanır, ama
her satırın purge edilip edilmeyeceği YALNIZCA KENDİ `screenshot_expires_at`/
rapor-bağlantı durumuna bakar — başka bir organizasyonun verisine hiçbir
şekilde dokunmaz (bkz. `backend/tests/test_reports.py`
`test_report_linked_screenshot_survives_short_ttl_expiry_...` ve
`test_unlinked_short_ttl_screenshot_is_purged_normally`).

### Paket 4 Final Hardening: stale CTA annotation launch davranışı

Bir kullanıcının onayladığı CTA (`current_cta_annotation`/
`new_cta_annotation`), launch anında tekrar doğrulanır
(`app.services.test_wizard.revalidate_cta_annotation`). Geçersizleşmişse
(asset artık kullanılamıyor veya checksum eşleşmiyor) **sessizce
temizlenmez** — launch yine de başarılı olur (annotation zorunlu değildir,
genel dikkat analizi/aday-seviyesi değerlendirme her zaman fallback'tir),
ama `LaunchResponse.warnings` içinde `{"code": "stale_cta_annotation_cleared",
"slot": "current"|"new", "message": "..."}` biçiminde açık bir uyarı döner —
doğru tarafa (`slot`) bağlı, A/B'nin diğer tarafını asla etkilemez. Frontend
bu uyarıyı kullanıcıya "Tasarım değiştiği için önceki CTA seçiminiz
temizlendi" mesajıyla gösterir (bkz. `frontend/src/pages/wizard/TestWizard.tsx`).

### Paket 4 Final Hardening: `source_reference` — sahte URL workaround'ının kaldırılması

Önceki bir turda, DesignAsset (screenshot/AI) kaynaklı taraflarda motorun
("bir URL alanı her zaman dolu olmalı" şeklindeki eski, URL-merkezli)
girdisini karşılamak için `input_snapshot["url"]` alanına `design-asset:<id>`
biçiminde sahte bir metin yazılıyordu. Bu, kullanıcıya/rapor ekranına
sızma riski taşıyan bir workaround'du. Artık:

- `input_snapshot["url"]` yalnızca GERÇEK bir URL varsa doludur, aksi halde
  `None`'dur.
- `input_snapshot["source_reference"]` (ör. `design-asset:<id>`) AYRI,
  açıkça isimlendirilmiş bir alandır — yalnızca motorun "boş olmayan kimlik"
  gereksinimini additive biçimde karşılar (bkz. `app.engine.baseline
  .run_baseline_simulation`, `app.engine.advanced_modules
  ._require_source_identity`); hiçbir skor hesabına girmez.
- Skorlama/determinizm her zaman GERÇEK görsel/DOM içerik özetinden
  (`PageFeatureSnapshot.input_hash` = `PageAnalysis.content_sha256`) türer —
  `source_reference`/`design_asset_id`'den DEĞİL; bu nedenle aynı görsel
  içerik farklı bir `design_asset_id` ile sunulsa bile analiz sonucu
  aynıdır (bkz. `backend/tests/test_simulation_engine.py
  test_visual_result_is_independent_of_source_reference_identity`).
- Rapor ekranında `info_box.input_summary.url`/`source_type` doğrudan,
  gizleme/maskeleme YAPILMADAN iletilir — artık maskelenecek sahte bir
  değer yoktur.

## Güvenlik: AI ile Tasarım Varyantı Üretimi

Bu bölüm, A/B karşılaştırmasında "Tasarım B" için isteğe bağlı AI ile
varyant üretme özelliğinin veri akışını kapsar (bkz.
`backend/app/services/design_generation.py`; kapsamlı ürün/gizlilik
kuralları için bkz. `docs/ai-policy.md` "AI ile Tasarım Varyantı
Üretimi").

**Hangi veri uzak sağlayıcıya gönderilir**: yalnızca referans tasarım
görselinin (Tasarım A'nın ekran görüntüsü) piksel verisi ve kullanıcının
serbest metin talebi (prompt). Sayfa URL'si, cookie, token veya başka bir
kimlik doğrulama verisi hiçbir zaman gönderilmez; kullanıcı, aktarımdan
önce ayrı bir onay kutusuyla açıkça bilgilendirilir (bkz. `docs/ai-policy.md`
"Gizlilik ve açık kullanıcı onayı").

**Sağlayıcı yoksa özellik çalışmaz**: `IMAGE_GENERATION_PROVIDER=none`
(varsayılan) iken hiçbir ağ çağrısı yapılmaz ve hiçbir görsel üretilmez;
sahte/placeholder bir sonuç asla üretilmez. Gerçek bir sağlayıcı
sözleşmesi bu depoda henüz doğrulanmadığı için `remote` seçeneği de
şu an işlevsiz bir adaptör iskeletidir (bkz. `docs/ai-policy.md`).

**Saklama**: üretilen sonuç görseli, kullanıcı yüklemesiyle AYNI güvenli
doğrulama hattından geçirilip AYNI TTL ile (`design_asset_retention_seconds`,
24 saat) saklanır; iş (job) kaydındaki `prompt` alanı da aynı sürede
`NULL`'a çekilir (bkz. `app.services.design_generation.purge_expired_jobs`).
API anahtarı hiçbir zaman veritabanında saklanmaz, loglanmaz veya
frontend'e gönderilmez — yalnızca `IMAGE_GENERATION_API_KEY` ortam
değişkeninde tutulur ve yalnızca backend/worker sürecinden (gerçek bir
sağlayıcı bağlandığında) okunur.

**Analiz ile karıştırılmamalıdır**: bu özellik bir görsel ÜRETİR, bir
sayfayı ANALİZ ETMEZ; `analyzer`/`page_analysis` (SSRF korumalı, pasif
sayfa analizi) ile hiçbir kod veya veri paylaşmaz.

## Launch akışında TOCTOU race düzeltmesi (Paket 4 sonrası hata düzeltme)

**Olay:** gerçek kullanıcı akışında (A/B karşılaştırma, iki taraf da ekran
görüntüsü, her iki CTA onaylanmış, 500 persona) "Test başlatılamadı. Lütfen
tekrar deneyin." jenerik hatası gözlemlendi. Kök neden araştırması (canlı
Playwright reprodüksiyonu + backend logları) şunu doğruladı: `app.services
.settings.get_or_create_organization_settings` ve `app.services.entitlements
.get_or_create_entitlement` — ikisi de "SELECT; yoksa INSERT" desenini
kilitsiz uyguluyordu. Bir organizasyonun bir kaynağa (organization_settings
satırı / belirli bir `feature_key` için entitlement satırı) İLK erişiminde
iki istek gerçekten eş zamanlı gelirse (bu turda React StrictMode'un dev'de
effect'leri iki kez çalıştırması ile kanıtlandı — ayrıca `launch_draft`'ın
kendisi `reserve_entitlement` üzerinden `get_or_create_entitlement`'ı
doğrudan çağırır), ikinci `INSERT` benzersizlik kısıtını ihlal eder ve
yakalanmamış bir `IntegrityError` 500 Internal Server Error olarak dışarı
sızar — frontend bunu jenerik mesaja indirger.

**Düzeltme:** her iki fonksiyon da artık INSERT denemesini bir SAVEPOINT
(`session.begin_nested()`) içinde yapar; `IntegrityError` yakalanırsa
yalnızca SAVEPOINT geri alınır (dışarıdaki transaction ETKİLENMEZ) ve satır,
diğer isteğin oluşturduğu haliyle güvenle yeniden okunur. Bu, hiçbir
kullanıcıya görünür 500 üretmeden, iki isteğin de aynı (doğru) satırı
almasını garanti eder. `app.services.settings.get_or_create_user_preferences`
aynı desene sahip olduğu için aynı düzeltmeyle güncellendi (henüz üretimde
gözlemlenmiş bir kırılma yok, ama aynı sınıf hata).

Doğrulama: `backend/tests/test_get_or_create_race_safety.py` iki bağımsız DB
oturumunu bir `asyncio.Event` bariyeriyle senkronize ederek gerçek bir eş
zamanlı INSERT yarışını deterministik olarak zorlar (asyncio zamanlama
şansına bırakmaz); düzeltme olmadan bu testler gerçek production hatasıyla
(`UniqueViolationError`) başarısız olur, düzeltmeyle geçer.

**İlişkili düzeltme — hata mesajı gösterimi:** `frontend/src/pages/wizard
/TestWizard.tsx`'in `handleLaunch` fonksiyonu eskiden yalnızca HTTP 400/402
için backend'in gerçek `detail` mesajını gösteriyordu; başka her durum
(404/409/422/500 dahil) jenerik "Test başlatılamadı" mesajına düşüyordu. Artık
her `ApiError` için gerçek mesaj gösterilir — `app/api/client.ts`'nin
`rawFetch`'i zaten yalnızca güvenli (stack trace/SQL/tenant bilgisi
İÇERMEYEN) bir `detail` metni veya gövdesiz yanıtlar için genel bir
`"API isteği başarısız: <status> <statusText>"` yer tutucusu üretir; bu
nedenle bu değişiklik hiçbir iç sistem detayını sızdırmaz.

## Prompt 7 motoruyla ilişki (Paket 4B)

`analyzer`'ın ürettiği sürümlü `page_feature_snapshot` (bkz.
`PAGE_FEATURE_SNAPSHOT_VERSION` benzeri `SNAPSHOT_VERSION` alanı,
`analyzer/app/schemas.py`), Paket 4B itibarıyla **URL kaynaklı launch'larda**
heuristic motorun (`backend/app/engine/baseline.py`,
`backend/app/engine/advanced_modules.py`) girdisidir:

- Wizard launch'ı, her URL varyantı için aynı launch transaction'ında bir
  `PageAnalysis` capture'ı oluşturur ve `SimulationRun.page_analysis_id`
  FK'sını kalıcı olarak bağlar (bkz. `app.services.test_wizard.launch_draft`).
  Client bu ID'yi asla seçemez/enjekte edemez.
- `SimulationRun` işlenmeye, bağlı `PageAnalysis` `SUCCEEDED` olana kadar
  başlamaz (bkz. `app.services.simulation_worker.claim_next_queued_runs`);
  `FAILED` olursa run da güvenli biçimde `FAILED` olur ve A/B grubunun tam
  rezervasyonu release edilir — hiçbir koşulda eski `sha256(url)` yer
  tutucusuna (`backend/app/engine/fixtures.py`) sessizce düşülmez.
- `app.services.page_analysis_adapter.adapt_page_analysis`, gerçek
  `element_boxes`/`layout_regions`/`text_stats`/`contrast_candidates`
  verisini doğrulayıp (şema/NaN/aralık kontrolleri) motorun beklediği
  skaler girdiye çevirir; `SimulationRun.result.feature_source` alanı
  sonucun `"dom"` (gerçek PageAnalysis) mü yoksa `"fixture"` (Paket 4B
  öncesi legacy run) mü olduğunu açıkça saklar.
- CTA sınıflandırması kanıta göre yapılır: `element_boxes` içindeki gerçek
  DOM `role` (`button`/`link`) kanıtlı kutular `dom_interactive_candidate`
  (Paket 4C düzeltmesi — eskiden yanlışlıkla `confirmed_cta` deniyordu;
  her etkileşimli DOM öğesi mutlaka bir pazarlama CTA'sı değildir);
  `layout_regions.birincil_cta` (semantik kanıtsız, yalnızca "ilk eşleşen
  buton" heuristiği) `cta_candidate`/`attention_region` olarak kalır.
- URL yakalaması, dinamik/tembel yüklenen içeriği pasif kaydırmayla görünür
  hale getirir ve en fazla 4.000 px yüksekliğinde uzun sayfa görüntüsü alır.
  DOM kutuları aynı yakalama sınırına göre filtrelenir; metinsiz etkileşimli
  öğeler ve görüntü dışındaki kutular CTA adayı olarak saklanmaz. Kaydırma
  sırasında tıklama, form gönderme veya oturum açma yapılmaz.
- **Paket 4 Final itibarıyla güncel değil**: ekran görüntüsü/AI kaynaklı
  launch engeli kaldırıldı (bkz. `app.services.test_wizard._revalidate_launch_sources`,
  `app.services.page_analysis_adapter.adapt_visual_page_analysis`) — DesignAsset
  kaynaklı taraflar da artık gerçek bir `SimulationRun`'a bağlanır; provenance
  `"visual_heuristic"` olarak ayrı işaretlenir, `"dom"` ile asla karışmaz.

## Dev/test ortamında disposable (smoke) tenant temizliği — GÜVENLİ PROSEDÜR

**Arka plan (olay kaydı):** Paket 4 Final doğrulama turunda, canlı smoke testi
sırasında oluşturulan disposable organizasyonlar `DELETE ... WHERE name LIKE
'Smoke%'` (prefix/LIKE tabanlı toplu silme) ile temizlenmeye çalışıldı. Bu,
**ciddi bir prosedür hatasıydı**: sorgu, bu turda oluşturulmamış (muhtemelen
önceki bir oturumdan kalma, temizlenmemiş) iki-üç organizasyonu da kapsama
aldı ve geri alınamaz biçimde sildi (bu ortamda `archive_mode=off`, ayrı bir
backup/WAL arşivi yok — bkz. aşağıdaki "Zorunlu ön koşul: backup"). İsim
prefix'i TEK BAŞINA hiçbir zaman silme yetkisi sayılmamalıdır.

**Bundan sonra, dev/test ortamında smoke-test verisi temizlenirken ZORUNLU
prosedür:**

1. **Zorunlu ön koşul — backup**: herhangi bir toplu/keşif amaçlı silme
   denemesinden ÖNCE `pg_dump -Fc` ile doğrulanmış (checksum + `pg_restore
   --list` + izole DB'ye restore testi) bir yedek alınmalı. Backup
   başarısızsa/doğrulanamıyorsa temizliğe devam edilmez.
2. **Exact ID kaydı, create-time**: temizlenecek her disposable kaynağın
   `id`'si, oluşturulduğu anda (API response'undan) kaydedilir — sonradan bir
   `LIKE`/isim deseniyle "tahmin edilmez".
3. **Silmeden önce dörtlü kimlik doğrulaması**: `id` + `name` + `slug` +
   (varsa) bağlı kullanıcı `email` + `created_at`'in HEPSİ, kaydedilen
   beklenen değerlerle eşleşmeli. Herhangi biri eşleşmezse işlem GUARD
   FAILED ile durur, hiçbir satır silinmez.
4. **Bağlı kayıt sayısı raporu**: silmeden önce, o organizasyona bağlı
   user/membership/project/TestWizardDraft/TestDefinition/TestVariant/
   SimulationRun/Report/PageAnalysis/DesignAsset/DesignGenerationJob/
   ChipReservation/ChipLedgerEntry/entitlement satır sayıları sayılıp
   loglanır/raporlanır (silme sonrası bu veri bir daha kurtarılamaz — bkz.
   CASCADE FK'ler).
5. **Kesin `WHERE id = :id`**: hiçbir zaman `LIKE`, `IN (subquery)` veya
   isim/etiket eşleştirmesiyle toplu silme yapılmaz — yalnızca tek tek,
   önceden bilinen birincil anahtarla.
6. **Transaction + guard-fail → rollback**: tüm kontroller (madde 3) tek bir
   transaction içinde yapılır; herhangi biri başarısızsa transaction
   `ROLLBACK` edilir, kısmi silme oluşmaz.
7. **Silme sonrası doğrulama**: yalnızca temizlenmesi istenen exact ID'lerin
   artık mevcut olmadığı, işlem öncesinde var olan DİĞER hiçbir satırın
   (özellikle işlemden önce doğrulanmış referans organizasyon sayısının)
   etkilenmediği ayrıca sorgulanıp doğrulanır.
8. Bu prosedürü uygulayan yardımcı script'ler (`scripts/`, test fixture'ları
   vb.) prefix/LIKE tabanlı bir silme yolunu KABUL ETMEMELİ — bkz.
   `backend/tests/test_exact_id_cleanup_guard.py` (yardımcı fonksiyonun
   `LIKE`/prefix girişini reddettiğini doğrular).

Bu kurallar yalnızca dev/smoke ortamı için değildir — production'da
organizasyon/tenant silme akışı (varsa) aynı ilkeleri (exact ID, çoklu alan
doğrulaması, transaction+rollback, silme-sonrası doğrulama) izlemelidir.
