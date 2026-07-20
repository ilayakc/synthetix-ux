# Metodoloji: Heuristic Baseline Simulasyon Motoru

Bu belge, `backend/app/engine/` altindaki sentetik simulasyon motorunun
girdilerini, kurallarini, varsayimlarini, belirsizlik uretim yontemini ve
sinirlamalarini aciklar. Bilimsel iddia sinirlari icin bkz.
[docs/scientific-integrity.md](scientific-integrity.md); ticari/urun
kurallari icin bkz. [docs/product-rules.md](product-rules.md).

## Ozet ve kapsam

Bu motor **gercek insan davranisi uretmez**. Kalibrasyon verisi (gercek
kullanilabilirlik testi sonuclariyla karsilastirma) yoktur; her calistirma
`calibration_status=uncalibrated` olarak isaretlenir ve bu durum hicbir
zaman otomatik olarak degismez (bkz. "Kalibrasyon plani" bolumu). Motor
Playwright veya baska bir gercek tarayici/crawler kullanmaz, bir LLM/AI
saglayicisina baglanmaz, Kafka ya da ChromaDB kullanmaz (bkz. README
"Kapsam disi").

Tum sonuclar **"sentetik senaryo tahmini"** olarak sunulur: erken asama
fikir uretimi/hipotez olusturma icin bir on inceleme araci olabilir, ancak
gercek kullanilabilirlik testinin, A/B testinin veya pazar arastirmasinin
yerini **almaz**.

## Girdi kaynagi: `page_feature_snapshot`

Bu asamada gercek bir sayfa hicbir zaman ziyaret edilmez (Playwright yok).
Bunun yerine `app.engine.fixtures.get_page_feature_snapshot(url, role)`,
verilen `url` + `role` (`existing`/`new`/`primary`) ciftinden **sha256
tabanli, deterministik** bir sentetik sayfa ozellik kumesi turetir:
gezinme derinligi (`nav_depth`), birincil CTA sayisi, form alani sayisi,
kelime sayisi, ortalama cumle uzunlugu, baslik sayisi, kontrast oranlari ve
mobil uyum. Ayni `(url, role)` her zaman ayni ozellik kumesini uretir.

Bu, **gercek sayfa analizinin yerini tutmaz**; yalnizca motorun sabit
semali, surumlu (`PAGE_FEATURE_SNAPSHOT_VERSION`) bir girdiye ihtiyaci
oldugu icin kullanilan bir yer tutucudur. Gercek bir Playwright tabanli
cikarim hattina gecildiginde bu fonksiyonun ic uygulamasi degisecek, ancak
sema (alan adlari/turleri) ve surum alani ayni kalacak sekilde
tasarlanmistir.

