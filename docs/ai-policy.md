# AI Politikası

Bu belge iki BAĞIMSIZ, ayrı yapılandırılan AI özelliğini kapsar:

1. **AI destekli açıklama** (`backend/app/services/ai_explanation.py`,
   `AI_*` ortam değişkenleri) - zaten hesaplanmış rapor metriklerini doğal
   dile çeviren, isteğe bağlı bir metin katmanı (bkz. aşağıdaki bölümler).
2. **AI ile tasarım varyantı üretimi** (`backend/app/services/design_generation.py`,
   `IMAGE_GENERATION_*` ortam değişkenleri) - A/B karşılaştırmasında
   "Tasarım B" için isteğe bağlı bir GÖRSEL üretim katmanı (bkz. "AI ile
   tasarım varyantı üretimi" bölümü, en altta).

Bu iki katman **tamamen ayrı yapılandırmalar** kullanır; birinin
etkin/yapılandırılmış olması diğerinin de çalıştığı anlamına GELMEZ. Her
ikisi de **analiz (analysis) DEĞİLDİR**: metin katmanı zaten hesaplanmış
sonuçları yorumlar, görsel katmanı yeni bir tasarım taslağı üretir; hiçbiri
görsel karşılaştırma/analiz motorunun (Paket 4, henüz bağlı değil) yerini
tutmaz.

Bilimsel iddia sınırları için bkz. [scientific-integrity.md](scientific-integrity.md);
ticari kurallar için bkz. [product-rules.md](product-rules.md);
simülasyon motoru için bkz. [methodology.md](methodology.md).

## AI Destekli Açıklama Katmanı

Bu belgenin bu bölümden "Denetim (audit) kaydı"na kadar olan kısmı,
`backend/app/services/ai_explanation.py` altında uygulanan, isteğe bağlı
ve kontrollü "AI destekli açıklama" katmanının kapsamını, veri işleme
kurallarını ve sınırlamalarını tanımlar.

## Kapsam

Bu katman **karar vermez**. Bir simülasyon raporunun (bkz.
`app.routers.reports`) zaten hesaplanmış, depolanmış metriklerini doğal
dile çevirir; kısa özet, metrik dayanakları, olası açıklamalar, önerilen
bir doğrulama deneyi ve sınırlamalar üretir. Hiçbir metrik, simülasyon
sonucu veya persona örneklemesi bu katman tarafından yeniden hesaplanmaz
ya da üretilmez.

Kapsam dışı: ChromaDB/RAG, vektör veritabanı, serbest sohbet botu. Bunlar
bu MVP için gerekli değildir ve eklenmemiştir.

## Sağlayıcı modeli ve varsayılan davranış

Önemli ayrım: **geliştiricinin kendi Claude Pro / Claude Code (IDE)
aboneliği bir API anahtarı sayılmaz.** Ürünün AI açıklama katmanı, bu
abonelikten bağımsız, kendi yapılandırmasıyla (`AI_PROVIDER` ortam
değişkeni) çalışır.

- `AI_PROVIDER=none` (**varsayılan**): Hiçbir ağ çağrısı yapılmaz, hiçbir
  API anahtarı gerekmez. `NoneProvider`, rapordaki metriklerden
  deterministik, kural tabanlı bir şablon açıklaması üretir. Ürün bu
  haliyle **eksiksiz** çalışır; AI açıklama bölümü her zaman doldurulur.
- `AI_PROVIDER=remote`: Yalnızca `AI_REMOTE_ENDPOINT` (ve isteğe bağlı
  `AI_REMOTE_API_KEY`) açıkça ayarlanmışsa aktif olur. Ayarlanmamışsa
  sistem otomatik ve sessizce `none` sağlayıcısına döner; bir yapılandırma
  eksikliği asla raporu bozmaz veya isteği başarısız kılmaz.

Sağlayıcı arayüzü (`ExplainabilityProvider` / `BaseExplainabilityProvider`)
sağlayıcıdan bağımsızdır; ileride başka bir uzak sağlayıcı adaptörü
eklenmek istenirse yalnızca bu arayüzü uygulaması yeterlidir.

## Veri minimizasyonu

Modele (uzak sağlayıcı kullanıldığında) veya şablon motoruna (varsayılan)
**asla** şu veriler gönderilmez:

- Ham HTML veya sayfa içeriği
- Cookie, oturum/erişim token'ı
- Form değerleri
- Kullanıcı e-postası veya başka bir kişisel/tanımlayıcı veri

Girdi şeması (`AIExplanationInput`, `extra="forbid"`) yalnızca şu
alanları taşıyabilir: rapor/run kimliği, model/kural/fixture sürümleri,
metodoloji referansı, yapılandırılmış metrik anlık görüntüleri
(`AIExplanationMetricSnapshot`), zaten anonimleştirilmiş kritik bulgu
metinleri ve anonim persona segment etiketleri (`key`, `label`, `count`,
`share`). Şema düzeyinde `extra="forbid"` olduğu için yukarıdaki
listedeki alanlardan biri (ör. `html`, `cookies`, `token`, `user_email`)
kazara eklenmeye çalışılırsa nesne oluşturulamaz ve `ValidationError`
fırlatılır (bkz. `backend/tests/test_ai_explanations.py`).

## Girdi/çıktı şeması

**Girdi** (`AIExplanationInput`): `calibration_status` alanı sabit olarak
`"uncalibrated"`dir ve değiştirilemez (Literal tip).

**Çıktı** (`AIExplanationOutput`) bölümleri, görev talimatındaki isimlerle
birebir eşleşir:

| Alan | Açıklama |
| --- | --- |
| `short_summary` | Kısa özet |
| `metric_basis` | Metrik dayanakları (her biri `metric_ids` taşır) |
| `possible_explanations` | Olası açıklamalar (her biri `metric_ids` taşır) |
| `suggested_verification_experiment` | Önerilen doğrulama deneyi |
| `limitations` | Sınırlamalar |
| `calibration_status` | Her zaman `"uncalibrated"` |

Ek denetim (audit) alanları: `prompt_version`, `provider`, `model_name`,
`generated_at`.

### Metrik dayanağı zorunluluğu

Her bulgu (`AIExplanationFinding`) bir veya daha fazla `metric_id`
referansı taşımak ZORUNDADIR (`min_length=1`); boş bir liste şema
düzeyinde reddedilir. Ayrıca `AIExplanationOutput.validate_against_input`,
her `metric_id`'nin gerçekten girdide (`AIExplanationInput.metrics` veya
`persona_segments`) var olduğunu doğrular. Bu iki kontrol birlikte,
**kaynaksız bir sayı veya simülasyonda hiç bulunmayan bir oranın**
kullanıcıya asla gösterilmemesini garanti eder: böyle bir çıktı
üretilirse (ör. uzak bir sağlayıcı "uydurma" bir metriğe atıfta
bulunursa) `AIExplanationValidationError` fırlatılır ve çağıran taraf
otomatik olarak güvenli şablon sağlayıcısına düşer.

