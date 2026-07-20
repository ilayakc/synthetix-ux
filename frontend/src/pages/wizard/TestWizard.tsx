import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  type AnalysisModuleDefinition,
  ApiError,
  type QuoteResponse,
  type WizardDraftPayload,
  createWizardDraft,
  getAnalysisModuleCatalog,
  getQuote,
  getUsageSummary,
  getWizardDraft,
  launchWizardDraft,
  listPersonaPresets,
  patchWizardDraft,
} from "../../api/client";
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
    if (!payload.current_url?.trim()) {
      errors.current_url = "URL gereklidir.";
    } else if (!isValidHttpUrl(payload.current_url)) {
      errors.current_url = "Geçerli bir http(s) URL girin.";
    }
    if (payload.test_type === "ab_comparison") {
      if (!payload.new_url?.trim()) {
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
    setPayload((current) => ({ ...current, [field]: value }));
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
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setBanner(err.message);
      } else if (err instanceof ApiError && err.status === 400) {
        setBanner(err.message);
      } else {
        setBanner("Test başlatılamadı. Lütfen tekrar deneyin.");
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
          Test başlatıldı
        </h1>
        <div className="auth-notice">
          <p>
            {launchResult.usedFreeEntitlement
              ? "Test, ücretsiz hakkınız kullanılarak başlatıldı."
              : "Test, Chip bakiyenizden düşülerek başlatıldı."}
          </p>
          <p>{launchResult.engineStatusMessage}</p>
        </div>
        <div className="modal__actions">
          <button type="button" className="auth-submit" onClick={() => navigate("/projeler")}>
            Projelere dön
          </button>
        </div>
      </section>
    );
  }

  const launchButtonLabel = quote?.free_entitlement_applicable
    ? "Ücretsiz hakkı kullan ve başlat"
    : "Chip ile başlat";

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

      <div className="wizard-panel">
        {currentStep === 1 && (
          <Step1Details payload={payload} fieldErrors={fieldErrors} onChange={handleChange} />
        )}
        {currentStep === 2 && (
          <Step2Urls payload={payload} fieldErrors={fieldErrors} onChange={handleChange} />
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
            className="auth-google-button"
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
            <button
              type="button"
              className="auth-submit"
              onClick={handleLaunch}
              disabled={isLaunching || payload.authorization_confirmed !== true}
            >
              {isLaunching ? "Başlatılıyor…" : launchButtonLabel}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
