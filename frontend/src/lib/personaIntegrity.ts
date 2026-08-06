// "Kimler simüle edildi?" panelindeki (bkz. frontend/src/pages/Simulations.tsx
// SimulationPersonasPanel) dağılım bütünlük göstergesi için saf, testable
// yardımcı - kalıcı Persona satırlarının toplam `population_weight`i,
// run'in gerçek `persona_count`iyle (bkz. backend
// app.routers.simulations.SimulationRunResponse.persona_count) tutarlı mı?
//
// Ayrı bir dosyada tutulur (Simulations.tsx içinde DEĞİL) - bu, dosyanın
// yalnızca React bileşenleri export ettiği varsayımına dayanan
// `react-refresh/only-export-components` uyarısını (Fast Refresh, bileşen
// olmayan bir export'un aynı dosyada bulunmasından rahatsız olur) ESLint
// disable yorumu EKLEMEDEN çözer.

export type PersonaIntegrityStatus = "matched" | "mismatched" | "unknown";

/**
 * `personaCount` bilinmiyorsa (`null` - bkz. backend `_extract_persona_count`,
 * eski/legacy run veya beklenmeyen tip) her zaman "unknown" döner; YANLIŞ
 * bir toplam asla "matched" olarak raporlanmaz.
 */
export function personaIntegrityStatus(
  totalPopulation: number,
  personaCount: number | null,
): PersonaIntegrityStatus {
  if (personaCount === null) return "unknown";
  return totalPopulation === personaCount ? "matched" : "mismatched";
}
