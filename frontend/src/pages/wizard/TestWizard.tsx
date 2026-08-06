import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  type AnalysisModuleDefinition,
  ApiError,
  type QuoteResponse,
  type WizardDraftPayload,
  type WizardLaunchWarning,
  createWizardDraft,
  getAnalysisModuleCatalog,
  getQuote,
  getUsageSummary,
  getWizardDraft,
  launchWizardDraft,
  listPersonaPresets,
  patchWizardDraft,
} from "../../api/client";
import {
  AI_REPORT_PERSONA_REQUIRED_MESSAGE,
  aiReportRequiresPersonaSelection,
} from "./aiReportPersona";
import { incompatibleSelectedModuleKeys } from "./moduleCompatibility";
import Step1Details from "./Step1Details";
import Step2Urls from "./Step2Urls";
import Step3Personas from "./Step3Personas";
import Step4Modules from "./Step4Modules";
import Step5Summary from "./Step5Summary";
import WizardStepper from "./WizardStepper";

const QUOTE_TEST_TYPE_BY_WIZARD_TYPE: Record<string, string> = {
  existing_site_basic_ux: "basic_ux_test",
  ab_comparison: "basic_ux_test",
  accessibility_precheck: "accessibility_precheck",
};

// Persona secimi 3. adimda, analiz modulleri (ai_report dahil) 4. adimda
// yapilir; bu yuzden `ai_report` + persona uyumsuzlugu ancak 4. adimdan
// ayrilirken (her ikisi de bilinirken) yakalanabilir - eksikse kullanici 3.
// adima geri yonlendirilir (bkz. handleNext / handleLaunch).
const PERSONA_STEP = 3;
const PERSONA_DEPENDENT_STEP = 4;

function isValidHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function validateStep(step: number, payload: WizardDraftPayload): Record<string, string> {
  const errors: Record<string, string> = {};

  if (step === 1) {
    if (!payload.project_id) errors.project_id = "Proje seçmelisiniz.";
    if (!payload.name?.trim()) errors.name = "Test adı gereklidir.";
    if (!payload.target_task?.trim()) errors.target_task = "Hedef görev gereklidir.";
    if (!payload.test_type) errors.test_type = "Test türü seçmelisiniz.";
  }

  if (step === 2) {
    const isCurrentScreenshotSource = payload.current_source_type === "screenshot";

    if (isCurrentScreenshotSource) {
      if (!payload.current_design_asset_id) {
        errors.current_design_asset_id = "Bir tasarım ekran görüntüsü yüklemelisiniz.";
      }
    } else if (!payload.current_url?.trim()) {
      errors.current_url = "URL gereklidir.";
    } else if (!isValidHttpUrl(payload.current_url)) {
      errors.current_url = "Geçerli bir http(s) URL girin.";
    }
    if (payload.test_type === "ab_comparison") {
      const isNewVisualSource =
        payload.new_source_type === "screenshot" || payload.new_source_type === "ai_generated";
      if (isNewVisualSource) {
        if (!payload.new_design_asset_id) {
          errors.new_design_asset_id = "Bir tasarım ekran görüntüsü yüklemelisiniz.";
        }
      } else if (!payload.new_url?.trim()) {
        errors.new_url = "Yeni tasarım URL'si gereklidir.";
      } else if (!isValidHttpUrl(payload.new_url)) {
        errors.new_url = "Geçerli bir http(s) URL girin.";
      }
    }
  }

  if (step === 3) {
    const count = payload.persona_count;
    if (count === undefined || count === null) {
      errors.persona_count = "Persona sayısı gereklidir.";
    } else if (count < 100 || count > 50_000) {
      errors.persona_count = "Persona sayısı 100 ile 50.000 arasında olmalıdır.";
    }
    if (!payload.target_audience?.trim()) errors.target_audience = "Hedef kitle gereklidir.";
  }

  if (step === 5) {
    if (payload.authorization_confirmed !== true) {
      errors.authorization_confirmed = "Devam etmek için yetki onayı vermelisiniz.";
    }
  }

  return errors;
}

