# AI Politikası: AI Destekli Açıklama Katmanı

Bu belge, `backend/app/services/ai_explanation.py` altında uygulanan,
isteğe bağlı ve kontrollü "AI destekli açıklama" katmanının kapsamını,
veri işleme kurallarını ve sınırlamalarını tanımlar. Bilimsel iddia
sınırları için bkz. [scientific-integrity.md](scientific-integrity.md);
ticari kurallar için bkz. [product-rules.md](product-rules.md);
simülasyon motoru için bkz. [methodology.md](methodology.md).

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
