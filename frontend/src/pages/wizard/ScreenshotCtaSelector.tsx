import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  type CtaAnnotation,
  type PageAnalysisResponse,
  createPageAnalysisForDesignAsset,
  getPageAnalysis,
  patchWizardDraft,
} from "../../api/client";

const POLL_INTERVAL_MS = 2000;
const MAX_POLL_ATTEMPTS = 30; // ~60 saniye - yerel/deterministik OpenCV analizi bundan çok daha hızlı tamamlanmalıdır.

export interface ScreenshotCtaSelectorProps {
  /** "current" (Tasarım A) veya "new" (Tasarım B) - taslak payload'ındaki
   * `current_cta_annotation`/`new_cta_annotation` alanlarından hangisinin
   * okunup/yazılacağını belirler; iki taraf birbirinden bağımsızdır. */
  slot: "current" | "new";
  label: string;
  draftId: string | null;
  assetId: string | undefined;
  annotation: CtaAnnotation | null | undefined;
  onAnnotationChange: (value: CtaAnnotation | null) => void;
}

type PollState = "idle" | "loading" | "ready" | "failed" | "timeout";

const CANDIDATE_DISCLAIMER_FALLBACK =
  "Bu alanlar görsel özelliklere göre CTA adayı olarak önerilir. Gerçek kullanıcı tıklaması veya göz takibi verisi değildir.";

function formatScore(score: number): string {
  return `${Math.round(score * 100)}`;
}

