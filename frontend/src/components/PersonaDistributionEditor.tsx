import { useId } from "react";
import type { PersonaBucket, PersonaDimensionInfo, PersonaDistribution } from "../api/client";

interface PersonaDistributionEditorProps {
  dimensions: PersonaDimensionInfo[];
  distribution: PersonaDistribution;
  onChange: (next: PersonaDistribution) => void;
}

function sumWeights(buckets: PersonaBucket[] | undefined): number {
  return (buckets ?? []).reduce((total, bucket) => total + (Number(bucket.weight) || 0), 0);
}

function newBucketKey(dimensionKey: string, buckets: PersonaBucket[]): string {
  let index = buckets.length + 1;
  let key = `${dimensionKey}_${index}`;
  const existing = new Set(buckets.map((b) => b.key));
  while (existing.has(key)) {
    index += 1;
    key = `${dimensionKey}_${index}`;
  }
  return key;
}

/** Persona dagilim boyutlarini (yas, bolge, cihaz, dijital yatkinlik, erisilebilirlik)
 * agirlikli kova listeleri olarak duzenlemeye yarayan, erisilebilir form kontrolleri. */
export default function PersonaDistributionEditor({
  dimensions,
  distribution,
  onChange,
}: PersonaDistributionEditorProps) {
  const baseId = useId();

  const toggleDimension = (dimensionKey: string, enabled: boolean) => {
    const next = { ...distribution };
    if (enabled) {
      next[dimensionKey] = [{ key: newBucketKey(dimensionKey, []), label: "Kova 1", weight: 100 }];
    } else {
      delete next[dimensionKey];
    }
    onChange(next);
  };

  const updateBuckets = (dimensionKey: string, buckets: PersonaBucket[]) => {
    onChange({ ...distribution, [dimensionKey]: buckets });
  };

  const addBucket = (dimensionKey: string) => {
    const buckets = distribution[dimensionKey] ?? [];
    updateBuckets(dimensionKey, [
      ...buckets,
      { key: newBucketKey(dimensionKey, buckets), label: `Kova ${buckets.length + 1}`, weight: 0 },
    ]);
  };

  const removeBucket = (dimensionKey: string, bucketKey: string) => {
    const buckets = (distribution[dimensionKey] ?? []).filter((b) => b.key !== bucketKey);
    updateBuckets(dimensionKey, buckets);
  };

  const updateBucket = (dimensionKey: string, bucketKey: string, patch: Partial<PersonaBucket>) => {
    const buckets = (distribution[dimensionKey] ?? []).map((b) =>
      b.key === bucketKey ? { ...b, ...patch } : b,
    );
    updateBuckets(dimensionKey, buckets);
  };

  return (
    <div className="persona-distribution-editor">
      {dimensions.map((dimension) => {
        const enabled = dimension.key in distribution;
        const buckets = distribution[dimension.key] ?? [];
        const total = sumWeights(buckets);
        const totalIsValid = !enabled || Math.abs(total - 100) < 0.01;
        const legendId = `${baseId}-${dimension.key}-legend`;
        const sumId = `${baseId}-${dimension.key}-sum`;

        return (
          <fieldset key={dimension.key} className="persona-dimension" aria-labelledby={legendId}>
            <legend id={legendId} className="persona-dimension__legend">
              <label>
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(event) => toggleDimension(dimension.key, event.target.checked)}
                />{" "}
                {dimension.label}
              </label>
            </legend>

            {enabled && (
              <div className="persona-dimension__body">
                {buckets.map((bucket, index) => {
                  const labelInputId = `${baseId}-${dimension.key}-${index}-label`;
                  const weightInputId = `${baseId}-${dimension.key}-${index}-weight`;
                  return (
                    <div key={bucket.key} className="persona-bucket-row">
                      <label htmlFor={labelInputId} className="visually-hidden">
                        {dimension.label} kova adı
                      </label>
                      <input
                        id={labelInputId}
                        type="text"
                        value={bucket.label}
                        onChange={(event) =>
                          updateBucket(dimension.key, bucket.key, { label: event.target.value })
                        }
                        aria-label={`${dimension.label} kova adı`}
                      />

                      {dimension.key === "age_range" && (
                        <>
                          <label className="visually-hidden" htmlFor={`${labelInputId}-min`}>
                            Min yaş
                          </label>
                          <input
                            id={`${labelInputId}-min`}
                            type="number"
                            min={0}
                            value={bucket.min_age ?? 0}
                            onChange={(event) =>
                              updateBucket(dimension.key, bucket.key, {
                                min_age: Number(event.target.value),
                              })
                            }
                            aria-label={`${dimension.label} minimum yaş`}
                          />
                          <label className="visually-hidden" htmlFor={`${labelInputId}-max`}>
                            Max yaş
                          </label>
                          <input
                            id={`${labelInputId}-max`}
                            type="number"
                            min={0}
                            value={bucket.max_age ?? 0}
                            onChange={(event) =>
                              updateBucket(dimension.key, bucket.key, {
                                max_age: Number(event.target.value),
                              })
                            }
                            aria-label={`${dimension.label} maksimum yaş`}
                          />
                        </>
                      )}

                      <label htmlFor={weightInputId} className="visually-hidden">
                        {dimension.label} ağırlık yüzdesi
                      </label>
                      <input
                        id={weightInputId}
                        type="number"
                        min={0}
                        max={100}
                        step="0.1"
                        value={bucket.weight}
                        onChange={(event) =>
                          updateBucket(dimension.key, bucket.key, {
                            weight: Number(event.target.value),
                          })
                        }
                        aria-label={`${dimension.label} ağırlık yüzdesi`}
                        aria-describedby={sumId}
                      />
                      <span aria-hidden="true">%</span>

                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => removeBucket(dimension.key, bucket.key)}
                        disabled={buckets.length <= 1}
                        aria-label={`${bucket.label} kovasını kaldır`}
                      >
                        Kaldır
                      </button>
                    </div>
                  );
                })}

                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => addBucket(dimension.key)}
                >
                  + Kova ekle
                </button>

                <p
                  id={sumId}
                  role="status"
                  className={totalIsValid ? "wizard-field-hint" : "auth-error"}
                >
                  Toplam ağırlık: {total.toFixed(1)}% {totalIsValid ? "" : "(100% olmalıdır)"}
                </p>
              </div>
            )}
          </fieldset>
        );
      })}
    </div>
  );
}
