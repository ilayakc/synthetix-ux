# Production Compose (Paket 5B)

Bu belge, `compose.prod.yaml` ile calisan production-benzeri stack'i anlatir.
**Bu bir gercek deployment kilavuzu DEGILDIR** - internete acma, domain/TLS
baglama veya cloud/VPS olusturma adimlari icermez; bunlar bu paketin
kapsami disindadir. Amac, development'tan (`compose.yaml`) TAMAMEN AYRI,
tekrarlanabilir bir production topolojisini yerel/izole bir Docker
ortaminda calistirilabilir kilmaktir.

## Mimari ozeti

- **frontend** (nginx): derlenmis statik dosyalari sunar VE `/api`
  isteklerini ayni origin uzerinden ic agdaki `backend`'e reverse-proxy
  eder. **Disariya acilan TEK host portu budur** (bkz. `PROD_FRONTEND_PORT`).
- **backend / worker**: `backend/Dockerfile`'dan (development ile AYNI
  Dockerfile, bind-mount YOK, `--reload` YOK) uretilen, birebir ayni
  image/commit'ten calisan iki servis. Kod image icine `COPY` ile
  gomuludur - container restart'i asla eski/farkli kodla calismaz.
- **analyzer**: development ile ayni Dockerfile; host'a port yayinlamaz.
- **db / redis**: kalici named volume'lerle (`pgdata_prod`, `redis_data_prod`)
  calisir; host'a port yayinlamaz.
- **migrate**: tek seferlik (`restart: "no"`) servis, yalnizca
  `alembic upgrade head` calistirir. `backend` ve `worker`, bu servisin
  **basariyla tamamlanmasini** (`service_completed_successfully`) bekler;
  migration'i baska hicbir servis calistirmaz.

Proje adi (`name: synthetix-ux-prod`) development stack'inden (`synthetix-ux`)
kasitli olarak farklidir - ayni volume/network/container isim uzayini
paylasmazlar.

## 1. Production secret'larini hazirlama

```powershell
copy .env.production.example .env.production
```

`.env.production` dosyasini gercek, guclu degerlerle doldurun:

- `JWT_SECRET_KEY`, `ANALYZER_SHARED_TOKEN`: `python -c "import secrets; print(secrets.token_urlsafe(64))"`
  ile ayri ayri uretin - birbirinden farkli olmalidir.
- `POSTGRES_PASSWORD` / `DATABASE_URL`: guclu, benzersiz bir DB parolasi.
- `ALLOWED_HOSTS`: gercek, bilinen host adlarinizin virgulle ayrilmis
  listesi (bos veya `*` KABUL EDILMEZ - fail-closed reddedilir).
- `CORS_ALLOWED_ORIGIN`: gercek production origin'iniz.

`.env.production` **Git tarafindan izlenmez** (bkz. `.gitignore`: `.env.*`
kurali `.env.production.example` disindaki tum `.env.*` dosyalarini
kapsar). Bu dosyayi ASLA commit etmeyin veya paylasmayin.

## 2. Production config dogrulama

Stack'i ayaga kaldirmadan once render edilen config'i inceleyin (secret
degerlerinin BEKLENDIGI gibi doldugunu, yanlislikla baska bir dosyadan
okunmadigini kontrol etmek icin):

```powershell
docker compose -f compose.prod.yaml --env-file .env.production config
```

Bilinen guvensiz/eksik bir degerle (ornegin `ALLOWED_HOSTS` bos) stack
baslatilirsa, `backend`/`worker` `app.config_security.validate_production_secrets`
tarafindan **fail-closed** reddedilir (servis vermeye baslamadan once
cikis yapar) - bkz. Paket 5A.

## 3. Migration sirasi

```powershell
docker compose -f compose.prod.yaml --env-file .env.production up -d --build
```

Calisma sirasi (compose `depends_on` ile garanti edilir):

