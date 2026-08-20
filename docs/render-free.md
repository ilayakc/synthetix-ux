# Render ücretsiz sunum kurulumu

Bu dosya yalnızca kısa süreli proje sunumu ve değerlendirme içindir. Render
kaynaklarının tamamı `render.yaml` içinde açıkça `plan: free` olarak tanımlıdır.
Ücretli `worker`, `private service`, disk veya ek instance oluşturulmaz.

## Mimari

Render ücretsiz planında **Background Worker instance tipi yoktur** ve ücretsiz
servisler yalnızca herkese açık (public) trafik alır. Bu yüzden topoloji
**yalnızca üç kaynaktan** oluşur:

- `synthetix-ux-ily` (tek `type: web`, Docker) — aynı container içinde:
  - nginx (React frontend + `/api` reverse proxy)
  - FastAPI backend (uvicorn)
  - ARQ simülasyon worker'ı (`arq app.worker.WorkerSettings`)
  - Playwright/Chromium analyzer — **yalnızca loopback**'te,
    `127.0.0.1:8100` üzerinde dinler
- `synthetix-ux-ily-db` — ücretsiz PostgreSQL 16
- `synthetix-ux-ily-queue` — ücretsiz Redis uyumlu Key Value

Bu dört süreç (`backend`, `worker`, `analyzer`, `nginx`) tek container içinde
`deploy/render_free_start.py` → `app.render_free_launcher` supervisor'ı
tarafından başlatılır. Supervisor **fail-closed**'dur: çocuk süreçlerden biri
beklenmedik biçimde ölürse container non-zero kodla çıkar ve Render onu yeniden
başlatır (API asla worker ölüyken tek başına ayakta kalmaz).

Backend ve worker analyzer'a `ANALYZER_BASE_URL=http://127.0.0.1:8100`
üzerinden ulaşır. Frontend ve API **aynı origin** üzerinden sunulur; böylece
auth/CSRF cookie'leri iki farklı alan adı arasında taşınmaz. `ALLOWED_HOSTS` ve
`CORS_ALLOWED_ORIGIN`, başlangıç betiği tarafından Render'ın verdiği gerçek
hostname'den (`RENDER_EXTERNAL_HOSTNAME`) türetilir.

> **Neden ayrı bir analyzer servisi YOK.** Analyzer daha önce ayrı bir ücretsiz
> web servisiydi. Ücretsiz plan private trafik sunamadığı için bu servise
> yalnızca public internet üzerinden, Render/Cloudflare edge'inin arkasından
> erişilebiliyordu; edge, istekler analyzer uygulamasına ulaşmadan **HTTP 429**
> döndürüyordu. Analyzer artık container içinde loopback'te çalıştığı için bu
> public edge tamamen ortadan kalktı. **Ayrı bir analyzer web servisi
> oluşturmayın** — bu, 429 sorununu geri getirir ve blueprint'i bozar.

## Render panelinde kurulum

1. Repoyu private GitHub deposuna gönderin. `.env` dosyasını **göndermeyin**
   (`.gitignore` bunu zaten dışlar).
2. Render Dashboard'da **New > Blueprint** seçin.
3. GitHub deposunu bağlayın; Blueprint yolu olarak `render.yaml` kullanın.
4. Önizleme ekranında **tek web servisinin**, PostgreSQL'in ve Key Value'nun
   tamamında **Free** yazdığını doğrulayın. Ücret ya da ikinci bir web/worker
   servisi görünürse oluşturmayın.
5. Ana web servisi `synthetix-ux-ily` için **Environment** sayfasını açın.
   `OPENAI_API_KEY` değişkeninin değerine OpenAI API anahtarını yapıştırın.
   Anahtarı `render.yaml`, `.env`, GitHub veya ekran görüntüsüne eklemeyin.
   Blueprint'teki `sync: false` tanımı, sırrın GitHub'a yazılmadan Render'da
   tutulmasını sağlar. (AI raporu ve AI ısı haritası bu tek anahtarı paylaşır.)
6. **Apply** ile kaynakları oluşturun ve web deploy'unun tamamlanmasını
   bekleyin.
7. Sağlık kontrolünü açın: `https://synthetix-ux-ily.onrender.com/api/health`
   (Render health check yolu da budur). Analyzer için ayrı bir public health
   adresi **yoktur** — loopback'te çalışır.
8. Sunumdan birkaç dakika önce ana adresi açarak uyuyan servisi uyandırın
   (cold start). Ardından giriş, yeni test, URL analizi ve gerçek AI raporu
   akışını bir kez baştan sona çalıştırın.

Deploy manuel tetiklenecekse Render Dashboard'da servisin **Manual Deploy >
Deploy latest commit** seçeneğini kullanın; ilerleyişi ve olası hataları
**Logs** sekmesinden izleyin.

## Bilinen ücretsiz plan sınırları

- **Cold start:** Web servisi boşta kaldığında uyur; ilk istek soğuk başlar ve
  onlarca saniye sürebilir.
- **Tek instance:** Backend, worker ve analyzer aynı instance'tadır. Instance
  uyurken worker ve analyzer da durur.