Persona ornegi (`input_snapshot.persona_sample`, varsa) `app.services.
personas.sample_cohorts` tarafindan uretilen deterministik cohort/segment
ozetidir (bkz. o modulun dokstring'i); bu motor personalarin *nasil
davranacagini* burada hesaplar ("Prompt 7" notu artik gecerli degildir).

## Kurallar: `app.engine.rules_config`

Tum agirliklar `HeuristicRulesConfig` icinde, `app.services.pricing` ile
ayni desende **surumlu** olarak tutulur (`RULES_VERSIONS`,
`CURRENT_RULES_VERSION`). Bir calistirma, hangi surume gore
hesaplandigini `SimulationRun.rules_version` alaninda saklar; kurallar
sonradan degisse bile eski sonuclar geriye donuk olarak degismez.

## Metrikler ve formuller (surum `2026.1`)

Asagidaki tum nokta tahminleri **dogrudan kural agirliklarindan**
hesaplanir; hicbir nokta tahmini rastgele sayidan turetilmez ("Rastgele
sayı üretmek tek başına model değildir" ilkesi, bkz. proje talimatlari).

- **Gorev tamamlama olasiligi**: taban olasilik (`base_completion_probability`)
  eksi (gezinme adimi × ceza) eksi (form alani × ceza) eksi (birincil CTA
  ilk ekranda degilse sabit ceza); sonuc persona orneginin dijital
  yatkinlik dagilimina gore agirlikli bir carpanla olceklenir (`digital_literacy_multiplier`).
- **Tahmini gorev suresi (saniye)**: taban sure + (gezinme adimi ×
  saniye) + (form alani × saniye) + (100 kelime basina saniye); dijital
  yatkinlik carpaniyla olceklenir. Dagilim, ucgen (triangular) bir dagilim
  olarak `low/mode/high` ve `p10/p50/p90` yuzdelikleriyle raporlanir.
- **Yanlis tiklama olasiligi**: taban olasilik + (fazladan birincil CTA
  sayisi × ceza) + (ortalama kontrast orani WCAG AA esiginin altindaysa
  sabit ceza).
- **Terk (abandonment) olasiligi**: taban olasilik + (gezinme adimi ×
  ceza) + (form alani × ceza) + (sayfa mobil uyumlu degilse sabit ceza).
- **Okunabilirlik skoru (0-100)**: taban skor eksi (400 kelimenin
  uzerindeki her kelime icin kucuk bir ceza) eksi (cumle basina 18
  kelimenin uzerindeki uzunluk icin ceza) arti (baslik sayisi basina
  sinirli bir bonus).
- **Kontrast kontrolu**: ortalama kontrast orani WCAG AA esigi (4.5:1) ile
  karsilastirilir; `pass`/`avg_ratio`/`min_ratio`/`threshold` alanlariyla
  raporlanir. Bu, gercek bir renk analizi degil, sentetik `page_feature_snapshot`
  uzerinden yapilan bir kontrol oldugu icin bilgilendirici (indicative)
  kabul edilmelidir.
- **Bolgesel tahmini ilgi**: yalnizca persona dagiliminda `region` boyutu
  ve o bolgeye ait acikca isaretlenmis `scenario_interest` (varsayim +
  guven seviyesi) varsa hesaplanir; hicbir zaman gercek pazar talebi
  olarak sunulmaz (bkz. `app.services.personas.REGIONAL_INTEREST_DISCLAIMER`).
  Boyut/veri yoksa bos liste dondurulur; iddia uretilmez.

## Belirsizlik uretimi

Olasilik metrikleri (tamamlama/yanlis tiklama/terk) icin belirsizlik,
nokta tahmininin etrafinda **sabit oranli bir ucgen (triangular) aralik**
olarak uretilir (`probability_uncertainty_half_width_ratio`, varsayilan
%22). Bu, bir Monte Carlo simulasyonu veya istatistiksel bir guven
araligi **degildir**; kural tabanli bir "bu tahmin ne kadar hassas
olabilir" gostergesidir. Gorev suresi icin de benzer sekilde sabit
oranli bir yayilim (`duration_relative_spread`) kullanilir ve ucgen
dagilimin tam ters-CDF formulu ile p10/p50/p90 hesaplanir.

`deterministic_seed` alani (persona ornekleme motoruyla ayni tasarim
tutarliligi icin) her calistirmada saklanir, ancak **bu motor tarafindan
rastgelelik uretmek icin kullanilmaz** — ayni girdi + farkli seed her
zaman ayni metrikleri uretir (bkz.
`backend/tests/test_simulation_engine.py::test_different_seed_does_not_change_point_estimates`).
Bu kasitli bir tasarim karari: "rastgele sayı üretmek tek başına model
değildir" ilkesini ihlal etmemek icin, bu asamada rastgelelik hicbir
nokta tahminine sizmaz. `deterministic_seed` alani, ileride kalibre
edilmis/stokastik bir motor surumune gecildiginde (bkz. asagida) gercek
bir rastgelelik kaynagi olarak kullanilabilecek sekilde altyapida
tutulur.

## A/B karsilastirmasi

`app.engine.baseline.compare_baseline_results`, iki varyantin sonuclarini
karsilastirir ve her metrik icin **mutlak deger (A ve B), fark (delta) ve
orneklenen sentetik persona sayisini** dondurur. **Istatistiksel
anlamlilik iddia edilmez**; sonuc her zaman "simulasyon farki" olarak
etiketlenir (`COMPARISON_NOTE`) ve `calibration_status=uncalibrated`
alanini tasir.

## Durum makinesi ve calistirma guvenilirligi

Bir `SimulationRun`, `queued -> running -> (succeeded | failed |
cancelled)` durumlarini izler (bkz. `app.services.simulation_worker`).
Worker (`arq` cron, her ~3 saniyede bir), bekleyen isleri `SELECT ... FOR
UPDATE SKIP LOCKED` ile kilitleyerek alir; boylece iki worker sureci ayni
isi iki kez islemez. Ilerleme, gecici olarak Redis'e (`app.services.
simulation_progress`, TTL'li), kalici olarak PostgreSQL'e yazilir —
kalici sonucun tek kaynagi her zaman PostgreSQL'dir.

**Rezervasyon (Prompt 3) entegrasyonu**: bir is baslatildiginda (bkz.
`app.services.test_wizard.launch_draft`) ilgili ucretsiz hak ya da Chip
miktari **rezerve edilir**. Is basariyla tamamlandiginda rezervasyon
**tuketilir** (`consume_*`); kalici basarisizlik veya iptalde **serbest
birakilir** (`release_*`). Bu islemler (bkz. `app.services.entitlements`
ve `app.services.chip_ledger`) idempotenttir: ayni rezervasyonu paylasan
birden fazla calistirma (ornegin bir A/B testinin iki varyanti,
`launch_run_id` ile eslesir) icin cift tuketim veya cift iade
uretilmez — bir varyant basarili olup rezervasyonu tukettiginde, diger
varyantin basarisizligi artik "zaten tuketilmis" durumunu sessizce kabul
eder ve tekrar bir islem yapmaz.

**Yeniden deneme (retry)**: yalnizca `failed` durumundaki bir is manuel
olarak yeniden denenebilir (`POST /api/simulations/runs/{id}/retry`);
bu, rezervasyonu **yeniden** yapar (orijinal rezervasyon basarisizlikta
zaten serbest birakilmis olur) ve isi tekrar `queued`'a alir. Worker
cokup "running" durumunda takili kalan isler icin ayri bir "reap" cron'u
(`app.services.simulation_worker.reap_stale_running_runs`) belirli bir
zaman asimindan sonra isi ya yeniden kuyruga alir ya da (deneme sinirina
ulasildiysa) basarisiz sayar ve rezervasyonu serbest birakir.

## Sinirlamalar

- Sayfa ozellikleri gercek bir crawl'dan gelmez (bkz. yukarida); bu
  motorun ciktilari, gercek bir sayfanin gercek ozelliklerini yansitmaz.
- Kural agirliklari (`rules_config.py`) uzman gorusu/varsayimla
  belirlenmistir; herhangi bir gercek kullanici verisiyle
  dogrulanmamistir.
- Belirsizlik araliklari, istatistiksel bir guven araligi degil, kural
  tabanli bir hassasiyet gostergesidir.
- Bolgesel ilgi, yalnizca kullanicinin kendi girdigi (dogrulanmamis)
  bir varsayimdir.

## Gelismis moduller: ag/cihaz, kampanya CTA, sentetik dikkat

Sihirbazin 4. adiminda secilebilen, Chip gerektiren uc gelismis modul (bkz.
`app.services.module_catalog`) `app.services.simulation_worker.process_run`
icinde, baseline sonucu hesaplandiktan sonra ama rezervasyon tuketilmeden
once islenir (`_process_selected_modules`). Sonuclari
`SimulationRun.result["modules"][<modul_anahtari>]` altinda saklanir; hicbiri
icin ayri bir tablo/migration eklenmemistir.

**Olcum turu ayrimi onemlidir** (bkz. docs/scientific-integrity.md): iki
modul SENTETIK TAHMIN, biri GERCEK TEKNIK OLCUMDUR - bu ikisi hicbir zaman
birbirinin yerine sunulmaz.

- **`network_device_test` (TECHNICAL_MEASUREMENT, gercek)**: sentetik
  degildir. `app.services.device_network_analysis`, analyzer container'inda
  gercekten calisan Playwright'i (`analyzer/app/browser.py:
  analyze_device_network`) 4 sabit cihaz/ag profilinde (`desktop_broadband`,
  `mobile_4g`, `mobile_slow_3g`, `tablet_wifi`) cagirir; her profil icin CDP
  `Network.emulateNetworkConditions` ile gercek ag kosullari simule edilir ve
  gercek sayfa yukleme zamanlamalari + axe-core erisilebilirlik ihlal sayisi
  olculur. Tek bir profilin basarisiz olmasi (timeout vb.) tum modulu
  dusurmez; bu, `error_rate` (basarisiz/toplam) metriginin kaynagidir. Bu
  GERCEK bir olcumdur ama gercek bir kullanicinin oznel deneyimini/
  memnuniyetini TEMSIL ETMEZ - yalnizca teknik performans olculur (bkz.
  `NETWORK_DEVICE_DISCLAIMER`, analyzer/app/schemas.py).
- **`campaign_cta_test` (SYNTHETIC_ESTIMATE)**: `app.engine.
  advanced_modules.run_campaign_cta_analysis`, `network_device_test`'in
  aksine analyzer'a hic gitmez; `app.engine.fixtures.
  get_page_feature_snapshot` (ayni deterministik sentetik sayfa fixture'i,
  baseline motoruyla paylasilir) uzerinden `primary_cta_count` kadar
  sentetik CTA adayi turetir. Her CTA icin tiklama olasiligi = taban
  olasilik − (sira cezasi × sira) + (ust ekran bonusu/cezasi) − (dusuk
  kontrast cezasi varsa); ucgen belirsizlik araligiyla raporlanir (bkz.
  `rules_config.cta_*` agirliklari). Esik tabanli "mesaj netligi bulgulari"
  (cok fazla CTA, dusuk kontrast, CTA ilk ekranda degil) eklenir.
- **`synthetic_attention_estimate` (SYNTHETIC_ESTIMATE)**: `app.engine.
  advanced_modules.run_synthetic_attention_estimate`, ayni sentetik sayfa
  fixture'indan 5 sabit sayfa bolgesi (ust navigasyon, hero/baslik, birincil
  CTA, govde metni, alt bilgi) icin bir agirlik dagilimi hesaplar (nav_depth,
  heading_count, above_fold_cta, page_word_count'a dayali kurallar +
  deterministik seed'li kucuk bir varyasyon), 1.0'a normalize eder. Bu KESIN
  OLARAK gercek goz izleme (eye-tracking) verisi veya gercek kullanici
  dikkat davranisi DEGILDIR; yalnizca sayfa yapisindan/gorsel belirginlikten
  turetilmis bir heuristiktir (bkz. `SYNTHETIC_ATTENTION_DISCLAIMER`).
  Ciktisi ayrica `SimulationRun.result["attention_grid"]` alanina kopyalanir
  (rapor sayfasindaki mevcut "Sentetik dikkat tahmini" ısı haritasi
  bolumunun veri kaynagi budur).

**Chip/hata iliskisi**: uc modulden herhangi biri kurtarilamaz bir hatayla
basarisiz olursa (`ModuleInputError`/`ModuleProcessingError`) TUM run
`failed` isaretlenir ve rezervasyon (mevcut, run-bazli, idempotent
consume/release mekanizmasiyla - bkz. yukarida "Rezervasyon entegrasyonu")
serbest birakilir; boylece kismi/yarim bir modul sonucu asla "succeeded"
olarak Chip tuketmez. Kullanici mevcut `retry` uc noktasiyla yeniden dener.

## Kalibrasyon plani (gelecek)

`calibration_status`, `uncalibrated -> calibrating -> calibrated`
durumlarini destekleyecek sekilde modellenmistir (bkz.
`app.models.simulations.CalibrationStatus`), ancak bu asamada hicbir
otomatik gecis yoktur ve **hicbir surum kendini "calibrated" olarak
isaretleyemez**. Gelecekte planlanan yaklasim:

1. Gercek kullanilabilirlik testi sonuclarini (gorev tamamlama, sure,
   hata orani) gonullu musteri projelerinden, acik riza ile toplamak.
2. Bu gercek sonuclari, ayni girdilerle uretilmis sentetik tahminlerle
   (ayni `input_snapshot_hash`) eslestirip sapmayi olcmek.
3. Yeterli buyuklukte ve cesitlilikte bir kalibrasyon veri kumesi
   toplandiginda (metodoloji ekibince onaylanmis bir esik), ilgili
   `rules_version` icin `calibrating` durumuna gecmek ve agirliklari bu
   veriye gore yeniden ayarlamak.
4. Sapma kabul edilebilir sinirlar icinde kaldiginda ve bagimsiz bir
   gozden gecirme tamamlandiginda, o surumu "calibrated" olarak
   isaretlemek — bu asamaya kadar tum sonuclar "uncalibrated" kalir ve
   pazarlama/urun iletisiminde "dogrulanmis"/"bilimsel olarak
   kanitlanmis" gibi ifadeler kullanilamaz (bkz.
   docs/scientific-integrity.md).