export default function ScreenshotCtaSelector({
  slot,
  label,
  draftId,
  assetId,
  annotation,
  onAnnotationChange,
}: ScreenshotCtaSelectorProps) {
  const [pollState, setPollState] = useState<PollState>("idle");
  const [analysis, setAnalysis] = useState<PageAnalysisResponse | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  const [manualMode, setManualMode] = useState(false);
  const [manualBox, setManualBox] = useState({ x: 0.1, y: 0.1, w: 0.2, h: 0.08 });

  const [saveError, setSaveError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);

  const pollTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollAttempts = useRef(0);
  const startedForAssetId = useRef<string | null>(null);
  const previousAssetId = useRef<string | undefined>(assetId);

  function stopPolling() {
    if (pollTimeout.current) {
      clearTimeout(pollTimeout.current);
      pollTimeout.current = null;
    }
  }

  function schedulePoll(analysisId: string, forAssetId: string) {
    stopPolling();
    pollTimeout.current = setTimeout(async () => {
      // Bekleme sırasında asset değişmiş olabilir (kullanıcı görseli
      // değiştirdi/kaldırdı) - bu durumda GEÇ GELEN eski sonuç yeni ekrana
      // hiçbir şekilde yazılmamalıdır.
      if (startedForAssetId.current !== forAssetId) return;
      try {
        const updated = await getPageAnalysis(analysisId);
        if (startedForAssetId.current !== forAssetId) return;
        setAnalysis(updated);
        if (updated.status === "succeeded") {
          setPollState("ready");
        } else if (updated.status === "failed") {
          setPollState("failed");
        } else {
          pollAttempts.current += 1;
          if (pollAttempts.current >= MAX_POLL_ATTEMPTS) {
            setPollState("timeout");
          } else {
            schedulePoll(analysisId, forAssetId);
          }
        }
      } catch (err) {
        if (startedForAssetId.current !== forAssetId) return;
        setAnalysisError(err instanceof ApiError ? err.message : "Analiz durumu sorgulanamadı.");
        setPollState("failed");
      }
    }, POLL_INTERVAL_MS);
  }

  useEffect(() => {
    stopPolling();
    pollAttempts.current = 0;
    setAnalysis(null);
    setAnalysisError(null);
    setSaveError(null);
    setWarnings([]);
    setManualMode(false);

    // Asset değiştiyse (ilk render DEĞİL - önceki bir asset'ten farklıysa),
    // bu slot'un eski CTA seçimini yerel state'te de hemen geçersiz kıl.
    // Sunucu tarafı zaten aynı kuralı uygular (bkz. backend
    // invalidate_stale_cta_annotations) ama sihirbazın genel otomatik-kaydet
    // döngüsü PATCH yanıtını yerel state'e geri yazmadığı için (bkz.
    // TestWizard.tsx debounce efekti), bu bileşen kendi tutarlılığını kendi
    // sağlamalıdır.
    if (previousAssetId.current !== undefined && previousAssetId.current !== assetId) {
      onAnnotationChange(null);
    }
    previousAssetId.current = assetId;

    if (!assetId) {
      startedForAssetId.current = null;
      setPollState("idle");
      return;
    }

    startedForAssetId.current = assetId;
    setPollState("loading");
    createPageAnalysisForDesignAsset(assetId)
      .then((created) => {
        if (startedForAssetId.current !== assetId) return;
        setAnalysis(created);
        if (created.status === "succeeded") {
          setPollState("ready");
        } else if (created.status === "failed") {
          setPollState("failed");
        } else {
          schedulePoll(created.id, assetId);
        }
      })
      .catch((err) => {
        if (startedForAssetId.current !== assetId) return;
        setAnalysisError(err instanceof ApiError ? err.message : "Görsel analiz başlatılamadı.");
        setPollState("failed");
      });

    return () => {
      stopPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assetId]);

  useEffect(() => stopPolling, []);

  async function saveAnnotation(
    box: { x: number; y: number; w: number; h: number },
    selectionSource: "candidate_confirmation" | "manual_box",
    candidateIndex?: number,
  ) {
    if (!draftId || !assetId || isSaving) return;
    setIsSaving(true);
    setSaveError(null);
    try {
      const field = slot === "current" ? "current_cta_annotation" : "new_cta_annotation";
      const response = await patchWizardDraft(draftId, {
        [field]: {
          design_asset_id: assetId,
          box,
          selection_source: selectionSource,
          ...(candidateIndex !== undefined ? { source_candidate_index: candidateIndex } : {}),
        },
      } as never);
      onAnnotationChange((response.payload[field] as CtaAnnotation | null) ?? null);
      setWarnings(response.warnings ?? []);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "CTA seçimi kaydedilemedi.");
    } finally {
      setIsSaving(false);
    }
  }

  async function clearAnnotation() {
    if (!draftId || isSaving) return;
    setIsSaving(true);
    setSaveError(null);
    try {
      const field = slot === "current" ? "current_cta_annotation" : "new_cta_annotation";
      const response = await patchWizardDraft(draftId, { [field]: null } as never);
      onAnnotationChange(null);
      setWarnings(response.warnings ?? []);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Seçim temizlenemedi.");
    } finally {
      setIsSaving(false);
    }
  }

  if (!assetId) {
    return null;
  }

  const features =
    analysis?.features && "visual_cta_candidates" in analysis.features
      ? (analysis.features as {
          visual_cta_candidates: {
            box: { x: number; y: number; w: number; h: number };
            heuristic_score: number;
          }[];
          candidate_disclaimer: string;
          synthetic_attention_estimate: { disclaimer: string };
        })
      : null;

  return (
    <div className="cta-selector">
      <h4 className="cta-selector-title">{label} — CTA alanı</h4>

      {pollState === "loading" && (
        <p className="wizard-field-hint" role="status">
          Görsel analiz hazırlanıyor…
        </p>
      )}

      {pollState === "failed" && (
        <p className="auth-error" role="alert">
          {analysisError ?? "Görsel analiz başarısız oldu."}
        </p>
      )}

      {pollState === "timeout" && (
        <p className="auth-error" role="alert">
          Görsel analiz beklenenden uzun sürüyor. Lütfen daha sonra tekrar deneyin.
        </p>
      )}

      {pollState === "ready" && features && (
        <div className="cta-selector-body">
          <p className="wizard-field-hint">{features.candidate_disclaimer || CANDIDATE_DISCLAIMER_FALLBACK}</p>

          {features.visual_cta_candidates.length === 0 && (
            <p className="wizard-field-hint">Bu görsel için otomatik bir CTA adayı bulunamadı.</p>
          )}

          {features.visual_cta_candidates.length > 0 && !manualMode && (
            <ul className="cta-selector-candidate-list" aria-label={`${label} - CTA adayları`}>
              {features.visual_cta_candidates.map((candidate, index) => {
                const isSelected =
                  annotation?.selection_source === "candidate_confirmation" &&
                  annotation.source_candidate_index === index;
                return (
                  <li key={index}>
                    <button
                      type="button"
                      className={`cta-selector-candidate${
                        isSelected ? " cta-selector-candidate--selected" : ""
                      }`}
                      aria-pressed={isSelected}
                      disabled={isSaving}
                      onClick={() => void saveAnnotation(candidate.box, "candidate_confirmation", index)}
                    >
                      <span>CTA adayı {index + 1}</span>
                      <span className="cta-selector-candidate-score">
                        Sıralama skoru: {formatScore(candidate.heuristic_score)}
                      </span>
                      {isSelected && <span className="cta-selector-candidate-badge">Seçili</span>}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          <div className="cta-selector-manual-toggle">
            <label>
              <input
                type="checkbox"
                checked={manualMode}
                onChange={(event) => setManualMode(event.target.checked)}
              />
              <span>CTA alanını kendim seçmek istiyorum</span>
            </label>
          </div>

          {manualMode && (
            <div className="cta-selector-manual-box">
              <div className="cta-selector-manual-fields">
                <label>
                  x
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    value={manualBox.x}
                    onChange={(event) =>
                      setManualBox((prev) => ({ ...prev, x: Number(event.target.value) }))
                    }
                  />
                </label>
                <label>
                  y
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    value={manualBox.y}
                    onChange={(event) =>
                      setManualBox((prev) => ({ ...prev, y: Number(event.target.value) }))
                    }
                  />
                </label>
                <label>
                  genişlik
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    value={manualBox.w}
                    onChange={(event) =>
                      setManualBox((prev) => ({ ...prev, w: Number(event.target.value) }))
                    }
                  />
                </label>
                <label>
                  yükseklik
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    value={manualBox.h}
                    onChange={(event) =>
                      setManualBox((prev) => ({ ...prev, h: Number(event.target.value) }))
                    }
                  />
                </label>
              </div>
              <button
                type="button"
                className="btn-secondary"
                disabled={isSaving}
                onClick={() => void saveAnnotation(manualBox, "manual_box")}
              >
                {isSaving ? "Kaydediliyor…" : "Manuel alanı kaydet"}
              </button>
            </div>
          )}

          {annotation && (
            <button type="button" className="btn-secondary" disabled={isSaving} onClick={() => void clearAnnotation()}>
              Seçimi temizle
            </button>
          )}

          {warnings.includes("cta_annotation_covers_full_image") && (
            <p className="wizard-field-hint" role="alert">
              Seçtiğiniz alan görselin neredeyse tamamını kaplıyor - CTA alanını daha dar seçmeniz önerilir.
            </p>
          )}

          <p className="wizard-field-hint">
            {features.synthetic_attention_estimate.disclaimer}
          </p>

          {saveError && (
            <p className="auth-error" role="alert">
              {saveError}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
