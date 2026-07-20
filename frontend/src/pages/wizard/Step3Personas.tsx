import { useEffect, useState } from "react";
import {
  ApiError,
  type PersonaDimensionInfo,
  type PersonaPresetResponse,
  type PersonaSamplePreviewResponse,
  listPersonaDimensions,
  listPersonaPresets,
  previewPersonaSample,
} from "../../api/client";
import PersonaDistributionEditor from "../../components/PersonaDistributionEditor";
import type { StepProps } from "./types";

const MIN_PERSONA_COUNT = 100;
const MAX_PERSONA_COUNT = 50_000;
const FREE_PERSONA_LIMIT = 1000;

type PersonaMode = "none" | "preset" | "custom";

function currentMode(payload: StepProps["payload"]): PersonaMode {
  if (payload.persona_preset_id) return "preset";
  if (payload.persona_distribution) return "custom";
  return "none";
}

export default function Step3Personas({ payload, fieldErrors, onChange }: StepProps) {
  const personaCount = payload.persona_count ?? MIN_PERSONA_COUNT;
  const mode = currentMode(payload);

  const [dimensions, setDimensions] = useState<PersonaDimensionInfo[]>([]);
  const [presets, setPresets] = useState<PersonaPresetResponse[] | null>(null);
  const [presetsError, setPresetsError] = useState<string | null>(null);

  const [preview, setPreview] = useState<PersonaSamplePreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([listPersonaDimensions(), listPersonaPresets()])
      .then(([dims, presetList]) => {
        setDimensions(dims);
        setPresets(presetList);
      })
      .catch(() => setPresetsError("Persona presetleri yüklenemedi."));
  }, []);

  useEffect(() => {
    if (
      !personaCount ||
      personaCount < MIN_PERSONA_COUNT ||
      personaCount > MAX_PERSONA_COUNT ||
      mode === "none"
    ) {
      setPreview(null);
      return;
    }

    let cancelled = false;
    setPreviewLoading(true);
    setPreviewError(null);

    previewPersonaSample({
      persona_count: personaCount,
      preset_id: mode === "preset" ? payload.persona_preset_id : undefined,
      distribution: mode === "custom" ? payload.persona_distribution : undefined,
    })
      .then((result) => {
        if (!cancelled) setPreview(result);
      })
      .catch((err) => {
        if (!cancelled) {
          setPreview(null);
          setPreviewError(err instanceof ApiError ? err.message : "Dağılım özeti hesaplanamadı.");
        }
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, payload.persona_preset_id, payload.persona_distribution, personaCount]);

  const setMode = (nextMode: PersonaMode) => {
    if (nextMode === "preset") {
      onChange("persona_distribution", undefined);
      if (!payload.persona_preset_id && presets && presets.length > 0) {
        onChange("persona_preset_id", presets[0].id);
      }
    } else if (nextMode === "custom") {
      onChange("persona_preset_id", undefined);
      if (!payload.persona_distribution) {
        onChange("persona_distribution", {});
      }
    } else {
      onChange("persona_preset_id", undefined);
      onChange("persona_distribution", undefined);
    }
  };

  return (
    <div>
      <div className="wizard-field">
        <label htmlFor="wizard-persona-count">
          Persona sayısı ({MIN_PERSONA_COUNT.toLocaleString("tr-TR")}–
          {MAX_PERSONA_COUNT.toLocaleString("tr-TR")})
        </label>
        <input
          id="wizard-persona-count"
          type="number"
          min={MIN_PERSONA_COUNT}
          max={MAX_PERSONA_COUNT}
          value={personaCount}
          onChange={(event) => {
            const value = Number(event.target.value);
            onChange("persona_count", Number.isFinite(value) ? value : undefined);
          }}
        />
        <p className="wizard-field-hint">
          {personaCount <= FREE_PERSONA_LIMIT
            ? `${FREE_PERSONA_LIMIT.toLocaleString("tr-TR")} personaya kadar ücretsiz temel UX testi hakkınız uygulanabilir.`
            : `${FREE_PERSONA_LIMIT.toLocaleString("tr-TR")} persona sınırını aştığınız için bu test Chip gerektirir.`}
        </p>
        {fieldErrors.persona_count && <p className="auth-error">{fieldErrors.persona_count}</p>}
      </div>

      <div className="wizard-field">
        <label htmlFor="wizard-target-audience">Hedef kitle</label>
        <textarea
          id="wizard-target-audience"
          rows={3}
          value={payload.target_audience ?? ""}
          onChange={(event) => onChange("target_audience", event.target.value)}
          placeholder="Örn. Yeni B2B müşteri adayları, 25-45 yaş arası"
        />
        {fieldErrors.target_audience && <p className="auth-error">{fieldErrors.target_audience}</p>}
      </div>

      <fieldset className="wizard-field" aria-describedby="persona-mode-hint">
        <legend>Persona dağılımı (isteğe bağlı)</legend>
        <p id="persona-mode-hint" className="wizard-field-hint">
          Hazır bir preset seçebilir, kendi dağılımınızı tanımlayabilir ya da bu adımı boş
          bırakabilirsiniz. Boş bırakılırsa örnekleme yalnızca hedef kitle açıklamanıza göre
          yapılır.
        </p>
        <div className="wizard-radio-group">
          <label className="wizard-radio-option">
            <input
              type="radio"
              name="persona-mode"
              checked={mode === "none"}
              onChange={() => setMode("none")}
            />
            Belirtme
          </label>
          <label className="wizard-radio-option">
            <input
              type="radio"
              name="persona-mode"
              checked={mode === "preset"}
              onChange={() => setMode("preset")}
            />
            Hazır / şirkete özel preset kullan
          </label>
          <label className="wizard-radio-option">
            <input
              type="radio"
              name="persona-mode"
              checked={mode === "custom"}
              onChange={() => setMode("custom")}
            />
            Özel dağılım tanımla
          </label>
        </div>
      </fieldset>

      {presetsError && <p className="auth-error">{presetsError}</p>}

      {mode === "preset" && (
        <div className="wizard-field">
          <label htmlFor="wizard-persona-preset">Preset</label>
          <select
            id="wizard-persona-preset"
            value={payload.persona_preset_id ?? ""}
            onChange={(event) => onChange("persona_preset_id", event.target.value || undefined)}
          >
            <option value="" disabled>
              Bir preset seçin
            </option>
            {(presets ?? []).map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.name}
                {preset.is_builtin ? " (Hazır)" : ""}
              </option>
            ))}
          </select>
        </div>
      )}

      {mode === "custom" && (
        <PersonaDistributionEditor
          dimensions={dimensions}
          distribution={payload.persona_distribution ?? {}}
          onChange={(next) => onChange("persona_distribution", next)}
        />
      )}

      {mode !== "none" && (
        <div className="wizard-field" aria-live="polite">
          {previewLoading && <p className="wizard-field-hint">Dağılım özeti hesaplanıyor…</p>}
          {previewError && <p className="auth-error">{previewError}</p>}
          {preview && (
            <>
              <p className="wizard-field-hint">
                {preview.segments.length} segment · {preview.total_count.toLocaleString("tr-TR")}{" "}
                persona · üretici sürümü: {preview.generator_version}
              </p>
              <table className="persona-segment-table">
                <caption className="visually-hidden">Persona segment dağılım özeti</caption>
                <thead>
                  <tr>
                    <th scope="col">Segment</th>
                    <th scope="col">Sayı</th>
                    <th scope="col">Pay</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.segments.slice(0, 20).map((segment) => (
                    <tr key={segment.key}>
                      <td>{segment.label}</td>
                      <td>{segment.count.toLocaleString("tr-TR")}</td>
                      <td>{(segment.share * 100).toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {preview.segments.length > 20 && (
                <p className="wizard-field-hint">
                  İlk 20 segment gösteriliyor (toplam {preview.segments.length}).
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
