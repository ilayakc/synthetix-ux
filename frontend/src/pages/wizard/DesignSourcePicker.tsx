import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  type DesignAssetResponse,
  deleteDesignAsset,
  getDesignAsset,
  getDesignAssetPreviewUrl,
  uploadDesignAsset,
} from "../../api/client";

const ACCEPTED_ACCEPT_ATTR = "image/png,image/jpeg,image/webp";

const CONTENT_TYPE_LABELS: Record<string, string> = {
  "image/png": "PNG",
  "image/jpeg": "JPEG",
  "image/webp": "WebP",
};

function formatBytes(byteSize: number): string {
  if (byteSize < 1024) return `${byteSize} B`;
  if (byteSize < 1024 * 1024) return `${(byteSize / 1024).toFixed(1)} KB`;
  return `${(byteSize / (1024 * 1024)).toFixed(1)} MB`;
}

function formatExpiresAt(expiresAt: string | null): string | null {
  if (!expiresAt) return null;
  try {
    return new Date(expiresAt).toLocaleString("tr-TR");
  } catch {
    return null;
  }
}

/** Bir üçüncü (isteğe bağlı) kaynak seçeneği - bkz. DesignBSourcePicker.
 * Bu, bileşenin tek `radiogroup`unu (a11y için önemli) bozmadan "AI ile
 * oluştur" gibi ek bir seçenek eklemeyi sağlar; jenerik bileşenin kendisi
 * bu seçeneğin ne anlama geldiğini bilmez, yalnızca radio + panel render eder. */
export interface DesignSourcePickerExtraOption {
  value: string;
  label: string;
  disabledReason?: string;
  renderPanel: () => ReactNode;
}

export interface DesignSourcePickerProps {
  label: string;
  sourceType: string | undefined;
  url: string | undefined;
  assetId: string | undefined;
  onSourceTypeChange: (sourceType: string) => void;
  onUrlChange: (url: string) => void;
  onAssetChange: (assetId: string | null) => void;
  urlLabel?: string;
  urlFieldId?: string;
  urlError?: string;
  assetError?: string;
  /** Ekran görüntüsü seçeneğinin devre dışı bırakılma nedeni (verilirse seçenek
   * gizlenmez, yalnızca açıklamayla birlikte kullanılamaz gösterilir). */
  screenshotDisabledReason?: string;
  extraOption?: DesignSourcePickerExtraOption;
}

