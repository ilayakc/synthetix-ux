import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  type DesignGenerationAvailability,
  type DesignGenerationResponse,
  cancelDesignGeneration,
  createDesignGeneration,
  getDesignAssetPreviewUrl,
  getDesignGeneration,
  getDesignGenerationAvailability,
} from "../../api/client";

const POLL_INTERVAL_MS = 2000;

export interface AiDesignGeneratorProps {
  /** Tasarım A'nın (referans) DesignAsset kimliği - yalnızca Tasarım A bir
   * ekran görüntüsüyse mevcuttur (bkz. proje talimatı "Referans görsel"). */
  referenceAssetId: string | undefined;
  /** Tasarım A henüz bir ekran görüntüsü değilse (ör. URL kaynağı), AI
   * seçeneği bu nedenle açıklamalı biçimde engellenir. */
  referenceUnavailableReason?: string;
  /** Kullanıcı sonucu AÇIKÇA kabul ettiğinde çağrılır; bundan önce
   * `new_design_asset_id` HİÇBİR ŞEKİLDE değiştirilmemelidir. */
  onAccept: (jobId: string, assetId: string) => void;
  onUseUpload: () => void;
  onUseUrl: () => void;
}

function statusMessage(status: DesignGenerationResponse["status"]): string {
  switch (status) {
    case "queued":
      return "Talebiniz sıraya alındı…";
    case "running":
      return "AI tasarım taslağınızı oluşturuyor…";
    case "succeeded":
      return "Tasarım taslağı hazır.";
    case "failed":
      return "Üretim başarısız oldu.";
    case "cancelled":
      return "Üretim iptal edildi.";
    default:
      return "";
  }
}

