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
