# Ürün Kuralları (Değişmez)

Bu kurallar, ürünün ticari ve bilimsel dürüstlük çerçevesini tanımlar ve
sonraki aşamalarda yazılacak iş mantığının uyması gereken sabitlerdir. Bu
belge yalnızca kuralları kayıt altına alır; bu aşamada herhangi bir iş
mantığı, veritabanı tablosu veya cüzdan mekanizması uygulanmamıştır.

- Yeni kaydolan her şirket, 0 (sıfır) Chip bakiyesiyle başlar.
- En fazla 1.000 persona içeren bir proje için 1 adet ücretsiz temel UX testi
  hakkı tanınır.
- 1 adet ücretsiz erişilebilirlik ön kontrolü hakkı tanınır.
- Gelişmiş modüller (ör. ileri simülasyonlar, genişletilmiş raporlama gibi
  temel ücretsiz hakların dışında kalan özellikler) Chip harcaması gerektirir.
- Sentetik test sonuçları hiçbir koşulda gerçek insan kullanıcı davranışı
  olarak sunulamaz, pazarlanamaz veya bu şekilde çağrıştırılamaz.
- A/B karşılaştırmasında "Tasarım A" ve "Tasarım B" tarafları bağımsız olarak
  URL, yüklenen ekran görüntüsü veya (yalnızca Tasarım B için) AI ile üretilen
  bir varyant olabilir; ancak görsel (URL dışı) bir kaynak, görsel
  karşılaştırma motoru bağlanana kadar gerçek bir test BAŞLATAMAZ (bkz.
  docs/architecture.md "Servisler" bölümü ve docs/ai-policy.md "A/B launch
  koruması").
- AI ile tasarım varyantı üretimi, gerçek bir görsel üretim sağlayıcısı
  yapılandırılmadan (bkz. docs/ai-policy.md) kullanıcıya sunulmaz; hiçbir
  placeholder/sahte/kopyalanmış görsel "AI sonucu" gibi gösterilmez ve bu
  özelliğin Chip maliyeti henüz belirlenmemiştir (uygulanana kadar herhangi
  bir Chip düşülmez).

## Ziyaretçi ve trafik analitiği (metrik tanımları)

Aşağıdaki kavramlar birbirinden **ayrı** ölçülür ve farklı isimlerle tekrar
gösterilmez (bkz. `app.routers.analytics`, docs/security.md "Ziyaretçi ve
trafik analitiği"):

- **Sayfa görüntüleme (page_view):** her `page_view` olayı bir görüntülemedir.
- **Benzersiz ziyaretçi:** `page_view` olaylarındaki **farklı visitor ID**
  sayısı. "Bugün/son 7 gün/son 30 gün" pencereleri, seçilen tarih aralığından
  bağımsız, sabit takvim pencereleridir.
- **Kayıt (signup_completed):** kayıt akışının tamamlanması.
- **Başarılı giriş (login_succeeded):** her başarılı kayıt/parola/demo girişi.
- **Aktif kullanıcı:** son 30 günde en az bir başarılı girişi olan kullanıcı.
- **Ziyaretçiden kayda dönüşüm:** seçilen aralıktaki yeni kullanıcı / toplam
  benzersiz ziyaretçi.
- **Kayıttan ilk girişe dönüşüm:** en az bir başarılı girişi olan kullanıcı /
  toplam kullanıcı.

**Kaynak/kampanya ilişkilendirme (attribution):** her ziyaretçi için
**first-touch** (ilk oturumun kaynağı, bir daha değişmez) ve **last-touch**
(en son oturumun kaynağı) ayrı tutulur. Kayıt anında bu değerler kullanıcıya ve
oluşturduğu organizasyona denormalize kopyalanır. Kullanıcı/şirket gösterim
adları her zaman doğrulanmış `memberships`/`organizations` join'lerinden gelir;
istemciden gelen değerlere güvenilmez.

**Varsayılan politika (değişmez):** analitik hem geliştirmede hem production'da
`ANALYTICS_ENABLED=true`, `ANALYTICS_REQUIRE_CONSENT=true` (opt-in, gizlilik
öncelikli) varsayılanıyla gelir. İzin verilmeden yalnızca sistemin çalışması
için gerekli sunucu-taraflı iş olayları (signup/login/organizasyon) — pazarlama
attribution'ı olmadan — tutulur. Saklama süresi `ANALYTICS_RETENTION_DAYS`
(varsayılan 180 gün) olup güvenli bir cleanup cron'uyla uygulanır. Sistem
`ANALYTICS_ENABLED=false` ile tamamen kapatılabilir. Hiçbir analitik değer
gerçek insan davranışı olarak sunulmaz; bu ölçümler yalnızca operasyonel
trafik/erişim istatistikleridir.