## Yasaklı ifadeler

`app.engine.baseline.BANNED_CLAIM_PHRASES` (bkz.
[scientific-integrity.md](scientific-integrity.md)) ile birlikte, bu
katman ayrıca şunları yasaklar (`AI_BANNED_CLAIM_PHRASES`):

- "Gerçek kullanıcı gördü / davrandı" dili
- Kesin neden-sonuç iddiaları
- "Kanıtlandı" ifadesi
- "Gerçek göz takibi" iddiaları
- Hukuki veya erişilebilirlik sertifikası iddiaları
- "Gerçek pazar talebi" iddiaları
- Belirsizliği gizleyen/yok sayan ifadeler (`limitations` alanı her
  çıktıda zorunlu ve boş bırakılamaz; sistem promptu modele bunu
  gizlememesini açıkça söyler)

Bu ifadelerden biri üretilen metinde tespit edilirse
(`assert_no_banned_ai_claims`) çıktı reddedilir ve güvenli şablona
düşülür; kullanıcıya asla yasaklı bir ifade içeren metin gösterilmez.

## Zaman aşımı, yeniden deneme, maliyet sınırı ve güvenli düşüş (fallback)

Uzak sağlayıcı (`RemoteHttpProvider`):

- `AI_REQUEST_TIMEOUT_SECONDS` ile sınırlı bir `httpx` zaman aşımı kullanır.
- `AI_MAX_RETRIES` kadar sınırlı yeniden deneme yapar (yalnızca ağ/zaman
  aşımı hatalarında; şema hatalarında yeniden denemenin faydası
  olmadığından hemen durur).
