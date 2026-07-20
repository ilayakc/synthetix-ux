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

## Prompt 7 motoruyla ilişki

`analyzer`'ın ürettiği sürümlü `page_feature_snapshot` (bkz.
`PAGE_FEATURE_SNAPSHOT_VERSION` benzeri `SNAPSHOT_VERSION` alanı,
`analyzer/app/schemas.py`), ileride Prompt 7 heuristic motorunun
(`backend/app/engine/baseline.py`) şu anda kullandığı sentetik
`backend/app/engine/fixtures.py` yer tutucusunun yerini alabilecek şekilde
tasarlanmıştır. **Bu entegrasyon bu aşamanın kapsamında değildir**: motor,
bu snapshot'ı henüz tüketmez; yalnızca sema ve üretim hattı hazırlanmıştır.
