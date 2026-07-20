import { type FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError,
  type PersonaDimensionInfo,
  type PersonaDistribution,
  type PersonaPresetResponse,
  archivePersonaPreset,
  copyPersonaPreset,
  createPersonaPreset,
  listPersonaDimensions,
  listPersonaPresets,
} from "../api/client";
import PersonaDistributionEditor from "../components/PersonaDistributionEditor";

function PresetBadge({ preset }: { preset: PersonaPresetResponse }) {
  if (preset.is_builtin) {
    return <span className="status-badge status-badge--draft">Hazır</span>;
  }
  return (
    <span className={`status-badge status-badge--${preset.status}`}>
      {preset.status === "archived" ? "Arşivlendi" : "Özel"}
    </span>
  );
}

interface PresetDetailModalProps {
  preset: PersonaPresetResponse;
  dimensions: PersonaDimensionInfo[];
  onClose: () => void;
}

function PresetDetailModal({ preset, dimensions, onClose }: PresetDetailModalProps) {
  const dimensionLabel = (key: string) =>
    dimensions.find((dimension) => dimension.key === key)?.label ?? key;
  const distributionEntries = Object.entries(preset.distribution);

  return (
    <div className="modal-overlay" role="presentation" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="preset-detail-heading"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="preset-detail-heading">{preset.name}</h2>
        {preset.is_builtin && (
          <p className="wizard-field-hint">
            Bu hazır preset doğrudan değiştirilemez; düzenlemek için önce kopyalayın.
          </p>
        )}
        {preset.description && <p className="page-placeholder">{preset.description}</p>}

        {distributionEntries.length === 0 ? (
          <p className="wizard-field-hint">Bu preset için tanımlı bir dağılım yok.</p>
        ) : (
          distributionEntries.map(([dimensionKey, buckets]) => (
            <div className="wizard-field" key={dimensionKey}>
              <strong>{dimensionLabel(dimensionKey)}</strong>
              <ul className="module-card__outputs">
                {buckets.map((bucket) => (
                  <li key={bucket.key}>
                    {bucket.label}: %{bucket.weight}
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}

        <div className="modal__actions">
          <button type="button" className="auth-submit" onClick={onClose}>
            Kapat
          </button>
        </div>
      </div>
    </div>
  );
}

interface CopyPresetModalProps {
  source: PersonaPresetResponse;
  onClose: () => void;
  onCopied: (preset: PersonaPresetResponse) => void;
}

function CopyPresetModal({ source, onClose, onCopied }: CopyPresetModalProps) {
  const [name, setName] = useState(`${source.name} (kopya)`);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      const copy = await copyPersonaPreset(source.id, { name: name.trim() });
      onCopied(copy);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Preset kopyalanamadı.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="copy-preset-heading"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="copy-preset-heading">"{source.name}" presetini kopyala</h2>
        <form className="auth-form" onSubmit={handleSubmit}>
          <label htmlFor="copy-preset-name">Yeni preset adı</label>
          <input
            id="copy-preset-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            maxLength={255}
          />
          {error && <p className="auth-error">{error}</p>}
          <div className="modal__actions">
            <button type="button" className="auth-google-button" onClick={onClose}>
              Vazgeç
            </button>
            <button type="submit" className="auth-submit" disabled={isSubmitting || !name.trim()}>
              {isSubmitting ? "Kopyalanıyor…" : "Kopyala"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

interface CreatePresetModalProps {
  dimensions: PersonaDimensionInfo[];
  onClose: () => void;
  onCreated: (preset: PersonaPresetResponse) => void;
}

function CreatePresetModal({ dimensions, onClose, onCreated }: CreatePresetModalProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [distribution, setDistribution] = useState<PersonaDistribution>({});
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      const preset = await createPersonaPreset({
        name: name.trim(),
        description: description.trim() ? description.trim() : null,
        distribution,
      });
      onCreated(preset);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Preset oluşturulamadı.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-preset-heading"
        style={{ maxWidth: 560 }}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="create-preset-heading">Yeni şirkete özel preset</h2>
        <form className="auth-form" onSubmit={handleSubmit}>
          <label htmlFor="preset-name">Preset adı</label>
          <input
            id="preset-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            maxLength={255}
          />

          <label htmlFor="preset-description">Kısa açıklama</label>
          <input
            id="preset-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            maxLength={2000}
          />

          <PersonaDistributionEditor
            dimensions={dimensions}
            distribution={distribution}
            onChange={setDistribution}
          />

          {error && <p className="auth-error">{error}</p>}

          <div className="modal__actions">
            <button type="button" className="auth-google-button" onClick={onClose}>
              Vazgeç
            </button>
            <button type="submit" className="auth-submit" disabled={isSubmitting || !name.trim()}>
              {isSubmitting ? "Oluşturuluyor…" : "Oluştur"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function PersonaPresets() {
  const navigate = useNavigate();
  const [dimensions, setDimensions] = useState<PersonaDimensionInfo[]>([]);
  const [presets, setPresets] = useState<PersonaPresetResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [copySource, setCopySource] = useState<PersonaPresetResponse | null>(null);
  const [detailSource, setDetailSource] = useState<PersonaPresetResponse | null>(null);

  const load = () => {
    setError(null);
    Promise.all([listPersonaDimensions(), listPersonaPresets(includeArchived)])
      .then(([dims, presetList]) => {
        setDimensions(dims);
        setPresets(presetList);
      })
      .catch(() => setError("Persona presetleri yüklenemedi."));
  };

  useEffect(load, [includeArchived]);

  const handleArchive = async (preset: PersonaPresetResponse) => {
    try {
      const archived = await archivePersonaPreset(preset.id);
      setPresets((current) => (current ?? []).map((p) => (p.id === archived.id ? archived : p)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Preset arşivlenemedi.");
    }
  };

  return (
    <section aria-labelledby="persona-presets-heading">
      <h1 id="persona-presets-heading" className="page-heading">
        Persona Presetleri
      </h1>
      <p className="page-placeholder">
        Sentetik test personalarının yaş, bölge, cihaz sınıfı, dijital yatkınlık ve erişilebilirlik
        ihtiyacı dağılımlarını buradan tanımlayın. Hazır presetler doğrudan değiştirilemez; önce
        kopyalayarak şirketinize özel, düzenlenebilir bir sürüm oluşturabilirsiniz.
      </p>

      <div className="wizard-field" style={{ maxWidth: 320 }}>
        <label htmlFor="include-archived-toggle">
          <input
            id="include-archived-toggle"
            type="checkbox"
            checked={includeArchived}
            onChange={(event) => setIncludeArchived(event.target.checked)}
          />{" "}
          Arşivlenenleri de göster
        </label>
      </div>

      {error && <p className="auth-error">{error}</p>}

      {presets && (
        <div className="persona-preset-list">
          {presets.map((preset) => (
            <div className="persona-preset-row" key={preset.id}>
              <div className="persona-preset-row__meta">
                <strong>
                  {preset.name} <PresetBadge preset={preset} />
                </strong>
                {preset.description && (
                  <span className="wizard-field-hint">{preset.description}</span>
                )}
                {preset.is_builtin && (
                  <span className="wizard-field-hint">
                    Hazır preset; düzenlemek için önce kopyalayın.
                  </span>
                )}
              </div>
              <div className="persona-preset-row__actions">
                <button
                  type="button"
                  className="auth-google-button"
                  onClick={() => setDetailSource(preset)}
                >
                  Detayları gör
                </button>
                <button
                  type="button"
                  className="auth-google-button"
                  onClick={() =>
                    navigate(`/tests/new?persona_preset=${encodeURIComponent(preset.id)}`)
                  }
                >
                  Bu presetle test oluştur
                </button>
                <button
                  type="button"
                  className="auth-google-button"
                  onClick={() => setCopySource(preset)}
                >
                  Kopyala
                </button>
                {!preset.is_builtin && preset.status === "active" && (
                  <button
                    type="button"
                    className="auth-google-button"
                    onClick={() => handleArchive(preset)}
                  >
                    Arşivle
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <button type="button" className="auth-submit" onClick={() => setIsCreateOpen(true)}>
        + Yeni özel preset
      </button>

      {isCreateOpen && (
        <CreatePresetModal
          dimensions={dimensions}
          onClose={() => setIsCreateOpen(false)}
          onCreated={(preset) => {
            setIsCreateOpen(false);
            setPresets((current) => [preset, ...(current ?? [])]);
          }}
        />
      )}

      {copySource && (
        <CopyPresetModal
          source={copySource}
          onClose={() => setCopySource(null)}
          onCopied={(preset) => {
            setCopySource(null);
            setPresets((current) => [preset, ...(current ?? [])]);
          }}
        />
      )}

      {detailSource && (
        <PresetDetailModal
          preset={detailSource}
          dimensions={dimensions}
          onClose={() => setDetailSource(null)}
        />
      )}
    </section>
  );
}