- `AI_MAX_OUTPUT_TOKENS` ile çıktı/maliyet sınırı talep eder.

`generate_explanation` orkestratörü, sağlayıcı hatası (zaman aşımı, ağ
hatası, şema dışı çıktı, yasaklı ifade, bilinmeyen `metric_id`) durumunda
**hiçbir istisna sızdırmaz**: otomatik olarak `NoneProvider` şablonuna
döner ve üretim daima geçerli, doğrulanmış bir çıktıyla sonuçlanır. Bir AI
sağlayıcı arızası hiçbir zaman raporun kendisini bozmaz.

## Denetim (audit) kaydı

Her üretim bir `AuditLog` kaydı bırakır (`action=ai_explanation_generated`,
`entity_type=report`). Kayıt yalnızca şunları içerir: `prompt_version`,
`provider`, `model_name`, `generated_at`, kaynak `report_id` /
`simulation_run_id`, `calibration_status` ve fallback uygulanıp
uygulanmadığı. **Hiçbir gizli anahtar veya ham hassas içerik loglanmaz.**

## UI görünümü

Rapor arayüzünde bu bölümün başlığı **"AI destekli açıklama"**dır; asla
"AI kararı" olarak adlandırılmaz. Bölümün altında, otomatik üretildiğini
ve bir uzman değerlendirmesi/doğrulaması gerektirdiğini belirten bir not
her zaman gösterilir (bkz. `app.services.ai_explanation.AI_SECTION_UI_SUBLABEL`).

## Testler

Bkz. `backend/tests/test_ai_explanations.py`: sağlayıcı kapalıyken
(varsayılan) tam çalışırlık, çıktı şema doğrulaması, metrik dayanağı
zorunluluğu, yasaklı ifadelerin reddi, girdiye ham/hassas veri
sızmaması (redaksiyon), sağlayıcı zaman aşımında/hatasında güvenli
şablona düşme, simülasyonda bulunmayan ("uydurma") bir metriğe atıf
yapan çıktının asla dışarı sızmaması, tenant izolasyonu ve denetim
kaydının gizli içerik taşımaması.

## AI ile Tasarım Varyantı Üretimi

Bu bölüm, A/B karşılaştırmasında yalnızca "Tasarım B" tarafı için
sunulan, isteğe bağlı AI ile tasarım varyantı üretme özelliğini kapsar
(bkz. `backend/app/services/design_generation.py`,
`backend/app/routers/design_generations.py`,
`frontend/src/pages/wizard/AiDesignGenerator.tsx`).

### Sağlayıcı modeli ve varsayılan davranış

Yukarıdaki metin/açıklama katmanından **TAMAMEN AYRI** bir yapılandırma
kullanır (`IMAGE_GENERATION_*` ortam değişkenleri); bir metin sağlayıcısının
görsel de üretebileceği VARSAYILMAZ. Geliştiricinin kendi Claude Pro/IDE
aboneliği burada da bir API anahtarı SAYILMAZ.

- `IMAGE_GENERATION_PROVIDER=none` (**varsayılan**): Özellik tamamen
  DEVRE DIŞIDIR. `NoneProvider` hiçbir görsel üretmez - metin açıklama
  katmanının aksine, burada "güvenli/deterministik bir sahte çıktı" diye
  bir şey YOKTUR (bir görüntü uydurmanın güvenli bir yolu olamaz).
  Kullanıcı arayüzünde "AI ile oluştur" seçeneği açıklamalı biçimde devre
  dışı gösterilir: *"AI görsel üretim sağlayıcısı henüz yapılandırılmadı.
  Tasarım B için URL veya ekran görüntüsü kullanabilirsiniz."*
