"""Simulasyon calistirma durum makinesi: queued -> running -> succeeded/failed/cancelled.

Bu modul heuristic motoru (`app.engine.baseline`) is akisina baglar:

- `claim_next_queued_runs`: bekleyen isleri `SELECT ... FOR UPDATE SKIP
  LOCKED` ile kilitleyerek `running`'e gecirir (birden fazla worker sureci
  ayni isi iki kez almaz).
- `process_run`: motoru calistirir, ilerlemeyi asamalar halinde Redis'e
  (gecici) ve DB'ye (kalici) yazar, basarida entitlement/Chip
  rezervasyonunu tuketir (`consume_*`), basarisizlik/iptalde serbest
  birakir (`release_*`) - bkz. app.services.entitlements / chip_ledger.
- `cancel_run` / `retry_run`: API tarafindan cagrilan kullanici eylemleri.
- `reap_stale_running_runs`: worker cokerse "running"de takili kalan
  isleri yeniden kuyruga alir veya deneme sinirina ulasmissa basarisiz
  sayar.

Atomik grup muhasebesi (Paket 4.0): tek bir mantiksal baslatma (launch) -
tekil bir test ya da bir A/B karsilastirmasinin iki varyanti - `SimulationRun.
launch_run_id` ile gruplanir (bkz. `app.services.test_wizard.launch_draft`).
Hicbir calistirma, KENDI basarisi/basarisizligina bakarak grubun paylasilan
rezervasyonunu/ucretsiz hakkini TEK BASINA tuketmez/serbest birakmaz - bkz.
`_resolve_launch_group`: bir calistirma terminal duruma (SUCCEEDED/FAILED/
CANCELLED) gectiginde grubun TUM uyeleri kilitlenip okunur; ancak HEPSI
terminal ise muhasebe islenir (hepsi basarili -> tek tuketim, en az biri
basarisiz/iptal -> tam serbest birakma). Boylece "A basarili -> tuketildi,
B daha sonra basarisiz -> B'nin release'i etkisiz kaliyor, Chip haksiz
tuketilmis oluyor" senaryosu olusmaz.

Idempotency: `consume_entitlement`/`consume_reservation` ve
`release_entitlement`/`release_reservation` zaten idempotenttir (tekrar
cagrildiginda hata firlatmadan mevcut durumu dondurur/veya durum uyumsuzsa
acik bir hata firlatir); `_resolve_launch_group` bu hatalari (ornegin daha
onceki bir retry jenerasyonuna ait, zaten kendi dongusunde sonuclanmis bir
rezervasyon icin) denetim amacli loglayarak yutar - boylece ayni grubu
esanli sonuclandirmaya calisan birden fazla worker/reaper cagrisi icin cift
tuketim/cift serbest birakma olusmaz.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session_maker
from app.engine.advanced_modules import (
    ModuleInputError,
    run_campaign_cta_analysis,
    run_synthetic_attention_estimate,
)
from app.engine.baseline import SimulationInputError, run_baseline_simulation
from app.engine.rules_config import CURRENT_RULES_VERSION
from app.models.page_analysis import PageAnalysis, PageAnalysisSourceKind, PageAnalysisStatus
from app.models.reports import Report
from app.models.simulations import SimulationRun, SimulationStatus
from app.services import chip_ledger, device_network_analysis, simulation_progress
from app.services import entitlements as entitlements_service
from app.services.exceptions import (
    ChipReservationNotFoundError,
    EntitlementNotFoundError,
    InvalidChipReservationStateError,
    InvalidEntitlementStateError,
    InvalidSimulationStateError,
    ModuleProcessingError,
    PageAnalysisDependencyError,
    SimulationRunNotFoundError,
)
from app.services.page_analysis_adapter import (
    DomAdaptedInput,
    PageAnalysisFeatureError,
    adapt_page_analysis,
    adapt_visual_page_analysis,
)

logger = logging.getLogger("synthetix.simulation_worker")

CLAIM_BATCH_SIZE = 5
STALE_RUNNING_TIMEOUT_SECONDS = 300
MAX_ATTEMPTS = 3

TERMINAL_STATUSES = (SimulationStatus.SUCCEEDED, SimulationStatus.FAILED, SimulationStatus.CANCELLED)

# --- Basarisizlik siniflandirmasi: hangi FAILED run'lar yeniden denenebilir? ---
#
# `network_device_test` seciliyken kaynak turu URL degilse (screenshot/
# ai_generated), run HER ZAMAN ayni `input_snapshot` (immutable) ile tekrar
# basarisiz olur - bu, run.error metnindeki HAM (ic) mesaj uzerinden
# frontend'in substring kontrolu yapmasindansa, burada TEK bir merkezden
# (`classify_run_failure`) hesaplanan bir `failure_code`/`retryable` alani
# olarak API response'una tasinir (bkz. app.routers.simulations). Gecici
# network/timeout/5xx gibi GERCEKTEN yeniden denenebilir hatalarda
# (`ModuleProcessingError` -> "analyzer'a ulasilamadi"/"analyzer hatasi")
# `retryable=True` kalir - yalnizca BU spesifik, yapisal olarak duzelemeyen
# durum icin `retryable=False` dondurulur.
NETWORK_DEVICE_TEST_REQUIRES_URL_CODE = "network_device_test_requires_url"

NETWORK_DEVICE_TEST_NON_RETRYABLE_MESSAGE = (
    "Bu çalışma ekran görüntüsüyle Ağ ve cihaz testi seçildiği için yeniden denenemez. "
    "Yeni bir test oluşturup bu modülü kaldırın veya bütün kaynakları URL olarak seçin."
)


def classify_run_failure(run: SimulationRun) -> tuple[bool, str | None]:
    """FAILED bir run icin `(retryable, failure_code)` dondurur.

    FAILED olmayan run'lar icin de guvenli bir varsayilan (`True, None`)
    dondurulur (cagiran taraf zaten yalnizca FAILED durumunda bu alanlari
    anlamli sekilde gosterir, bkz. `app.routers.simulations._to_response`).
    """

    if run.status != SimulationStatus.FAILED:
        return True, None

    modules: list[str] = run.input_snapshot.get("modules") or []
    url = run.input_snapshot.get("url")
    source_type = run.input_snapshot.get("source_type")
    # `url` dolu ise (gercek bir URL kaynagi basariyla launch edilmis, hata
    # analyzer/network tarafinda GECICI bir sebeple olusmus demektir) HER
    # ZAMAN retryable kalir - `source_type` alani (eski/legacy run'larda hic
    # bulunmayabilir) burada TEK BASINA belirleyici degildir. Yalnizca `url`
    # BOS/None VE kaynak turu acikca screenshot/AI ise (statik gorsel -
    # yapisal olarak duzelemez) non-retryable isaretlenir.
    if "network_device_test" in modules and not url and source_type in ("screenshot", "ai_generated"):
        return False, NETWORK_DEVICE_TEST_REQUIRES_URL_CODE

    return True, None


def _now() -> datetime:
    return datetime.now(UTC)


async def _set_progress(
    session: AsyncSession, run: SimulationRun, *, status: SimulationStatus, percent: int, message: str
) -> None:
    run.progress_percent = percent
    run.progress_message = message
    await session.flush()
    await simulation_progress.publish_progress(run.id, status=status.value, percent=percent, message=message)


async def claim_next_queued_runs(session: AsyncSession, limit: int = CLAIM_BATCH_SIZE) -> list[SimulationRun]:
    """Bekleyen (queued) isleri kilitleyip 'running' durumuna gecirir."""

    # Paket 4B: yeni URL run'lari (page_analysis_id dolu) bagli PageAnalysis
    # SUCCEEDED olana kadar claim EDILMEZ (fixture'a sessizce dusulmesin diye
    # - bkz. app.services.page_analysis_adapter). Eski (page_analysis_id NULL)
    # run'lar eskisi gibi kosulsuz claim edilir. PageAnalysis FAILED oldugunda
    # bu run'lar burada sonsuza kadar QUEUED KALMAZ - ayri bir cron
    # (`fail_runs_blocked_by_failed_page_analysis`) onlari FAILED'e cevirir.
    succeeded_page_analysis_ids = select(PageAnalysis.id).where(
        PageAnalysis.status == PageAnalysisStatus.SUCCEEDED
    )
    result = await session.execute(
        select(SimulationRun)
        .where(
            SimulationRun.status == SimulationStatus.QUEUED,
            or_(
                SimulationRun.page_analysis_id.is_(None),
                SimulationRun.page_analysis_id.in_(succeeded_page_analysis_ids),
            ),
        )
        .order_by(SimulationRun.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    runs = list(result.scalars().all())
    for run in runs:
        run.status = SimulationStatus.RUNNING
        run.started_at = _now()
        run.attempt_count += 1
        run.progress_percent = 0
        run.progress_message = "Kuyruktan alindi, isleniyor"
    await session.flush()
    return runs


_PAGE_ANALYSIS_FAILED_MESSAGE = "Bagli sayfa analizi basarisiz oldugu icin bu calistirma islenemedi"


async def fail_runs_blocked_by_failed_page_analysis(
    session: AsyncSession, limit: int = CLAIM_BATCH_SIZE
) -> int:
    """Bagli `PageAnalysis`'i FAILED olan QUEUED run'lari guvenli bicimde FAILED yapar.

    `claim_next_queued_runs` bu run'lari hicbir zaman claim ETMEZ (bagli
    PageAnalysis SUCCEEDED degil); bu fonksiyon olmadan boyle bir run
    sonsuza kadar QUEUED kalirdi. Yalnizca QUEUED durumundaki satirlari
    secip kilitledigi icin tekrar tekrar calistirilmasi idempotenttir (zaten
    FAILED olan satirlar bir daha secilmez). `_finalize_failed` uzerinden
    `_resolve_launch_group` zaten cagrilir - A/B grubunun diger tarafi
    basarili olsa bile grup terminal olunca tam rezervasyon/entitlement
    release edilir.
    """

    failed_page_analysis_ids = select(PageAnalysis.id).where(PageAnalysis.status == PageAnalysisStatus.FAILED)
    result = await session.execute(
        select(SimulationRun)
        .where(
            SimulationRun.status == SimulationStatus.QUEUED,
            SimulationRun.page_analysis_id.in_(failed_page_analysis_ids),
        )
        .order_by(SimulationRun.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    runs = list(result.scalars().all())
    for run in runs:
        await _finalize_failed(session, run, error=_PAGE_ANALYSIS_FAILED_MESSAGE)
    return len(runs)


async def _is_cancel_requested(session: AsyncSession, run_id: uuid.UUID) -> bool:
    result = await session.execute(select(SimulationRun.cancel_requested).where(SimulationRun.id == run_id))
    return bool(result.scalar_one_or_none())


async def _resolve_chip_reservation(
    session: AsyncSession, organization_id: uuid.UUID, reservation_id: uuid.UUID, *, consume: bool
) -> None:
    reason = "A/B grup karşılaştırması"
    try:
        if consume:
            await chip_ledger.consume_reservation(session, organization_id, reservation_id, reason)
        else:
            await chip_ledger.release_reservation(session, organization_id, reservation_id, reason)
    except (InvalidChipReservationStateError, ChipReservationNotFoundError) as exc:
        # Bu rezervasyon (ornegin daha onceki bir retry jenerasyonuna ait ve
        # zaten kendi dongusunde sonuclanmis) beklenen terminal durumda
        # degil. Kullanici odemesi/iadesi baska bir rezervasyon uzerinden
        # (grubun GUNCEL rezervasyonu) zaten dogru sekilde islenecegi icin bu
        # durum guvenli sekilde atlanir; yalnizca denetim amacli loglanir -
        # kullaniciya hicbir ic sistem detayi sizdirilmaz.
        logger.warning(
            "chip rezervasyonu %s beklenen durumda degil (islem=%s): %s",
            reservation_id,
            "consume" if consume else "release",
            exc,
        )


async def _resolve_entitlement(
    session: AsyncSession,
    organization_id: uuid.UUID,
    feature_key: str,
    run_id: uuid.UUID,
    *,
    consume: bool,
) -> None:
    try:
        if consume:
            await entitlements_service.consume_entitlement(session, organization_id, feature_key, run_id)
        else:
            await entitlements_service.release_entitlement(session, organization_id, feature_key, run_id)
    except (InvalidEntitlementStateError, EntitlementNotFoundError) as exc:
        logger.warning(
            "entitlement '%s' beklenen durumda degil (islem=%s): %s",
            feature_key,
            "consume" if consume else "release",
            exc,
        )


async def _resolve_launch_group(session: AsyncSession, run: SimulationRun) -> None:
    """Bir run terminal duruma gectiginde, ait oldugu mantiksal baslatma
    (launch) grubunun Chip/entitlement muhasebesini -gerekiyorsa- tek
    seferlik ve atomik olarak sonuclandirir.

    Grup, `SimulationRun.launch_run_id` ile belirlenir (bkz.
    `app.services.test_wizard.launch_draft`): ayni baslatmadan dogan TUM
    calistirmalar (A/B karsilastirmasinda iki varyant, tekil testte bir
    tane - beklenen sayi asla sabit varsayilmaz, gruptaki gercek satir
    sayisindan turetilir) ayni `launch_run_id` degerini tasir ve hepsi TEK
    bir DB transaction'i icinde, atomik olarak olusturulur (bkz.
    `launch_draft`) - bu nedenle "eksik olusturulmus bir grup" (bazi
    kardesler commit edilmis bazilari edilmemis) durumu olusamaz: bir
    SimulationRun DB'de gorulebiliyorsa, ayni launch_run_id'ye sahip TUM
    kardesleri de zaten commit edilmistir.

    Kural: gruptaki TUM calistirmalar terminal (SUCCEEDED/FAILED/CANCELLED)
    duruma gelmeden hicbir muhasebe islemi yapilmaz. Hepsi SUCCEEDED ise
    rezervasyon(lar)/hak tek seferlik tuketilir; en az biri basarisiz/iptal
    ise tamamen serbest birakilir - kismi basari hicbir zaman Chip tuketmez.

    Es zamanlilik: gruptaki BIRDEN FAZLA `SimulationRun` satirini id'ye gore
    sirali `SELECT ... FOR UPDATE` ile kilitlemek YERINE (bu, bir onceki
    tasarimda GERCEK bir deadlock'a yol acmisti - bkz. asagidaki not) tek bir
    `pg_advisory_xact_lock` (grubun `launch_run_id`'sinden turetilen tek bir
    anahtar uzerinde, transaction sonuna kadar tutulan) kullanilir:

    Neden coklu-satir `FOR UPDATE` YETERSIZ/TEHLIKELI: her calistirmayi isleyen
    transaction, kendi satirinin kilidini `_resolve_launch_group`dan COK ONCE
    (calistirma "claim" edilirken, bkz. `claim_next_queued_runs`/
    `_process_claimed_run`) alir ve islem suresince tutar. A ve B'yi isleyen
    iki transaction, ikisi de kendi satirini onceden tutarken, sonradan
    "grubun TUMUNU id sirasina gore kilitle" sorgusunu calistirirsa: id sirasi
    yalnizca AYNI sorgu icinde YENI alinan kilitlerin sirasini garanti eder -
    ama burada her transaction zaten FARKLI (kendi) satirini ONCEDEN tutuyor;
    T(A) -> B'yi bekler, T(B) -> A'yi bekler -> klasik dongusel bekleme
    (deadlock). Bu, Paket 4.0 dogrulamasi sirasinda gercek test veritabaninda
    yeniden uretilip gozlemlenmistir (bkz. sonuc raporu).

    Advisory lock, satir kimligi/sirasindan bagimsiz TEK bir kaynak oldugu
    icin bu sorunu yapisal olarak ortadan kaldirir: iki transaction da AYNI
    tek anahtari ister; hangisi once alirsa o once ilerler, digeri yalnizca
    O TEK kilidi bekler (dongusel bekleme olusamaz). Kilit `pg_advisory_
    xact_lock` oldugu icin transaction commit/rollback olunca otomatik
    serbest kalir - ayrica serbest birakma kodu gerekmez. Kilidi alan
    transaction, kardeslerin durumunu DUZ bir `SELECT` ile okur; kilit zaten
    "ayni grubu es zamanli cozumleme" girisimlerini serilestirdigi icin
    (bir onceki cozumleyici COMMIT olmadan bu SELECT calisamaz), okunan
    durumlar guncel/dogrudur - ayrica satir bazli `FOR UPDATE` gerekmez.
    """

    group_key = run.launch_run_id
    if group_key is not None:
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(str(group_key), 0))))
        result = await session.execute(
            select(SimulationRun).where(SimulationRun.launch_run_id == group_key).order_by(SimulationRun.id)
        )
        group_runs = list(result.scalars().all())
    else:
        # `launch_run_id` her zaman `launch_draft` tarafindan atanir; None
        # burada yalnizca teorik bir savunma durumudur - tek elemanli bir
        # grup gibi (bugunku per-run davranisla ayni) ele alinir.
        group_runs = [run]

    if not group_runs or not all(r.status in TERMINAL_STATUSES for r in group_runs):
        return

    all_succeeded = all(r.status == SimulationStatus.SUCCEEDED for r in group_runs)

    # NOT: bagimsiz muhasebe kalemleri (entitlement VE chip rezervasyonu) -
    # bkz. app.services.test_wizard.launch_draft: bir run hem
    # `free_entitlement_feature_key` (temel test ucretsiz hakki) HEM DE
    # `chip_reservation_id` (secili gelismis modullerin Chip'i) tasiyabilir;
    # ikisi de varsa ikisi de ayni kuralla (hepsi basarili -> tuket, degilse
    # -> serbest birak) sonuclandirilir. Chip rezervasyon id'leri icin bir
    # KUME kullanilir cunku bir retry, yalnizca retry edilen calistirmanin
    # rezervasyonunu yeniler (bkz. `retry_run`) - eski (zaten sonuclanmis)
    # rezervasyon `_resolve_chip_reservation` tarafindan guvenli sekilde
    # atlanir, yeni/guncel rezervasyon dogru sekilde sonuclandirilir.
    chip_reservation_ids = {r.chip_reservation_id for r in group_runs if r.chip_reservation_id is not None}

    for reservation_id in chip_reservation_ids:
        await _resolve_chip_reservation(session, run.organization_id, reservation_id, consume=all_succeeded)

    # AI raporu (`ai_report`) rezervasyonu (Faz 3C.2B1): baseline'dan AYRI,
    # launch grubu basina TEK (A/B'de iki satir AYNI id'yi paylasir) bir
    # rezervasyondur. Bu FAZDA yalnizca BASELINE-grup-sonucuna bagli davranir:
    #   - Grup TAMAMEN basariliysa AI rezervasyonu RESERVED birakilir (bu fazda
    #     consume EDILMEZ; AI pipeline'i bu fazda hic olusturulmaz - AI'nin
    #     kendi terminal sonucuna bagli consume/release Faz 3C.2B2'ye aittir).
    #   - En az bir baseline run FAILED/CANCELLED ise AI rezervasyonu da tam
    #     RELEASED edilir - paylasilan id yalnizca BIR KEZ islenir (KUME) ve
    #     `_resolve_chip_reservation` idempotenttir (tekrar/yaris guvenli).
    if not all_succeeded:
        ai_chip_reservation_ids = {
            r.ai_chip_reservation_id for r in group_runs if r.ai_chip_reservation_id is not None
        }
        for reservation_id in ai_chip_reservation_ids:
            await _resolve_chip_reservation(session, run.organization_id, reservation_id, consume=False)

        # AI etkilesim isi haritasi (`ai_interaction_heatmap`) rezervasyonu:
        # `ai_report` ile AYNI, BAGIMSIZ desen. Baseline grubu tamamen basariliysa
        # RESERVED birakilir (consume/release, heatmap job'larinin kendi terminal
        # sonucuna gore ayri settlement cycle'inda olur - bkz. app.services.
        # ai_interaction_heatmap.worker); en az bir baseline run FAILED/CANCELLED
        # ise burada tam RELEASED edilir (heatmap job'lari hic olusmayacak).
        heatmap_chip_reservation_ids = {
            r.heatmap_chip_reservation_id for r in group_runs if r.heatmap_chip_reservation_id is not None
        }
        for reservation_id in heatmap_chip_reservation_ids:
            await _resolve_chip_reservation(session, run.organization_id, reservation_id, consume=False)

    entitlement_feature_keys = {
        r.free_entitlement_feature_key for r in group_runs if r.free_entitlement_feature_key is not None
    }
    if entitlement_feature_keys:
        # `run_id` entitlement rezervasyonu icin `launch_run_id`dir (bkz.
        # launch_draft) ve grup ici tum satirlarda ayni degeri tasir; bu
        # nedenle entitlement_feature_keys doluysa group_key hicbir zaman
        # None degildir (entitlement rezervasyonu her zaman launch_run_id
        # ile yapilir - bkz. launch_draft/retry_run).
        assert group_key is not None
        for feature_key in entitlement_feature_keys:
            await _resolve_entitlement(
                session, run.organization_id, feature_key, group_key, consume=all_succeeded
            )


async def _load_page_feature_input(session: AsyncSession, run: SimulationRun) -> DomAdaptedInput | None:
    """Run'in bagli oldugu PageAnalysis'i gercek motor girdisine cevirir.

    `run.page_analysis_id is None` (Paket 4B oncesi/legacy run) -> `None`
    doner, cagiran taraf eskisi gibi `fixtures.get_page_feature_snapshot`
    (sha256(url) yer tutucu) yolunu kullanmaya devam eder.

    Bagli PageAnalysis bulunamazsa/baska organizasyona aitse/henuz SUCCEEDED
    degilse/`features` semasi gecersizse `PageAnalysisDependencyError`
    firlatilir - HICBIR kosulda fixture'a sessizce duselmez (bkz. Paket 4B
    kapsami). `claim_next_queued_runs` bu run'lari zaten yalnizca bagli
    PageAnalysis SUCCEEDED ise claim ettigi icin bu fonksiyondaki durum/
    organizasyon kontrolleri normalde tetiklenmez - yalnizca savunma amacli
    ikinci bir kontrol katmanidir.

    Paket 4 Final: `PageAnalysis.source_kind`'a gore net bicimde iki ayri
    adaptore dallanir - URL/DOM kaynagi `adapt_page_analysis`
    (`feature_source="dom"`), DesignAsset (screenshot/AI) kaynagi
    `adapt_visual_page_analysis` (`feature_source="visual_heuristic"`)
    kullanir; bu iki provenance turu ASLA karismaz. Kullanicinin sihirbazda
    onayladigi CTA kutusu (varsa) `run.input_snapshot["user_confirmed_cta"]`
    icinde launch aninda degismez olarak kopyalanmistir (bkz.
    app.services.test_wizard.launch_draft) ve yalnizca gorsel yol icin
    adaptore iletilir - gorsel adaylarin onune gecer.
    """

    if run.page_analysis_id is None:
        return None

    analysis = await session.get(PageAnalysis, run.page_analysis_id)
    if analysis is None or analysis.organization_id != run.organization_id:
        raise PageAnalysisDependencyError(_PAGE_ANALYSIS_FAILED_MESSAGE)
    if analysis.status != PageAnalysisStatus.SUCCEEDED:
        raise PageAnalysisDependencyError(_PAGE_ANALYSIS_FAILED_MESSAGE)

    role = run.input_snapshot.get("role", "primary")
    try:
        if analysis.source_kind == PageAnalysisSourceKind.DESIGN_ASSET:
            user_confirmed_cta = run.input_snapshot.get("user_confirmed_cta")
            return adapt_visual_page_analysis(analysis, role=role, user_confirmed_cta=user_confirmed_cta)
        return adapt_page_analysis(analysis, role=role)
    except PageAnalysisFeatureError as exc:
        logger.warning("PageAnalysis %s icin features semasi gecersiz (run_id=%s)", analysis.id, run.id)
        raise PageAnalysisDependencyError(_PAGE_ANALYSIS_FAILED_MESSAGE) from exc


async def _process_selected_modules(run: SimulationRun, dom_input: DomAdaptedInput | None) -> dict:
    """Sihirbazda secilen gelismis modulleri (varsa) isler ve sonuclarini dondurur.

    `input_snapshot["modules"]` bos/eksikse bos sozluk doner (hicbir yan
    etki/analyzer cagrisi yapilmaz). Bilinmeyen bir modul anahtari (teoride
    olusmamali - bkz. `app.services.test_wizard.validate_patch_fields`)
    sessizce atlanir, is dusurulmez.
    """

    modules: list[str] = run.input_snapshot.get("modules") or []
    module_results: dict = {}
    snapshot = dom_input.snapshot if dom_input is not None else None
    cta_evidence = dom_input.cta_evidence if dom_input is not None else None

    for module_key in modules:
        if module_key == "campaign_cta_test":
            module_results[module_key] = run_campaign_cta_analysis(
                run.input_snapshot,
                run.deterministic_seed,
                rules_version=CURRENT_RULES_VERSION,
                page_feature_snapshot=snapshot,
                cta_evidence=cta_evidence,
            )
        elif module_key == "synthetic_attention_estimate":
            module_results[module_key] = run_synthetic_attention_estimate(
                run.input_snapshot,
                run.deterministic_seed,
                rules_version=CURRENT_RULES_VERSION,
                page_feature_snapshot=snapshot,
            )
        elif module_key == "network_device_test":
            url = run.input_snapshot.get("url")
            if not url or not isinstance(url, str):
                raise ModuleInputError("input_snapshot.url gereklidir (network_device_test)")
            module_results[module_key] = await device_network_analysis.run_network_device_test(url)

    return module_results


async def process_run(session: AsyncSession, run: SimulationRun) -> None:
    """Tek bir 'running' calistirmayi sonuna kadar isler (basarili/basarisiz/iptal)."""

    await _set_progress(
        session, run, status=SimulationStatus.RUNNING, percent=10, message="Sayfa ozellikleri okunuyor"
    )

    if await _is_cancel_requested(session, run.id):
        await _finalize_cancelled(session, run)
        return

    try:
        dom_input = await _load_page_feature_input(session, run)
    except PageAnalysisDependencyError as exc:
        await _finalize_failed(session, run, error=str(exc))
        return

    try:
        result = run_baseline_simulation(
            run.input_snapshot,
            run.deterministic_seed,
            rules_version=CURRENT_RULES_VERSION,
            page_feature_snapshot=dom_input.snapshot if dom_input is not None else None,
        )
    except SimulationInputError as exc:
        await _finalize_failed(session, run, error=str(exc))
        return

    await _set_progress(
        session, run, status=SimulationStatus.RUNNING, percent=40, message="Gelismis moduller isleniyor"
    )

    if await _is_cancel_requested(session, run.id):
        await _finalize_cancelled(session, run)
        return

    try:
        module_results = await _process_selected_modules(run, dom_input)
    except (ModuleInputError, ModuleProcessingError) as exc:
        # Herhangi bir secili modul kurtarilamaz bir hatayla basarisiz
        # olursa TUM run 'failed' isaretlenir; boylece kismi bir sonuc asla
        # 'succeeded' olarak isaretlenmez ve rezervasyon (Chip/ucretsiz hak)
        # asagida _finalize_failed -> _resolve_launch_group ile (zaten
        # idempotent) serbest birakilir - Chip haksiz tuketilmez.
        await _finalize_failed(session, run, error=str(exc))
        return

    if module_results:
        result["modules"] = module_results
        attention = module_results.get("synthetic_attention_estimate")
        if attention is not None:
            result["attention_grid"] = attention["grid"]
            result["click_attention_grid"] = attention.get("click_grid")

    # Paket 4B/4 Final: sonucun gercek PageAnalysis verisinden mi (DOM veya
    # gorsel/OpenCV) yoksa legacy sha256(url) yer tutucusundan mi turedigi
    # acikca saklanir (bkz. app.services.page_analysis_adapter) - degeri
    # dogrudan adapterin urettigi provenance'tan okunur, burada tekrar
    # UYDURULMAZ; bu sayede DOM ve gorsel provenance ASLA karismaz.
    if dom_input is not None:
        result["feature_source"] = dom_input.provenance["feature_source"]
        result["provenance"] = dom_input.provenance
    else:
        result["feature_source"] = "fixture"

    await _set_progress(
        session, run, status=SimulationStatus.RUNNING, percent=70, message="Metrikler hesaplandi"
    )

    if await _is_cancel_requested(session, run.id):
        await _finalize_cancelled(session, run)
        return

    run.result = result
    run.model_version = result["engine_version"]
    run.rules_version = result["rules_version"]
    run.fixture_version = result["fixture_version"]
    run.status = SimulationStatus.SUCCEEDED
    run.finished_at = _now()
    run.progress_percent = 100
    run.progress_message = "Tamamlandi"
    await session.flush()

    # NOT: bu run'in kendi Report'u, grup henuz tam olarak cozumlenmemis olsa
    # bile (ör. A/B'nin diger tarafi hala devam ediyorsa) hemen olusturulur -
    # kismi sonuc teshis amacli gorulebilir olmali (bkz. Paket 4.0 kapsami);
    # yalnizca Chip/entitlement muhasebesi grubun TUMU terminal olana kadar
    # ertelenir (bkz. `_resolve_launch_group`).
    await _resolve_launch_group(session, run)

    session.add(
        Report(
            organization_id=run.organization_id,
            simulation_run_id=run.id,
            title="Sentetik simulasyon sonucu",
            content=result,
        )
    )
    await session.flush()

    await simulation_progress.publish_progress(
        run.id, status=SimulationStatus.SUCCEEDED.value, percent=100, message="Tamamlandi"
    )


async def _finalize_failed(session: AsyncSession, run: SimulationRun, *, error: str) -> None:
    run.status = SimulationStatus.FAILED
    run.error = error
    run.finished_at = _now()
    run.progress_message = "Basarisiz"
    await session.flush()
    await _resolve_launch_group(session, run)
    await simulation_progress.publish_progress(
        run.id, status=SimulationStatus.FAILED.value, percent=run.progress_percent, message=error
    )


async def _finalize_cancelled(session: AsyncSession, run: SimulationRun) -> None:
    run.status = SimulationStatus.CANCELLED
    run.finished_at = _now()
    run.progress_message = "Kullanici tarafindan iptal edildi"
    await session.flush()
    await _resolve_launch_group(session, run)
    await simulation_progress.publish_progress(
        run.id,
        status=SimulationStatus.CANCELLED.value,
        percent=run.progress_percent,
        message=run.progress_message,
    )


async def _get_owned_run(
    session: AsyncSession, organization_id: uuid.UUID, run_id: uuid.UUID
) -> SimulationRun:
    result = await session.execute(select(SimulationRun).where(SimulationRun.id == run_id).with_for_update())
    run = result.scalar_one_or_none()
    if run is None or run.organization_id != organization_id:
        raise SimulationRunNotFoundError(f"Calistirma bulunamadi: {run_id}")
    return run


async def cancel_run(session: AsyncSession, organization_id: uuid.UUID, run_id: uuid.UUID) -> SimulationRun:
    """Bir calistirmayi iptal eder. Terminal durumdaysa hicbir yan etki uretmeden dondurur."""

    run = await _get_owned_run(session, organization_id, run_id)

    if run.status in (SimulationStatus.SUCCEEDED, SimulationStatus.FAILED, SimulationStatus.CANCELLED):
        return run

    if run.status == SimulationStatus.QUEUED:
        await _finalize_cancelled(session, run)
        return run

    # RUNNING: worker'in bir sonraki kontrol noktasinda gormesi icin bayrak
    # birakilir; is hemen durdurulmaz (ic tutarliligi bozmamak icin).
    run.cancel_requested = True
    await session.flush()
    return run


async def retry_run(session: AsyncSession, organization_id: uuid.UUID, run_id: uuid.UUID) -> SimulationRun:
    """Basarisiz calistirmayi ve bagli basarisiz sayfa analizini yeniden kuyruga alir."""

    run = await _get_owned_run(session, organization_id, run_id)

    if run.status != SimulationStatus.FAILED:
        raise InvalidSimulationStateError(
            f"Yalnizca 'failed' durumundaki calistirmalar yeniden denenebilir (mevcut: {run.status.value})"
        )

    # Yapisal olarak duzelemeyen bir basarisizlik (ör. ekran goruntusu
    # kaynagiyla `network_device_test`) AYNI degismez `input_snapshot` ile
    # HER ZAMAN ayni sekilde basarisiz olur - yeniden kuyruga alinsa bile
    # hicbir yeni entitlement/Chip rezervasyonu yapilmadan BURADA reddedilir
    # (bkz. `classify_run_failure`, ayni karar app.routers.simulations
    # response'undaki `retryable` alaniyla PAYLASILIR - iki yerde ayri ayri
    # hesaplanmaz).
    retryable, _failure_code = classify_run_failure(run)
    if not retryable:
        raise InvalidSimulationStateError(NETWORK_DEVICE_TEST_NON_RETRYABLE_MESSAGE)

    # URL analizi basarisizsa yalnizca SimulationRun'i QUEUED yapmak yeterli
    # degildir: claim sorgusu FAILED PageAnalysis'e bagli run'i hicbir zaman
    # almaz ve fail-blocked cron'u hemen yeniden FAILED yapar. Manuel retry,
    # kullanicinin acik yeniden deneme iradesidir; bu nedenle ayni pasif URL
    # analizine taze uc deneme hakki verir. DesignAsset kaynaklarindaki kalici
    # hatalar bu yola girmez (onlar URL disi/yapisal hata olarak ayrica ele
    # alinir).
    if run.page_analysis_id is not None:
        analysis = (
            await session.execute(
                select(PageAnalysis)
                .where(PageAnalysis.id == run.page_analysis_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if (
            analysis is not None
            and analysis.organization_id == organization_id
            and analysis.source_kind == PageAnalysisSourceKind.URL
            and analysis.status == PageAnalysisStatus.FAILED
        ):
            analysis.status = PageAnalysisStatus.QUEUED
            analysis.attempt_count = 0
            analysis.error = None
            analysis.started_at = None
            analysis.finished_at = None

    # NOT: bagimsiz `if`ler (elif degil) - bkz. `_resolve_launch_group` ve
    # app.services.test_wizard.launch_draft: bir run hem ucretsiz hak hem de
    # (secili gelismis moduller icin) bir Chip rezervasyonu tasiyabilir.
    if run.free_entitlement_feature_key:
        assert run.launch_run_id is not None
        await entitlements_service.reserve_entitlement(
            session, run.organization_id, run.free_entitlement_feature_key, run.launch_run_id
        )
    if run.chip_reservation_id:
        previous = await session.get(chip_ledger.ChipReservation, run.chip_reservation_id)
        # Grup (A/B karsilastirmasi) henuz cozumlenmemisse (ör. kardes
        # calistirma hala devam ediyorken bu calistirma basarisiz olup
        # yeniden deneniyorsa) mevcut rezervasyon hala 'reserved' durumdadir
        # ve HENUZ serbest birakilmamistir - bu durumda YENI bir rezervasyon
        # OLUSTURULMAZ (aksi halde ayni karsilastirma icin ayni anda iki
        # rezervasyon acik kalir, bakiye gereksiz yere ikinci kez dusurulur).
        # Yeni rezervasyon yalnizca mevcut rezervasyon zaten sonuclanmissa
        # (RELEASED - grup daha once basarisiz/iptal olarak cozumlenmis)
        # olusturulur.
        already_reserved = (
            previous is not None and previous.status == chip_ledger.ChipReservationStatus.RESERVED
        )
        if not already_reserved:
            amount = previous.amount if previous is not None else 0
            if amount > 0:
                new_reservation = await chip_ledger.reserve_chips(
                    session,
                    run.organization_id,
                    amount,
                    f"simulasyon yeniden deneme: {run.id}",
                    run_id=run.launch_run_id,
                    idempotency_key=f"simulation-retry:{run.id}:{run.attempt_count + 1}",
                )
                run.chip_reservation_id = new_reservation.id

    # Baseline basarisizliginda AI rezervasyonu da serbest birakilir. Baseline
    # manuel olarak yeniden deneniyorsa AI raporunun sonradan sessizce eksik
    # kalmamasi icin bu ayri rezervasyon da yeniden kurulur ve launch grubunun
    # tum sibling run'larina ayni yeni id yazilir.
    if run.ai_chip_reservation_id:
        previous_ai = await session.get(chip_ledger.ChipReservation, run.ai_chip_reservation_id)
        ai_already_reserved = (
            previous_ai is not None
            and previous_ai.status == chip_ledger.ChipReservationStatus.RESERVED
        )
        if not ai_already_reserved and previous_ai is not None and previous_ai.amount > 0:
            ai_reservation = await chip_ledger.reserve_chips(
                session,
                run.organization_id,
                previous_ai.amount,
                f"AI raporu baseline yeniden deneme: {run.id}",
                run_id=run.launch_run_id or run.id,
                idempotency_key=(
                    f"ai-report-baseline-retry:{run.launch_run_id or run.id}:{run.attempt_count + 1}"
                ),
            )
            if run.launch_run_id is not None:
                siblings = list(
                    (
                        await session.execute(
                            select(SimulationRun)
                            .where(SimulationRun.launch_run_id == run.launch_run_id)
                            .with_for_update()
                        )
                    ).scalars()
                )
            else:
                siblings = [run]
            for sibling in siblings:
                if sibling.ai_chip_reservation_id is not None:
                    sibling.ai_chip_reservation_id = ai_reservation.id

    # Heatmap rezervasyonu da (ai_report ile ayni gerekce) baseline yeniden
    # denemede yeniden kurulur ve gruptaki tum sibling run'lara ayni yeni id
    # yazilir - aksi halde baseline retry sonrasi heatmap sessizce eksik kalirdi.
    if run.heatmap_chip_reservation_id:
        previous_hm = await session.get(chip_ledger.ChipReservation, run.heatmap_chip_reservation_id)
        hm_already_reserved = (
            previous_hm is not None
            and previous_hm.status == chip_ledger.ChipReservationStatus.RESERVED
        )
        if not hm_already_reserved and previous_hm is not None and previous_hm.amount > 0:
            hm_reservation = await chip_ledger.reserve_chips(
                session,
                run.organization_id,
                previous_hm.amount,
                f"AI etkilesim isi haritasi baseline yeniden deneme: {run.id}",
                run_id=run.launch_run_id or run.id,
                idempotency_key=(
                    f"ai-heatmap-baseline-retry:{run.launch_run_id or run.id}:{run.attempt_count + 1}"
                ),
            )
            if run.launch_run_id is not None:
                hm_siblings = list(
                    (
                        await session.execute(
                            select(SimulationRun)
                            .where(SimulationRun.launch_run_id == run.launch_run_id)
                            .with_for_update()
                        )
                    ).scalars()
                )
            else:
                hm_siblings = [run]
            for sibling in hm_siblings:
                if sibling.heatmap_chip_reservation_id is not None:
                    sibling.heatmap_chip_reservation_id = hm_reservation.id

    run.status = SimulationStatus.QUEUED
    run.error = None
    run.result = None
    run.progress_percent = 0
    run.progress_message = "Yeniden kuyruga alindi"
    run.started_at = None
    run.finished_at = None
    run.cancel_requested = False
    await session.flush()
    return run


async def reap_stale_running_runs(
    session: AsyncSession,
    *,
    timeout_seconds: int = STALE_RUNNING_TIMEOUT_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
) -> int:
    """'running' durumunda takili kalmis (worker cokmesi vb.) isleri kurtarir."""

    cutoff = _now() - timedelta(seconds=timeout_seconds)
    result = await session.execute(
        select(SimulationRun)
        .where(SimulationRun.status == SimulationStatus.RUNNING, SimulationRun.updated_at < cutoff)
        .with_for_update(skip_locked=True)
    )
    stale_runs = list(result.scalars().all())

    for run in stale_runs:
        if run.attempt_count < max_attempts:
            run.status = SimulationStatus.QUEUED
            run.progress_percent = 0
            run.progress_message = "Zaman asimi, yeniden kuyruga alindi"
            run.started_at = None
        else:
            run.status = SimulationStatus.FAILED
            run.error = "Zaman asimi: maksimum deneme sayisina ulasildi"
            run.finished_at = _now()
            await session.flush()
            await _resolve_launch_group(session, run)
            continue
        await session.flush()

    return len(stale_runs)


async def _process_claimed_run(run_id: uuid.UUID) -> None:
    """Tek bir kilitli calistirmayi kendi oturumunda isler; hata halinde 'failed' isaretler."""

    async with async_session_maker() as session:
        try:
            result = await session.execute(
                select(SimulationRun).where(SimulationRun.id == run_id).with_for_update()
            )
            run = result.scalar_one_or_none()
            if run is None or run.status != SimulationStatus.RUNNING:
                return
            await process_run(session, run)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("process_run basarisiz oldu (run_id=%s)", run_id)

    async with async_session_maker() as session:
        try:
            result = await session.execute(
                select(SimulationRun).where(SimulationRun.id == run_id).with_for_update()
            )
            run = result.scalar_one_or_none()
            if run is not None and run.status == SimulationStatus.RUNNING:
                await _finalize_failed(session, run, error="Beklenmeyen motor hatasi")
                await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("run %s icin basarisizlik kaydi da basarisiz oldu", run_id)


async def run_queue_cycle() -> None:
    """arq cron girdisi: bekleyen isleri alir ve isler. Kendi oturumunu acar."""

    async with async_session_maker() as session:
        try:
            runs = await claim_next_queued_runs(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("claim_next_queued_runs basarisiz oldu")
            return

    for run in runs:
        await _process_claimed_run(run.id)


async def run_reap_cycle() -> None:
    """arq cron girdisi: 'running'de takili kalmis isleri kurtarir. Kendi oturumunu acar."""

    async with async_session_maker() as session:
        try:
            await reap_stale_running_runs(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("reap_stale_running_runs basarisiz oldu")


async def run_fail_blocked_cycle() -> None:
    """arq cron girdisi: bagli PageAnalysis'i FAILED olan QUEUED run'lari FAILED yapar.

    Kendi oturumunu acar (bkz. `fail_runs_blocked_by_failed_page_analysis`).
    """

    async with async_session_maker() as session:
        try:
            await fail_runs_blocked_by_failed_page_analysis(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("fail_runs_blocked_by_failed_page_analysis basarisiz oldu")


__all__ = [
    "CLAIM_BATCH_SIZE",
    "STALE_RUNNING_TIMEOUT_SECONDS",
    "MAX_ATTEMPTS",
    "NETWORK_DEVICE_TEST_NON_RETRYABLE_MESSAGE",
    "NETWORK_DEVICE_TEST_REQUIRES_URL_CODE",
    "classify_run_failure",
    "claim_next_queued_runs",
    "fail_runs_blocked_by_failed_page_analysis",
    "process_run",
    "cancel_run",
    "retry_run",
    "reap_stale_running_runs",
    "run_queue_cycle",
    "run_reap_cycle",
    "run_fail_blocked_cycle",
]
