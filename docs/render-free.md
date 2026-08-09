# Render ücretsiz sunum kurulumu

Bu dosya yalnızca kısa süreli proje sunumu ve değerlendirme içindir. Render
kaynaklarının tamamı `render.yaml` içinde açıkça `plan: free` olarak tanımlıdır.
Ücretli `worker`, `private service`, disk veya veritabanı oluşturulmaz.

## Mimari

- `synthetix-ux-ily`: nginx + React frontend + FastAPI backend + ARQ worker
- `synthetix-ux-ily-analyzer`: token korumalı, internete açık analyzer
- `synthetix-ux-ily-db`: ücretsiz PostgreSQL
- `synthetix-ux-ily-queue`: ücretsiz Redis uyumlu Key Value
- AI raporu: production istisnası açıkça belirtilmiş deterministik `mock`

Frontend ve API aynı origin üzerinden sunulur. Böylece auth/CSRF cookie'leri
iki farklı Render alan adı arasında taşınmaz. `ALLOWED_HOSTS` ve
`CORS_ALLOWED_ORIGIN`, başlangıç betiği tarafından Render'ın verdiği gerçek
hostname'den türetilir.

## Render panelinde kurulum

1. Repoyu private GitHub deposuna gönderin. `.env` dosyasını göndermeyin.
2. Render Dashboard'da **New > Blueprint** seçin.
3. GitHub deposunu bağlayın; Blueprint yolu olarak `render.yaml` kullanın.
4. Önizleme ekranında iki web servisinin, PostgreSQL'in ve Key Value'nun
   tamamında **Free** yazdığını kontrol edin. Ücret görünürse oluşturmayın.
5. **Apply** ile kaynakları oluşturun ve iki web deploy'unun tamamlanmasını
   bekleyin.
6. Sağlık kontrollerini açın:
   - `https://synthetix-ux-ily.onrender.com/api/health`
   - `https://synthetix-ux-ily-analyzer.onrender.com/health`
7. Sunumdan birkaç dakika önce iki adresi de açarak uyuyan servisleri
   uyandırın. Ardından giriş, yeni test, analyzer ve mock AI raporu akışını
   bir kez tamamlayın.

Servis adı Render'da kullanılamıyorsa hem `name` alanını hem de ana servisteki
`ANALYZER_BASE_URL` değerini yeni analyzer adresine göre birlikte değiştirin.

## Bilinen ücretsiz plan sınırları

- Web servisleri boşta kaldığında uyur; ilk istek soğuk başlayabilir.
- Backend ve worker aynı instance'tadır. Instance uyurken worker da durur.
- Analyzer'ın Chromium süreci RAM sınırına takılabilir; eşzamanlılık bu nedenle
  `1` olarak ayarlanmıştır.
- Ücretsiz PostgreSQL süreli ve yedeksizdir. Sunum verilerini kalıcı kabul
  etmeyin.
- Ücretsiz Key Value yeniden başlatıldığında kuyruk verileri kaybolabilir.
- Ekran görüntülerinin veritabanını hızla doldurmaması için bağlı rapor
  görüntüleri 7 gün, geçici görüntüler 1 gün tutulur.
- `mock` AI gerçek model çıktısı değildir; sunumda sentetik/demo sonucu olarak
  açıkça anlatılmalıdır.

## Yerel Docker etkilenmez

`compose.yaml`, `compose.prod.yaml` ve mevcut geliştirme Dockerfile'ları aynı
şekilde çalışmaya devam eder. `Dockerfile.render-free` yalnızca Render'ın
ücretsiz demo servisi tarafından kullanılır.