- `IMAGE_GENERATION_PROVIDER=remote`: Bu depoda **henüz gerçek bir
  sağlayıcı sözleşmesi (endpoint/istek-yanit şeması) doğrulanmamıştır**.
  `RemoteHttpProvider` bilinçli olarak eksik bırakılmıştır
  (`generate()` `NotImplementedError` fırlatır); rastgele bir API formatı
  UYDURULMAMIŞTIR. Gerçek bir sağlayıcı seçildiğinde, bu sınıfın içi
  doldurulmalı ve bu belge güncellenmelidir.

### Referans görsel ve tetikleyici koşul

AI varyantı üretimi her zaman bir referans görsele ihtiyaç duyar:

- Tasarım A bir ekran görüntüsüyse, o `DesignAsset` referans olarak
  kullanılır.
- Tasarım A bir URL'yse, güvenilir bir referans ekran görüntüsü bu
  paket kapsamında henüz hazır DEĞİLDİR (URL'den otomatik referans
  görsel üretimi Paket 4'ün analyzer bağlantısını gerektirir); bu
  durumda AI seçeneği açıklamalı biçimde engellenir: *"AI varyant
  oluşturmak için önce Tasarım A'nın ekran görüntüsünü yükleyin."*
  Kullanıcının girdiği URL'nin kendisi hiçbir zaman sağlayıcıya
  gönderilmez ve sağlayıcının bu URL'yi ziyaret edeceği varsayılmaz.

### Gizlilik ve açık kullanıcı onayı

Bir tasarım ekran görüntüsü uzak bir AI sağlayıcısına gönderilmeden önce,
kullanıcıya arayüzde açıkça gösterilir: görselin hangi amaçla
gönderileceği, yapay zekânın tasarımı yeniden yorumlayabileceği, hassas
müşteri/kişisel veri içeren ekran görüntülerinin yüklenmemesi gerektiği,
çıktının çalışan HTML/CSS değil yalnızca görsel bir taslak olduğu ve
sonucun doğrulanması gerektiği. Bu onay, draft'ın genel URL analiz yetki
onayından (`authorization_confirmed`) **AYRIDIR**; her uzak aktarım için
ayrıca ve açıkça verilmelidir (`POST /api/design-generations` gövdesindeki
`authorization_confirmed` alanı) - onay olmadan hiçbir iş (job)
oluşturulmaz.

### Üretilen görselin güvenliği

Sağlayıcıdan dönen çıktıya HİÇBİR ZAMAN güvenilmez. Sonuç, Paket 1'in
yüklenen tasarım görselleriyle **AYNI** güvenli doğrulama hattından
(`app.services.design_assets.store_generated_asset` →
`_decode_and_validate`/`_reencode_without_metadata`) geçirilir: gerçek
decode, yalnızca PNG/JPEG/WebP kabulü (SVG her zaman reddedilir),
boyut/piksel sınırları, çoklu-kare (animasyon) reddi, metadata temizleme,
yeniden encode, SHA-256, süreli `DesignAsset` saklama ve tenant izolasyonu.
Doğrulama başarısız olursa iş `failed` işaretlenir ve HİÇBİR `DesignAsset`
oluşturulmaz - asla yarım/geçersiz bir sonuç kalıcı hale gelmez.

### İş (job) modeli ve saklama

Üretim, `DesignGenerationJob` (`queued`/`running`/`succeeded`/`failed`/
`cancelled`) ile takip edilen, senkron olmayan bir iştir (bkz.
`docs/architecture.md` "arq" deseni). İş kaydı, kaynak/sonuç
`DesignAsset` kimliklerini, sağlayıcı/model adını ve (varsa) güvenli bir
hata mesajını taşır; sağlayıcının ham hata metni veya API anahtarı asla
saklanmaz/loglanmaz.

**Prompt saklama ve redaksiyon**: kullanıcının serbest metin talebi
(`prompt`), "regenerate"/"talebi düzenle" akışını ve denetimi
desteklemek için iş kaydıyla birlikte saklanır. Bu metin, kullanıcının
kendi UI-değişikliği talebidir (ham sayfa içeriği/PII değildir), ancak
yine de sınırlı bir süre için saklanır: iş, ilişkili `DesignAsset` ile
AYNI saklama süresini kullanır (`DESIGN_ASSET_RETENTION_SECONDS`,
varsayılan 24 saat) ve bu süre dolduğunda `prompt` alanı `NULL`'a
çekilir (satırın kendisi, denetim amacıyla kalır - bkz.
`app.services.design_generation.purge_expired_jobs`).

### Draft'a bağlanma (kabul) kuralı

Bir AI sonucu, kullanıcı **açıkça** "Tasarım B olarak kullan"a tıklayana
kadar sihirbaz taslağına (draft) HİÇBİR ŞEKİLDE bağlanmaz
(`new_design_asset_id` değiştirilmez). Reddetme, yeniden üretme veya
talebi düzenleme, taslakta zaten kayıtlı olan manuel bir Tasarım B
kaynağını (URL/ekran görüntüsü) SİLMEZ veya DEĞİŞTİRMEZ. Backend, kabul
edilen `new_design_asset_id`nin gerçekten ilgili işin `result_asset_id`si
olduğunu ve işin `succeeded` durumda olduğunu ayrıca doğrular (bkz.
`app.services.test_wizard.validate_ai_generation_ownership`) - yalnızca
UI kontrolüne güvenilmez.

### AI çıktısının sınırları (UI'da her zaman gösterilir)

Üretilen çıktı çalışan bir web sitesi değildir; HTML/CSS üretildiği asla
iddia edilmez, etkileşimler çalışmaz. Metinler, logolar ve görsel
ayrıntılar hatalı değişmiş olabilir. Sonuç bir tasarım fikri/prototip
taslağıdır - gerçek kullanıcı testi, gerçek göz takibi veya gerçek
tıklama verisi DEĞİLDİR.

### A/B launch koruması

Bu pakette yalnızca URL/URL A/B karşılaştırması gerçek bir simülasyon
başlatabilir. Tasarım A veya Tasarım B tarafında ekran görüntüsü ya da
Tasarım B tarafında `ai_generated` kaynağı varsa (AI sonucu kabul edilmiş
olsa bile), launch backend tarafından açıkça engellenir - hiçbir
`TestDefinition`/`SimulationRun`/Chip rezervasyonu yan etkisi üretilmeden
(bkz. `app.services.test_wizard.AB_VISUAL_SOURCE_LAUNCH_BLOCKED_MESSAGE`).
Bu koruma, Paket 4'ün görsel karşılaştırma motoru bağlanana kadar
kaldırılmaz.

### Maliyet

Bu pakette gerçek bir sağlayıcı bağlanmadığı ve Chip fiyatlandırması
eklenmediği için, AI ile tasarım varyantı üretimi hiçbir Chip
tüketmez/rezerve etmez. `ai_design_variant_generation` bir katalog
anahtarı adayı olarak önerilmiştir; fiyat/rezervasyon/iade davranışı
ürün kararı gerektirir ve henüz uygulanmamıştır (bkz. proje kararları
raporu).

### Testler

Bkz. `backend/tests/test_design_generations.py` (servis katmanı, sahte/
mock sağlayıcı ile) ve `backend/tests/test_design_generations_api.py`
(API + sihirbaz entegrasyonu): sağlayıcı kapalıyken hiçbir iş/asset/Chip
etkisi oluşmaması, onay/prompt doğrulaması, tenant izolasyonu, kaynak
asset süresi dolmuş/silinmişse reddi, geçersiz/SVG/aşırı büyük sağlayıcı
çıktısının reddi, yeniden deneme sınırı, iptal, kabul/reddet/yeniden
üretim akışlarının draft durumunu doğru şekilde koruması ve launch
korumasının AI kaynaklı sonuçlar için de çalışması.