- **512 MB RAM:** Ölçümlere göre ağır sayfalar (tam-sayfa lazy-scroll + büyük
  ekran görüntüsü) ~700 MB tepe belleğe ulaşıp 512 MB'a sığmaz. Bu yüzden
  analyzer varsayılan olarak **lite modda** çalışır: yalnızca ilk
  ekran/viewport yakalanır (tam-sayfa scroll yapılmaz), ekran görüntüsü
  viewport yüksekliğiyle sınırlıdır, font/medya kaynakları engellenir ve
  eşzamanlılık `1`'e sabitlenir (`MAX_CONCURRENT_ANALYSES=1`). Bellek bol bir
  ortamda tam mod `LITE_MODE=false` ile açılabilir. Ayrıntı için bkz.
  `analyzer/app/config.py` ve `analyzer/app/browser.py`.
- **Viewport tabanlı analiz:** Lite modda sonuçlar **görünür ilk ekranı** temel
  alır; viewport dışında kalan (aşağı kaydırıldığında görünen) içerik analize
  girmez. Rapor bunu `analysis_mode="lite"` / `analysis_limited=true` alanlarıyla
  taşır ve arayüzde hata olmayan bir "hafif analiz" bilgi notu gösterilir.
- **Bellek koruması:** Analiz sırasında container belleği eşiği aşarsa
  (kernel OOM-kill'den önce) browser derhal kapatılır ve analiz yeniden
  denenebilir tipli bir hatayla iptal edilir — böylece ağır bir sayfa tüm
  container'ı çökertmez.
- **Veri kalıcı değildir:** Ücretsiz PostgreSQL süreli ve yedeksizdir; sunum
  verilerini kalıcı kabul etmeyin. Ücretsiz Key Value yeniden başlatıldığında
  kuyruk verileri kaybolabilir.
- **Saklama süreleri:** Ekran görüntülerinin veritabanını doldurmaması için
  bağlı rapor görüntüleri 7 gün, geçici görüntüler 1 gün tutulur.
- **Maliyet:** Render kaynakları ücretsiz olsa da **OpenAI API kullanımı ayrıca
  ücretlidir**. Model, akıl yürütme seviyesi ve çıktı sınırı `render.yaml`
  içinde maliyet kontrollü varsayılanlarla tanımlıdır; kullanım/harcama
  limitleri OpenAI panelinden ayrıca izlenmelidir.

Ayrıca CAPTCHA, zorunlu giriş, bot koruması veya ağır JS uygulaması olan bazı
sayfalar pasif analizde tam sonuç vermeyebilir; bu bir kısıt olarak dürüstçe
raporlanır, sahte bir "başarılı" snapshot üretilmez.

## Sorun giderme

Loglar Render Dashboard > `synthetix-ux-ily` > **Logs** altındadır. Yapılandırılmış
loglar JSON'dur; `role` alanı süreci ayırt eder (`api` / `worker` / `analyzer`).
Bir çalıştırmayı izlemek için loglarda `run_id` veya `page_analysis_id` ile arayın.

- **Uzun süre QUEUED/RUNNING kalıyor:** Genellikle cold start ya da worker'ın
  analyzer henüz hazır değilken işi tüketmesidir. Analyzer'a bağlantı hatası
  (`analyzer_unavailable`) kalıcı, gecikmeli yeniden deneme olarak ele alınır
  (varsayılan ~15 sn backoff); iş sessizce kaybolmaz. Reaper, uzun süre takılı
  kalan çalıştırmaları da kurtarır.
- **`analyzer_memory_pressure`:** Chromium açılmadan önce yeterli boş bellek
  yok ya da analiz sırasında bellek eşiği aşıldı. Yeniden denenebilir; ağır bir
  URL ise daha hafif bir sayfayla deneyin.
- **`browser_crashed`:** Chromium süreci beklenmedik biçimde sonlandı (çoğu kez
  bellek baskısı). Yeniden denenebilir tipli bir hatadır.
- **AI ısı haritasında boş sonuç:** Sayfada değerlendirilebilecek net bir
  etkileşim adayı yoksa hotspot **uydurulmaz**; sonuç bilinçli olarak boştur.
  Bu bir hata değildir. Koordinatlar her zaman analyzer'ın doğruladığı gerçek
  adaylardan gelir.
- **AI raporu "failed"/"not found":** `OPENAI_API_KEY` ayarlı mı, model adı
  geçerli mi ve OpenAI hesabında kota var mı kontrol edin. Pipeline başlatma
  hataları kalıcı biçimde işaretlenir; retry hakkı çift tüketilmez.
- **HTTP 429 görüyorsanız:** Bu genellikle **ayrı bir public analyzer servisi**
  oluşturulmasından kaynaklanır. Analyzer ayrı bir servis DEĞİLDİR; yalnızca
  container içinde loopback'te çalışır (yukarıdaki "Mimari" notuna bakın).

## Yerel Docker etkilenmez

`compose.yaml`, `compose.prod.yaml` ve mevcut geliştirme Dockerfile'ları aynı
şekilde çalışmaya devam eder; yerel geliştirmede analyzer ayrı bir container'dır
(host'a port yayınlamaz). `Dockerfile.render-free` ve buradaki tek-container
topolojisi **yalnızca** Render'ın ücretsiz demo servisi tarafından kullanılır.