export default function DesignSourcePicker({
  label,
  sourceType,
  url,
  assetId,
  onSourceTypeChange,
  onUrlChange,
  onAssetChange,
  urlLabel = "URL",
  urlFieldId = "design-source-url",
  urlError,
  assetError,
  screenshotDisabledReason,
  extraOption,
}: DesignSourcePickerProps) {
  const effectiveSourceType: string = sourceType ?? "url";
  const screenshotDisabled = Boolean(screenshotDisabledReason);
  const extraOptionDisabled = Boolean(extraOption?.disabledReason);

  const [assetMeta, setAssetMeta] = useState<DesignAssetResponse | null>(null);
  const [assetLoadError, setAssetLoadError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [isDragActive, setIsDragActive] = useState(false);

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let cancelled = false;

    if (!assetId) {
      setAssetMeta(null);
      setAssetLoadError(null);
      return;
    }

    setAssetLoadError(null);
    getDesignAsset(assetId)
      .then((meta) => {
        if (!cancelled) {
          setAssetMeta(meta);
          setAssetLoadError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setAssetMeta(null);
          setAssetLoadError(
            err instanceof ApiError && err.status === 404
              ? "Bu görsel artık kullanılamıyor (silinmiş veya saklama süresi dolmuş olabilir). Lütfen yeni bir görsel yükleyin."
              : "Görsel bilgisi yüklenemedi.",
          );
        }
      });

    return () => {
      cancelled = true;
    };
  }, [assetId]);

  async function handleFileSelected(file: File) {
    if (isUploading) return;
    setUploadError(null);
    setIsUploading(true);
    setUploadProgress(0);
    try {
      const result = await uploadDesignAsset(file, {
        onProgress: (loaded, total) => {
          if (total > 0) setUploadProgress(Math.round((loaded / total) * 100));
        },
      });
      setAssetMeta(result);
      setAssetLoadError(null);
      setDeleteError(null);
      onAssetChange(result.id);
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : "Yükleme başarısız oldu.");
    } finally {
      setIsUploading(false);
    }
  }

  function handleFileInputChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void handleFileSelected(file);
  }

  function handleDragEnter(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (!isUploading) setIsDragActive(true);
  }

  function handleDragOver(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
  }

  function handleDragLeave(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragActive(false);
  }

  function handleDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragActive(false);
    if (isUploading) return;
    const file = event.dataTransfer.files?.[0];
    if (file) void handleFileSelected(file);
  }

  async function handleRemove() {
    if (!assetId || isDeleting) return;
    setIsDeleting(true);
    setDeleteError(null);
    try {
      await deleteDesignAsset(assetId);
      setAssetMeta(null);
      setAssetLoadError(null);
      onAssetChange(null);
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : "Görsel kaldırılamadı.");
    } finally {
      setIsDeleting(false);
    }
  }

  // `assetMeta` yukleme basarili olur olmaz senkron olarak ayarlanir; ana
  // bilesenin (parent) `assetId` prop'unu guncellemesini BEKLEMEDEN onizleme/
  // metadata hemen gosterilebilsin diye render kontrolu prop yerine bu yerel
  // state'e dayanir (parent zaten `onAssetChange` ile bir sonraki render'da
  // ayni degeri prop olarak geri gonderir - bkz. Step2Urls/TestWizard).
  const hasUsableAsset = assetMeta !== null;
  const expiresAtLabel = assetMeta ? formatExpiresAt(assetMeta.expires_at) : null;

  return (
    <div className="design-source-picker">
      <div
        className="wizard-radio-group"
        role="radiogroup"
        aria-label={`${label} - kaynak türü`}
      >
        <label className="wizard-radio-option">
          <input
            type="radio"
            name={`${urlFieldId}-source-type`}
            value="url"
            checked={effectiveSourceType === "url"}
            onChange={() => onSourceTypeChange("url")}
          />
          <span>URL</span>
        </label>
        <label
          className={`wizard-radio-option${screenshotDisabled ? " wizard-radio-option--disabled" : ""}`}
        >
          <input
            type="radio"
            name={`${urlFieldId}-source-type`}
            value="screenshot"
            checked={effectiveSourceType === "screenshot"}
            disabled={screenshotDisabled}
            onChange={() => onSourceTypeChange("screenshot")}
          />
          <span>
            Ekran görüntüsü
            {screenshotDisabled && screenshotDisabledReason && (
              <span className="wizard-field-hint" style={{ display: "block" }}>
                {screenshotDisabledReason}
              </span>
            )}
          </span>
        </label>
        {extraOption && (
          <label
            className={`wizard-radio-option${extraOptionDisabled ? " wizard-radio-option--disabled" : ""}`}
          >
            <input
              type="radio"
              name={`${urlFieldId}-source-type`}
              value={extraOption.value}
              checked={effectiveSourceType === extraOption.value}
              disabled={extraOptionDisabled}
              onChange={() => onSourceTypeChange(extraOption.value)}
            />
            <span>
              {extraOption.label}
              {extraOptionDisabled && extraOption.disabledReason && (
                <span className="wizard-field-hint" style={{ display: "block" }}>
                  {extraOption.disabledReason}
                </span>
              )}
            </span>
          </label>
        )}
      </div>

      {extraOption && effectiveSourceType === extraOption.value && !extraOptionDisabled && (
        <div className="wizard-field">{extraOption.renderPanel()}</div>
      )}

      {effectiveSourceType === "url" && (
        <div className="wizard-field">
          <label htmlFor={urlFieldId}>{urlLabel}</label>
          <input
            id={urlFieldId}
            type="url"
            value={url ?? ""}
            onChange={(event) => onUrlChange(event.target.value)}
            placeholder="https://example.com"
          />
          <p className="wizard-field-hint">
            URL yalnızca biçim olarak doğrulanır; bu aşamada ziyaret edilmez veya taranmaz.
          </p>
          {urlError && <p className="auth-error">{urlError}</p>}
        </div>
      )}

      {effectiveSourceType === "screenshot" && !screenshotDisabled && (
        <div className="wizard-field">
          {assetLoadError && (
            <p className="auth-error" role="alert">
              {assetLoadError}
            </p>
          )}

          {hasUsableAsset && assetMeta && (
            <div className="design-source-preview">
              <img
                src={getDesignAssetPreviewUrl(assetMeta.id)}
                alt={`${label} - yüklenen tasarım ekran görüntüsü önizlemesi`}
                className="design-source-preview-image"
              />
              <ul className="design-source-preview-meta">
                <li>Format: {CONTENT_TYPE_LABELS[assetMeta.content_type] ?? assetMeta.content_type}</li>
                <li>
                  Boyut: {assetMeta.width} × {assetMeta.height} px
                </li>
                <li>Dosya boyutu: {formatBytes(assetMeta.byte_size)}</li>
                {expiresAtLabel && <li>Saklama süresi dolacak: {expiresAtLabel}</li>}
              </ul>
              <div className="design-source-preview-actions">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={isUploading || isDeleting}
                >
                  Görseli değiştir
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={handleRemove}
                  disabled={isUploading || isDeleting}
                >
                  {isDeleting ? "Kaldırılıyor…" : "Görseli kaldır"}
                </button>
              </div>
              {deleteError && (
                <p className="auth-error" role="alert">
                  {deleteError}
                </p>
              )}
            </div>
          )}

          <div
            className={`design-source-dropzone${isDragActive ? " design-source-dropzone--active" : ""}${
              isUploading ? " design-source-dropzone--uploading" : ""
            }`}
            onDragEnter={handleDragEnter}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <p>Tasarım ekran görüntüsünü buraya sürükleyip bırakın</p>
            <button
              type="button"
              className="auth-submit"
              onClick={() => fileInputRef.current?.click()}
              disabled={isUploading}
            >
              {isUploading ? "Yükleniyor…" : hasUsableAsset ? "Görseli değiştir" : "Dosya seç"}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_ACCEPT_ATTR}
              className="visually-hidden"
              aria-label={`${label} - tasarım ekran görüntüsü dosyası seç (PNG, JPEG veya WebP)`}
              onChange={handleFileInputChange}
              disabled={isUploading}
            />
            {isUploading && (
              <div
                className="design-source-progress"
                role="progressbar"
                aria-valuenow={uploadProgress}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label="Yükleme ilerlemesi"
              >
                <div className="design-source-progress-bar" style={{ width: `${uploadProgress}%` }} />
                <span>Yükleniyor… %{uploadProgress}</span>
              </div>
            )}
            {uploadError && (
              <p className="auth-error" role="alert">
                {uploadError}
              </p>
            )}
          </div>
          <p className="wizard-field-hint">
            Ekran görüntüsü henüz görsel analiz motoruna bağlı değildir; bu aşamada yalnızca
            kaydedilir.
          </p>
          {assetError && <p className="auth-error">{assetError}</p>}
        </div>
      )}
    </div>
  );
}