1. `db`, `redis` saglikli hale gelir.
2. `migrate` calisir, `alembic upgrade head` uygular, basariyla **biter**.
3. `analyzer` saglikli hale gelir.
4. `backend` ve `worker`, (2)'nin basarili tamamlanmasini VE (1)/(3)'un
   saglikli olmasini bekleyip baslar.
5. `frontend`, `backend` saglikli oldugunda baslar.

> **Backup uyarisi**: gercek (var olan veri iceren) bir production
> veritabaninda migration calistirmadan once HER ZAMAN yedek (backup)
> alin. Bu paket kapsaminda bir backup/restore script'i YOKTUR (bkz.
> "Kapsam disi" asagida) - bu, sonraki bir paketin konusudur.

## 4. Health / readiness kontrolu

```powershell
curl http://localhost:8080/api/health
curl http://localhost:8080/api/ready
```

(Port, `.env.production` icindeki `PROD_FRONTEND_PORT` ile degistirilebilir;
varsayilan `8080`.) Her iki istek de frontend'in `/api` reverse-proxy'si
uzerinden backend'e ulasir - ayri bir backend portu ACILMAZ.

## 5. Log kontrolu

```powershell
docker compose -f compose.prod.yaml --env-file .env.production logs -f backend worker frontend
```

## 6. Stack'i durdurma

```powershell
docker compose -f compose.prod.yaml --env-file .env.production down
```

Named volume'leri (`pgdata_prod`, `redis_data_prod`) de silmek isterseniz
(DIKKAT: kalici veriyi siler) `-v` bayragini ekleyin - bu, normal
"durdurma" akisinin bir parcasi DEGILDIR ve bilincli olarak ayri
tutulmustur.

## Bilinen sinirlamalar (bu paket kapsami disinda)

- **Rollback**: bu pakette henuz tamamlanmamistir. `migrate` servisi yalnizca
  `alembic upgrade head` calistirir; bir migration'i geri almak icin (gerekirse)
  su an manuel `alembic downgrade` mudahalesi gerekir.
- Gercek deployment (internet, domain, TLS/HSTS, CI workflow, backup script,
  rate limiting, JSON logging) bu paketin kapsami disindadir.
- HSTS header'i eklenmemistir: yalnizca gercek bir HTTPS deployment'inda
  anlamlidir; yerel HTTP smoke testini bozmamak icin bu asamada
  DOKUMANTE EDILMIS ama eklenmemistir - gercek TLS deployment asamasinda
  eklenmelidir (`Strict-Transport-Security: max-age=63072000; includeSubDomains`
  gibi bir deger, yalnizca HTTPS terminasyonu dogrulandiktan SONRA).

## Guvenlik sertlestirmesi ozeti (bkz. backend/app/main.py, app/config_security.py)

- `/docs`, `/redoc`, `/openapi.json`: production'da tamamen kapali (404),
  development'ta degismeden acik.
- `TrustedHostMiddleware`: yalnizca production'da eklenir; `ALLOWED_HOSTS`
  bos/`*` ise uygulama fail-closed baslamayi reddeder.
- Guvenlik header'lari (yalnizca production, tek katmanda - backend API
  cevaplarinda FastAPI middleware, statik frontend cevaplarinda nginx,
  ASLA ikisi birden ayni yol icin):
  `Content-Security-Policy`, `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY` + `frame-ancestors 'none'`, `Referrer-Policy`,
  `Permissions-Policy`.
- CSP'de `style-src 'self' 'unsafe-inline'` - React'in birkac sayfada
  (`ChipTopUp`, `Dashboard`, `PersonaPresets`, `ReportDetail`, `Simulations`,
  `DesignSourcePicker`, `Step5Summary`) kullandigi `style={{...}}` inline
  stil attribute'lari icin BILINCLI VE SINIRLI bir istisna - `script-src`'ye
  uygulanmaz, `unsafe-eval` hicbir yerde kullanilmaz.