export default function TestWizard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  const draftId = searchParams.get("draft");
  const preselectedProjectId = searchParams.get("project");
  const preselectedModulesRaw = searchParams.get("modules");
  const preselectedPersonaPresetId = searchParams.get("persona_preset");

  const [payload, setPayload] = useState<WizardDraftPayload>({});
  const [currentStep, setCurrentStep] = useState(1);
  const [draftStatus, setDraftStatus] = useState<"draft" | "launched" | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [moduleClearedNotice, setModuleClearedNotice] = useState<string | null>(null);
  const savedNoticeTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [quote, setQuote] = useState<QuoteResponse | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [quoteError, setQuoteError] = useState<string | null>(null);
  const [chipBalance, setChipBalance] = useState<number | null>(null);
  const [moduleCatalog, setModuleCatalog] = useState<AnalysisModuleDefinition[]>([]);

  const [isLaunching, setIsLaunching] = useState(false);
  const [launchResult, setLaunchResult] = useState<{
    engineStatusMessage: string;
    usedFreeEntitlement: boolean;
    warnings: WizardLaunchWarning[];
  } | null>(null);

  const hydrated = useRef(false);
  const saveTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showSavedNotice = () => {
    setSavedNotice("Seçim kaydedildi.");
    if (savedNoticeTimeout.current) clearTimeout(savedNoticeTimeout.current);
    savedNoticeTimeout.current = setTimeout(() => setSavedNotice(null), 2500);
  };

  useEffect(() => {
    return () => {
      if (savedNoticeTimeout.current) clearTimeout(savedNoticeTimeout.current);
    };
  }, []);

  // --- Taslagi yukle veya olustur (sayfa yenilenince kaybolmamasi icin URL'de tutulur) ---
  useEffect(() => {
    let cancelled = false;

    async function init() {
      try {
        if (draftId) {
          const draft = await getWizardDraft(draftId);
          if (cancelled) return;
          setPayload(draft.payload);
          setCurrentStep(draft.current_step);
          setDraftStatus(draft.status);
        } else {
          const draft = await createWizardDraft();
          if (cancelled) return;

          const initialPayload: WizardDraftPayload = {};
          if (preselectedProjectId) initialPayload.project_id = preselectedProjectId;

          if (preselectedModulesRaw || preselectedPersonaPresetId) {
            // Katalog/personalar sayfalarindan gelen baslangic secimleri yalnizca
            // guncel backend verisiyle dogrulanirsa draft'a uygulanir.
            const [catalog, presets] = await Promise.all([
              getAnalysisModuleCatalog(),
              listPersonaPresets(),
            ]);
            if (cancelled) return;
            setModuleCatalog(catalog.modules);

            if (preselectedModulesRaw) {
              const allowedKeys = new Set(
                catalog.modules
                  .filter((module) => module.selectable_in_wizard)
                  .map((module) => module.key),
              );
              const validModules = preselectedModulesRaw
                .split(",")
                .map((key) => key.trim())
                .filter((key) => allowedKeys.has(key));
              if (validModules.length > 0) initialPayload.modules = validModules;
            }

            if (
              preselectedPersonaPresetId &&
              presets.some((preset) => preset.id === preselectedPersonaPresetId)
            ) {
              initialPayload.persona_preset_id = preselectedPersonaPresetId;
            }
          }

          if (Object.keys(initialPayload).length > 0) {
            await patchWizardDraft(draft.id, initialPayload);
            showSavedNotice();
          }
          setPayload({ ...draft.payload, ...initialPayload });
          setCurrentStep(draft.current_step);
          setDraftStatus(draft.status);
          setSearchParams({ draft: draft.id }, { replace: true });
        }
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof ApiError ? err.message : "Sihirbaz taslağı yüklenemedi.");
        }
      } finally {
        if (!cancelled) {
          hydrated.current = true;
          setIsLoading(false);
        }
      }
    }

    init();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftId]);

  useEffect(() => {
    getUsageSummary()
      .then((summary) => setChipBalance(summary.chip_balance))
      .catch(() => setChipBalance(null));
  }, []);

  useEffect(() => {
    getAnalysisModuleCatalog()
      .then((catalog) => setModuleCatalog(catalog.modules))
      .catch(() => setModuleCatalog([]));
  }, []);

  // --- Degisiklikleri otomatik kaydet (debounce ile PATCH) ---
  useEffect(() => {
    if (!hydrated.current || !draftId || draftStatus === "launched") return;

    if (saveTimeout.current) clearTimeout(saveTimeout.current);
    saveTimeout.current = setTimeout(() => {
      patchWizardDraft(draftId, payload)
        .then(() => showSavedNotice())
        .catch((err) => {
          setBanner(err instanceof ApiError ? err.message : "Taslak kaydedilemedi.");
        });
    }, 500);

    return () => {
      if (saveTimeout.current) clearTimeout(saveTimeout.current);
    };
  }, [payload, draftId, draftStatus]);

  // --- Canli teklif (quote): her ilgili degisiklikte Prompt 3 servisinden ---
  useEffect(() => {
    const quoteTestType = payload.test_type
      ? QUOTE_TEST_TYPE_BY_WIZARD_TYPE[payload.test_type]
      : null;
    if (!quoteTestType || !payload.persona_count || payload.persona_count < 100) {
      setQuote(null);
      return;
    }

    let cancelled = false;
    setQuoteLoading(true);
    setQuoteError(null);

    getQuote({
      persona_count: payload.persona_count,
      test_type: quoteTestType,
      modules: payload.modules ?? [],
    })
      .then((result) => {
        if (!cancelled) setQuote(result);
      })
      .catch((err) => {
        if (!cancelled) {
          setQuote(null);
          setQuoteError(err instanceof ApiError ? err.message : "Teklif hesaplanamadı.");
        }
      })
      .finally(() => {
        if (!cancelled) setQuoteLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [payload.test_type, payload.persona_count, payload.modules]);

  // --- Kaynak turu (screenshot'a) degistiginde artik uyumsuz olan secili
  // gelismis modulleri otomatik temizle (bkz. moduleCompatibility.ts) ---
  //
  // Yalnizca kaynak turu/test turu/katalog degistiginde tetiklenir -
  // `payload.modules` KENDISI bagimlilik DEGILDIR: aksi halde bu efektin
  // kendi `setPayload` cagrisi yeniden tetiklenip sonsuz bir
  // guncelleme/autosave dongusune yol acardi. Katalog ilk yuklendiginde
  // (moduleCatalog [] -> dolu) VEYA taslak hydrate edildiginde (query-string
  // on-secim/eski draft) de bir kez calisir, boylece varsayilan/preselected
  // uyumsuz bir modul de sessizce kalmaz.
  useEffect(() => {
    if (!hydrated.current || moduleCatalog.length === 0) return;

    const incompatibleKeys = incompatibleSelectedModuleKeys(payload, moduleCatalog);
    if (incompatibleKeys.length === 0) return;

    const removedNames = incompatibleKeys
      .map((key) => moduleCatalog.find((module) => module.key === key)?.name ?? key)
      .join(", ");

    setPayload((current) => ({
      ...current,
      modules: (current.modules ?? []).filter((key) => !incompatibleKeys.includes(key)),
    }));
    setModuleClearedNotice(
      `Ekran görüntüsü kaynağı seçildiği için ${removedNames} kaldırıldı. Bu modül yalnızca canlı URL ile çalışır.`,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload.current_source_type, payload.new_source_type, payload.test_type, moduleCatalog]);

  const handleChange = <K extends keyof WizardDraftPayload>(
    field: K,
    value: WizardDraftPayload[K],
  ) => {
    setFieldErrors((current) => {
      if (!(field in current)) return current;
      const next = { ...current };
      delete next[field];
      return next;
    });
    setPayload((current) => {
      const next: WizardDraftPayload = { ...current, [field]: value };
      // Erisilebilirlik on kontrolu DOM/sayfa yapisi gerektirdigi icin daima
      // yalnizca URL kabul eder; kullanici test turunu buna degistirdiginde
      // ekran goruntusu secili kalmis olsaydı sonraki otomatik kaydetme
      // (autosave) backend tarafindan 400 ile reddedilirdi - bu yuzden
      // burada onceden URL'e sifirlanir (bkz. backend/app/services/
      // test_wizard.validate_source_type_for_test_type). A/B karsilastirmasi
      // ise artik her iki tarafta da ekran goruntusunu kabul eder, bu
      // nedenle bu sifirlama A/B'ye GECILDIGINDE artik uygulanmaz.
      if (
        field === "test_type" &&
        value === "accessibility_precheck" &&
        next.current_source_type === "screenshot"
      ) {
        next.current_source_type = "url";
      }
      // "Tasarim B" (new_*) alanlari yalnizca A/B karsilastirmasinda anlamlidir;
      // baska bir test turune gecildiginde sifirlanir ki sonraki bir A/B
      // taslaginda veya baska bir test turunde eski secim kalintisi kalmasin.
      if (field === "test_type" && value !== "ab_comparison") {
        next.new_source_type = "url";
        next.new_design_asset_id = undefined;
        next.new_ai_generation_id = undefined;
      }
      return next;
    });
  };

  const goToStep = async (nextStep: number) => {
    if (!draftId) return;
    setCurrentStep(nextStep);
    try {
      await patchWizardDraft(draftId, {}, nextStep);
    } catch (err) {
      setBanner(
        err instanceof ApiError ? err.message : "Adım geçişi kaydedilemedi. Lütfen tekrar deneyin.",
      );
    }
  };

  const handleNext = () => {
    const errors = validateStep(currentStep, payload);
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    // `ai_report` (4. adimda secilir) en az bir persona secimi (3. adim)
    // gerektirir; eksikse backend launch'i reddeder. Kullaniciyi burada,
    // ilerlemesine izin vermeden ONCE persona adimina yonlendir ve nedeni
    // acikca goster (bkz. aiReportRequiresPersonaSelection - ayni kural
    // backend'de de bagimsiz zorlanir).
    if (currentStep === PERSONA_DEPENDENT_STEP && aiReportRequiresPersonaSelection(payload)) {
      setBanner(AI_REPORT_PERSONA_REQUIRED_MESSAGE);
      void goToStep(PERSONA_STEP);
      return;
    }

    setBanner(null);
    void goToStep(Math.min(currentStep + 1, 5));
  };

  const handleBack = () => {
    void goToStep(Math.max(currentStep - 1, 1));
  };

  const handleLaunch = async () => {
    if (!draftId) return;
    const errors = validateStep(5, payload);
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    // Savunma: 4. adim gecisi normalde bu kombinasyonu engeller, ama stepper
    // ile dogrudan atlama / eski taslak hydration gibi durumlarda 5. adima
    // ulasilmis olabilir - launch'i tetiklemeden persona adimina yonlendir.
    if (aiReportRequiresPersonaSelection(payload)) {
      setBanner(AI_REPORT_PERSONA_REQUIRED_MESSAGE);
      void goToStep(PERSONA_STEP);
      return;
    }

    setIsLaunching(true);
    setBanner(null);
    try {
      // Adim/verinin sunucuda guncel oldugundan emin ol, sonra baslat.
      await patchWizardDraft(draftId, payload, 5);
      const result = await launchWizardDraft(draftId);
      setDraftStatus("launched");
      setLaunchResult({
        engineStatusMessage: result.engine_status_message,
        usedFreeEntitlement: result.used_free_entitlement,
        warnings: result.warnings,
      });
    } catch (err) {
      // `ApiError.message`, backend'in guvenli (stack trace/SQL/tenant
      // detayi ICERMEYEN) `detail` alanindan turer - bilinmeyen bir 5xx
      // icin de `apiFetch` zaten guvenli bir yer tutucu metin uretir (bkz.
      // app/api/client.ts `rawFetch` - govde yoksa/parse edilemezse `API
      // istegi basarisiz: <status> <statusText>`). Bu yuzden STATUS KODUNA
      // BAKMAKSIZIN her ApiError'un gercek mesaji gosterilir - yalnizca
      // gercekten ApiError OLMAYAN (ag hatasi, beklenmeyen JS istisnasi)
      // durumlarda jenerik Turkce mesaja dusulur. Eskiden yalnizca 400/402
      // ozel olarak gosteriliyordu; digger butun durumlar (404/409/422/5xx)
      // sessizce "Test baslatilamadi" mesajina indirgeniyordu - kullanici
      // gercek nedeni (ör. "asset artik kullanilamiyor") hic goremiyordu.
      if (err instanceof ApiError) {
        setBanner(err.message);
        console.error("Test baslatma basarisiz (status=%s):", err.status, err.message, err.body);
      } else {
        setBanner("Test başlatılamadı. Lütfen tekrar deneyin.");
        console.error("Test baslatma basarisiz (bilinmeyen hata):", err);
      }
    } finally {
      setIsLaunching(false);
    }
  };

  if (isLoading) {
    return <p className="page-placeholder">Sihirbaz yükleniyor…</p>;
  }

  if (loadError) {
    return <p className="auth-error">{loadError}</p>;
  }

  if (draftStatus === "launched" && launchResult) {
    return (
      <section aria-labelledby="wizard-heading" className="wizard">
        <h1 id="wizard-heading" className="page-heading">
          Test kuyruğa alındı
        </h1>
        <div className="auth-notice">
          {/* Launch istegi yalnizca isi kuyruga alir - worker asenkron olarak
              isler (bkz. app.services.simulation_worker). Bu ekran "analiz
              tamamlandi"/"rapor hazir" gibi YANLIS bir izlenim VERMEMELIDIR -
              kullanici gercek durumu Simulasyonlar sayfasindan takip eder. */}
          <p>
            Testiniz başlatıldı. Analiz durumunu Simülasyonlar sayfasından takip edebilirsiniz. Rapor,
            tüm çalıştırmalar başarıyla tamamlandıktan sonra oluşturulur.
          </p>
          <p>
            {launchResult.usedFreeEntitlement
              ? "Test, ücretsiz hakkınız kullanılarak başlatıldı."
              : "Test, Chip bakiyenizden düşülerek başlatıldı."}
          </p>
          <p>{launchResult.engineStatusMessage}</p>
        </div>
        {launchResult.warnings.length > 0 && (
          <div className="auth-notice" role="status">
            {launchResult.warnings.map((warning, index) => (
              <p key={index}>
                {warning.code === "stale_cta_annotation_cleared"
                  ? `Tasarım değiştiği için önceki CTA seçiminiz temizlendi (${warning.slot === "current" ? "Tasarım A" : "Tasarım B"}). CTA'ya özel değerlendirmeler bu durumda aday düzeyinde kalır.`
                  : warning.message}
              </p>
            ))}
          </div>
        )}
        <div className="modal__actions">
          <button type="button" className="btn-secondary" onClick={() => navigate("/projeler")}>
            Projelere dön
          </button>
          <Link to="/simulasyonlar" className="auth-submit">
            Simülasyonları görüntüle
          </Link>
        </div>
      </section>
    );
  }

  const isAbComparison = payload.test_type === "ab_comparison";
  const launchButtonLabel = quote?.free_entitlement_applicable
    ? "Ücretsiz hakkı kullan ve başlat"
    : "Chip ile başlat";

  // Paket 4 Final: screenshot/AI kaynaklarla launch engeli backend'de
  // kaldirildi (bkz. app.services.test_wizard.launch_draft -
  // `_revalidate_launch_sources`) - silinmis/expired/cross-tenant asset veya
  // kabul edilmemis bir AI sonucu gibi durumlar artik butonu ONCEDEN
  // KAPATMAZ; bunun yerine launch denemesi sunucu tarafinda reddedilir ve
  // sonuc, mevcut hata banner'i (asagida `banner` state'i, ApiError 400/402
  // yakalamasi) uzerinden ayni yolla gosterilir - Chip/entitlement'in
  // TUKETILMEDIGI acikca belirtilir (bkz. handleLaunch catch bloklari).
  const missingSourceForLaunch =
    (payload.current_source_type === "screenshot" && !payload.current_design_asset_id) ||
    (isAbComparison &&
      (payload.new_source_type === "screenshot" || payload.new_source_type === "ai_generated") &&
      !payload.new_design_asset_id);

  // Savunma katmani: normalde yukaridaki otomatik temizleme efekti
  // uyumsuz bir modulun `payload.modules`te kalmasina IZIN VERMEZ; yine de
  // (ör. bir yaris durumu/legacy hydration) uyumsuz bir modul hala
  // payload'daysa Step5'teki baslatma butonu KAPATILIR - frontend kontrolu
  // bir guvenlik siniri degildir, backend launch-oncesi dogrulamasi
  // (`app.services.test_wizard.validate_module_source_compatibility`) HER
  // ZAMAN bagimsiz olarak yeniden calisir.
  const incompatibleModulesForLaunch = incompatibleSelectedModuleKeys(payload, moduleCatalog);

  // Savunma katmani: `ai_report` secili ama persona secimi yoksa launch backend
  // tarafindan reddedilir (bkz. validate_ai_report_persona_requirement); butonu
  // onceden kapat ve kullaniciyi persona adimina yonlendiren aciklamayi goster.
  const aiReportMissingPersonaForLaunch = aiReportRequiresPersonaSelection(payload);

  return (
    <section aria-labelledby="wizard-heading" className="wizard">
      <h1 id="wizard-heading" className="page-heading">
        Yeni Test
      </h1>
      <WizardStepper currentStep={currentStep} />

      {banner && (
        <p className="auth-error" role="alert">
          {banner}
        </p>
      )}
      {savedNotice && (
        <p className="auth-notice" role="status">
          {savedNotice}
        </p>
      )}
      {moduleClearedNotice && (
        <p className="auth-notice" role="status">
          {moduleClearedNotice}
        </p>
      )}

      <div className="wizard-panel">
        {currentStep === 1 && (
          <Step1Details payload={payload} fieldErrors={fieldErrors} onChange={handleChange} />
        )}
        {currentStep === 2 && (
          <Step2Urls payload={payload} fieldErrors={fieldErrors} onChange={handleChange} draftId={draftId} />
        )}
        {currentStep === 3 && (
          <Step3Personas payload={payload} fieldErrors={fieldErrors} onChange={handleChange} />
        )}
        {currentStep === 4 && (
          <Step4Modules
            payload={payload}
            fieldErrors={fieldErrors}
            onChange={handleChange}
            quote={quote}
            quoteLoading={quoteLoading}
            quoteError={quoteError}
            chipBalance={chipBalance}
            moduleCatalog={moduleCatalog}
          />
        )}
        {currentStep === 5 && (
          <Step5Summary
            payload={payload}
            fieldErrors={fieldErrors}
            onChange={handleChange}
            quote={quote}
            quoteLoading={quoteLoading}
            quoteError={quoteError}
            chipBalance={chipBalance}
            moduleCatalog={moduleCatalog}
          />
        )}

        <div className="wizard-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={handleBack}
            disabled={currentStep === 1}
          >
            Geri
          </button>
          {currentStep < 5 ? (
            <button type="button" className="auth-submit" onClick={handleNext}>
              İleri
            </button>
          ) : (
            <div className="wizard-launch-group">
              <button
                type="button"
                className="auth-submit"
                onClick={handleLaunch}
                disabled={
                  isLaunching ||
                  payload.authorization_confirmed !== true ||
                  missingSourceForLaunch ||
                  incompatibleModulesForLaunch.length > 0 ||
                  aiReportMissingPersonaForLaunch
                }
              >
                {isLaunching ? "Başlatılıyor…" : launchButtonLabel}
              </button>
              {aiReportMissingPersonaForLaunch && (
                <p className="auth-error" role="status">
                  {AI_REPORT_PERSONA_REQUIRED_MESSAGE}
                </p>
              )}
              {missingSourceForLaunch && (
                <p className="wizard-field-hint" role="status">
                  Devam etmeden önce eksik tasarım kaynağını (ekran görüntüsü) tamamlamalısınız.
                </p>
              )}
              {incompatibleModulesForLaunch.length > 0 && (
                <p className="auth-error" role="status">
                  Seçili tasarım kaynağıyla uyumsuz bir analiz modülü var (Ağ ve cihaz testi yalnızca
                  canlı URL ile çalışır). Devam etmeden önce 4. adımdan bu modülü kaldırın.
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