export default function AiDesignGenerator({
  referenceAssetId,
  referenceUnavailableReason,
  onAccept,
  onUseUpload,
  onUseUrl,
}: AiDesignGeneratorProps) {
  const [availability, setAvailability] = useState<DesignGenerationAvailability | null>(null);
  const [availabilityError, setAvailabilityError] = useState<string | null>(null);

  const [prompt, setPrompt] = useState("");
  const [ctaColor, setCtaColor] = useState("");
  const [headingChange, setHeadingChange] = useState("");
  const [preserveAreas, setPreserveAreas] = useState("");
  const [consentChecked, setConsentChecked] = useState(false);

  const [job, setJob] = useState<DesignGenerationResponse | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [acceptedJobId, setAcceptedJobId] = useState<string | null>(null);

  const pollTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    // Referans görsel (Tasarım A ekran görüntüsü) yoksa bu panel zaten
    // erken dönüş yapıp yalnızca `referenceUnavailableReason`ı gösterecek
    // (bkz. aşağıdaki render mantığı); bu durumda sağlayıcı durumunu
    // sormanın hiçbir faydası yoktur - gereksiz bir ağ isteği atlanır.
    if (referenceUnavailableReason) return;

    let cancelled = false;
    getDesignGenerationAvailability()
      .then((result) => {
        if (!cancelled) setAvailability(result);
      })
      .catch((err) => {
        if (!cancelled) {
          setAvailabilityError(
            err instanceof ApiError ? err.message : "Sağlayıcı durumu kontrol edilemedi.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [referenceUnavailableReason]);

  useEffect(() => {
    return () => {
      if (pollTimeout.current) clearTimeout(pollTimeout.current);
    };
  }, []);

  function schedulePoll(jobId: string) {
    if (pollTimeout.current) clearTimeout(pollTimeout.current);
    pollTimeout.current = setTimeout(async () => {
      try {
        const updated = await getDesignGeneration(jobId);
        setJob(updated);
        if (updated.status === "queued" || updated.status === "running") {
          schedulePoll(jobId);
        }
      } catch (err) {
        setSubmitError(err instanceof ApiError ? err.message : "Durum kontrol edilemedi.");
      }
    }, POLL_INTERVAL_MS);
  }

  function buildFinalPrompt(): string {
    const structured: string[] = [];
    if (ctaColor.trim()) structured.push(`Ana CTA rengi: ${ctaColor.trim()}`);
    if (headingChange.trim()) structured.push(`Başlık değişikliği: ${headingChange.trim()}`);
    if (preserveAreas.trim()) structured.push(`Korunması gereken alanlar: ${preserveAreas.trim()}`);
    const structuredText = structured.length > 0 ? `\n\n${structured.join("\n")}` : "";
    return `${prompt.trim()}${structuredText}`;
  }

  async function handleSubmit() {
    if (!referenceAssetId || isSubmitting) return;
    setSubmitError(null);
    setIsSubmitting(true);
    try {
      const created = await createDesignGeneration({
        sourceAssetId: referenceAssetId,
        prompt: buildFinalPrompt(),
        authorizationConfirmed: consentChecked,
      });
      setJob(created);
      schedulePoll(created.id);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : "Üretim isteği gönderilemedi.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleCancel() {
    if (!job) return;
    try {
      const cancelled = await cancelDesignGeneration(job.id);
      setJob(cancelled);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : "İptal edilemedi.");
    }
  }

  function handleRegenerate() {
    setJob(null);
    setSubmitError(null);
  }

  function handleEditRequest() {
    setJob(null);
    setSubmitError(null);
  }

  function handleReject() {
    setJob(null);
    setSubmitError(null);
  }

  function handleUse() {
    if (!job || job.status !== "succeeded" || !job.result_asset) return;
    setAcceptedJobId(job.id);
    onAccept(job.id, job.result_asset.id);
  }

  if (referenceUnavailableReason) {
    return (
      <p className="wizard-field-hint" role="status">
        {referenceUnavailableReason}
      </p>
    );
  }

  if (availabilityError) {
    return <p className="auth-error">{availabilityError}</p>;
  }

  if (availability === null) {
    return <p className="page-placeholder">Sağlayıcı durumu kontrol ediliyor…</p>;
  }

  if (!availability.available) {
    return (
      <p className="wizard-field-hint" role="status">
        {availability.disabled_reason ??
          "AI görsel üretim sağlayıcısı henüz yapılandırılmadı. Tasarım B için URL veya ekran görüntüsü kullanabilirsiniz."}
      </p>
    );
  }

  const isPolling = job?.status === "queued" || job?.status === "running";
  const isAccepted = acceptedJobId !== null && acceptedJobId === job?.id;
  const promptTooLong = prompt.length > availability.max_prompt_length;

  if (job?.status === "succeeded" && job.result_asset) {
    const asset = job.result_asset;
    return (
      <div className="ai-design-generator ai-design-generator--result">
        <p className="auth-notice" role="status">
          AI tarafından üretilmiş tasarım taslağı
        </p>
        <div className="ai-design-generator-comparison">
          <div>
            <p className="wizard-field-hint">Orijinal (Tasarım A)</p>
            {referenceAssetId && (
              <img
                src={getDesignAssetPreviewUrl(referenceAssetId)}
                alt="Tasarım A önizlemesi"
                className="design-source-preview-image"
              />
            )}
          </div>
          <div>
            <p className="wizard-field-hint">AI tasarım taslağı</p>
            <img
              src={getDesignAssetPreviewUrl(asset.id)}
              alt="AI ile üretilen tasarım taslağı önizlemesi"
              className="design-source-preview-image"
            />
          </div>
        </div>
        <ul className="wizard-summary-list">
          <li>
            <span>Talep özeti</span>
            <span>{job.error_message ? "—" : buildFinalPrompt() || "—"}</span>
          </li>
          <li>
            <span>Sağlayıcı / model</span>
            <span>
              {job.provider} / {job.model_name}
            </span>
          </li>
          <li>
            <span>Oluşturulma zamanı</span>
            <span>{job.finished_at ? new Date(job.finished_at).toLocaleString("tr-TR") : "—"}</span>
          </li>
        </ul>
        <p className="wizard-field-hint">
          Bu görsel çalışan bir web sitesi değildir; HTML/CSS üretilmemiştir, etkileşimler
          çalışmaz. Metinler, logolar ve görsel ayrıntılar hatalı değişmiş olabilir. Bu bir
          tasarım fikri/prototip taslağıdır - gerçek kullanıcı testi veya gerçek göz takibi/tıklama
          verisi değildir. Kullanmadan önce sonucu doğrulayın.
        </p>
        <div className="wizard-launch-group">
          <button type="button" className="auth-submit" onClick={handleUse} disabled={isAccepted}>
            {isAccepted ? "Tasarım B olarak kullanılıyor" : "Tasarım B olarak kullan"}
          </button>
          <button type="button" className="btn-secondary" onClick={handleRegenerate}>
            Yeniden üret
          </button>
          <button type="button" className="btn-secondary" onClick={handleEditRequest}>
            Talebi düzenle
          </button>
          <button type="button" className="btn-secondary" onClick={handleReject}>
            Reddet
          </button>
          <button type="button" className="btn-secondary" onClick={onUseUpload}>
            Kendi ekran görüntümü yükle
          </button>
          <button type="button" className="btn-secondary" onClick={onUseUrl}>
            URL kullan
          </button>
        </div>
      </div>
    );
  }

  if (job && (job.status === "failed" || job.status === "cancelled")) {
    return (
      <div className="ai-design-generator">
        <p className="auth-error" role="alert">
          {job.status === "failed"
            ? job.error_message ?? "Üretim başarısız oldu."
            : "Üretim iptal edildi."}
        </p>
        <div className="wizard-launch-group">
          <button type="button" className="auth-submit" onClick={handleRegenerate}>
            Yeniden dene
          </button>
          <button type="button" className="btn-secondary" onClick={onUseUpload}>
            Kendi ekran görüntümü yükle
          </button>
          <button type="button" className="btn-secondary" onClick={onUseUrl}>
            URL kullan
          </button>
        </div>
      </div>
    );
  }

  if (job && isPolling) {
    return (
      <div className="ai-design-generator" role="status">
        <p>{statusMessage(job.status)}</p>
        <button type="button" className="btn-secondary" onClick={handleCancel} disabled={job.status !== "queued"}>
          İptal et
        </button>
      </div>
    );
  }

  return (
    <div className="ai-design-generator">
      <div className="wizard-field">
        <label htmlFor="ai-generator-prompt">Değişiklik talebi</label>
        <textarea
          id="ai-generator-prompt"
          rows={4}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="Örn: Üst bölümdeki ana CTA'yı turuncu yap, başlığı kısalt, kartların aralığını artır ve sağ üstteki ikincil butonu kaldır. Diğer alanları mümkün olduğunca koru."
        />
        <p className="wizard-field-hint">
          Değişmesini istediğiniz alanları ve korunması gereken bölümleri açıkça belirtin.
        </p>
        <p className="wizard-field-hint" aria-live="polite">
          {prompt.length} / {availability.max_prompt_length} karakter
        </p>
        {promptTooLong && (
          <p className="auth-error">
            Talep en fazla {availability.max_prompt_length} karakter olabilir.
          </p>
        )}
      </div>

      <div className="wizard-field">
        <label htmlFor="ai-generator-cta-color">Ana CTA rengi (isteğe bağlı)</label>
        <input
          id="ai-generator-cta-color"
          type="text"
          value={ctaColor}
          onChange={(event) => setCtaColor(event.target.value)}
          placeholder="Örn: turuncu"
        />
      </div>
      <div className="wizard-field">
        <label htmlFor="ai-generator-heading">Başlık değişikliği (isteğe bağlı)</label>
        <input
          id="ai-generator-heading"
          type="text"
          value={headingChange}
          onChange={(event) => setHeadingChange(event.target.value)}
          placeholder="Örn: daha kısa bir başlık"
        />
      </div>
      <div className="wizard-field">
        <label htmlFor="ai-generator-preserve">Korunması gereken alanlar (isteğe bağlı)</label>
        <input
          id="ai-generator-preserve"
          type="text"
          value={preserveAreas}
          onChange={(event) => setPreserveAreas(event.target.value)}
          placeholder="Örn: alt bilgi (footer), logo"
        />
      </div>

      <p className="auth-notice" role="note">
        Bu görsel, belirttiğiniz amaçla ({availability.provider_name ?? "yapılandırılmış sağlayıcı"})
        uzak bir AI sağlayıcısına gönderilecektir; sağlayıcı tasarımı yeniden yorumlayabilir. Hassas
        müşteri veya kişisel veri içeren ekran görüntüleri yüklemeyin. Sonuç çalışan bir HTML/CSS
        değil, yalnızca görsel bir taslaktır ve kullanmadan önce doğrulanmalıdır.
      </p>

      <label className="wizard-radio-option">
        <input
          type="checkbox"
          checked={consentChecked}
          onChange={(event) => setConsentChecked(event.target.checked)}
        />
        <span>
          Bu tasarım görselinin, yukarıda açıklanan amaçla bir uzak AI sağlayıcısına
          aktarılmasını onaylıyorum.
        </span>
      </label>

      {submitError && (
        <p className="auth-error" role="alert">
          {submitError}
        </p>
      )}

      <button
        type="button"
        className="auth-submit"
        onClick={handleSubmit}
        disabled={
          isSubmitting ||
          !referenceAssetId ||
          !prompt.trim() ||
          promptTooLong ||
          !consentChecked
        }
      >
        {isSubmitting ? "Gönderiliyor…" : "Varyant oluştur"}
      </button>
    </div>
  );
}
