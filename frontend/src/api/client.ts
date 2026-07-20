import { notifySessionExpired } from "../auth/sessionEvents";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const CSRF_COOKIE_NAME = "csrf_token";
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
// Bu uc noktalar kendi 401 durumlarini (ornegin ilk yuklemede "henuz giris
// yapilmadi") kendileri yonetir; bir "oturum suresi doldu" bildirimi
// tetiklemeleri yanlis pozitif olur (bkz. `AuthProvider`).
const AUTH_PATH_PREFIX = "/api/auth/";

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

function readCookie(name: string): string | null {
  const match = document.cookie.split("; ").find((row) => row.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.split("=").slice(1).join("=")) : null;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };

  // Cift-gonderim (double-submit) CSRF deseni: `csrf_token` cookie'si
  // HttpOnly degildir, bu yuzden burada okunup durum degistiren
  // isteklerde bir header olarak da gonderilir (bkz.
  // docs/architecture.md#cookieler-ve-csrf).
  if (MUTATING_METHODS.has(method)) {
    const csrfToken = readCookie(CSRF_COOKIE_NAME);
    if (csrfToken) {
      headers["X-CSRF-Token"] = csrfToken;
    }
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    method,
    credentials: "include",
    headers,
  });

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // yanit govdesi yok/JSON degil; body null kalir
    }

    if (response.status === 401 && !path.startsWith(AUTH_PATH_PREFIX)) {
      notifySessionExpired();
    }

    const message =
      (body as { detail?: string } | null)?.detail ??
      `API istegi basarisiz: ${response.status} ${response.statusText}`;
    throw new ApiError(response.status, message, body);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export interface HealthResponse {
  status: string;
}

export interface ReadyResponse {
  ready: boolean;
  database: "ok" | "unavailable";
  redis: "ok" | "unavailable";
  environment: string;
}

export function getHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/api/health");
}

export function getReadiness(): Promise<ReadyResponse> {
  return apiFetch<ReadyResponse>("/api/ready");
}

export type EntitlementStatus = "available" | "reserved" | "consumed";

export interface EntitlementSummary {
  feature_key: string;
  status: EntitlementStatus;
  quantity: number;
  reserved_until: string | null;
}

export interface UsageSummaryResponse {
  organization_id: string;
  chip_balance: number;
  entitlements: EntitlementSummary[];
  pricing_version: string;
}

export function getUsageSummary(): Promise<UsageSummaryResponse> {
  return apiFetch<UsageSummaryResponse>("/api/billing/usage-summary");
}

export type ChipLedgerEntryType = "credit" | "reserve" | "consume" | "release" | "adjustment";

export interface ChipLedgerEntryResponse {
  id: string;
  amount: number;
  entry_type: ChipLedgerEntryType;
  reason: string;
  reference_type: string | null;
  reference_id: string | null;
  created_at: string;
}

export function getChipLedger(): Promise<ChipLedgerEntryResponse[]> {
  return apiFetch<ChipLedgerEntryResponse[]>("/api/billing/chip-ledger");
}

// --- Chip paketleri ve yukleme talepleri (gercek odeme saglayicisi yoktur) ---
//
// Bir yukleme talebi olusturmak Chip bakiyesini DEGISTIRMEZ; yalnizca
// `pending` durumda bir talep kaydeder (bkz. backend/app/routers/billing.py).
// Bu ekran kart numarasi/CVV gibi hicbir odeme verisi toplamaz.

export interface ChipPackage {
  key: string;
  name: string;
  chip_amount: number;
}

export interface ChipPackageListResponse {
  package_version: string;
  packages: ChipPackage[];
}

export function getChipPackages(): Promise<ChipPackageListResponse> {
  return apiFetch<ChipPackageListResponse>("/api/billing/chip-packages");
}

export type TopUpRequestStatus = "pending";

export interface TopUpRequestResponse {
  id: string;
  package_key: string;
  chip_amount: number;
  status: TopUpRequestStatus;
  created_at: string;
}

export function createTopUpRequest(packageKey: string): Promise<TopUpRequestResponse> {
  return apiFetch<TopUpRequestResponse>("/api/billing/topup-requests", {
    method: "POST",
    body: JSON.stringify({ package_key: packageKey }),
  });
}

export function listTopUpRequests(): Promise<TopUpRequestResponse[]> {
  return apiFetch<TopUpRequestResponse[]>("/api/billing/topup-requests");
}

// --- Analiz modulu kataloğu (surumlu, salt okunur) -----------------------------

export type ModuleMeasurementType = "technical_measurement" | "synthetic_estimate";

export interface AnalysisModuleDefinition {
  key: string;
  name: string;
  description: string;
  outputs: string[];
  measurement_type: ModuleMeasurementType;
  chip_cost: number;
  free_entitlement_feature_key: string | null;
  estimated_duration_minutes: number;
  selectable_in_wizard: boolean;
}

export interface AnalysisModuleCatalogResponse {
  catalog_version: string;
  modules: AnalysisModuleDefinition[];
}

export function getAnalysisModuleCatalog(): Promise<AnalysisModuleCatalogResponse> {
  return apiFetch<AnalysisModuleCatalogResponse>("/api/analysis-modules/catalog");
}

// --- Projeler ----------------------------------------------------------------

export type ProjectStatus = "active" | "archived";

export interface ProjectResponse {
  id: string;
  organization_id: string;
  name: string;
  description: string | null;
  status: ProjectStatus;
  test_count: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export function listProjects(includeArchived = false): Promise<ProjectResponse[]> {
  const query = includeArchived ? "?include_archived=true" : "";
  return apiFetch<ProjectResponse[]>(`/api/projects${query}`);
}

export function getProject(projectId: string): Promise<ProjectResponse> {
  return apiFetch<ProjectResponse>(`/api/projects/${projectId}`);
}

export interface CreateProjectRequest {
  name: string;
  description?: string | null;
}

export function createProject(body: CreateProjectRequest): Promise<ProjectResponse> {
  return apiFetch<ProjectResponse>("/api/projects", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface UpdateProjectRequest {
  name?: string;
  description?: string | null;
}

export function updateProject(
  projectId: string,
  body: UpdateProjectRequest,
): Promise<ProjectResponse> {
  return apiFetch<ProjectResponse>(`/api/projects/${projectId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function archiveProject(projectId: string): Promise<ProjectResponse> {
  return apiFetch<ProjectResponse>(`/api/projects/${projectId}/archive`, {
    method: "POST",
  });
}

// --- Persona tanimi, dagilimi ve ornekleme ------------------------------------

export interface PersonaDimensionInfo {
  key: string;
  label: string;
}

export function listPersonaDimensions(): Promise<PersonaDimensionInfo[]> {
  return apiFetch<PersonaDimensionInfo[]>("/api/personas/dimensions");
}

export interface PersonaBucket {
  key: string;
  label: string;
  weight: number;
  min_age?: number;
  max_age?: number;
  scenario_interest?: {
    estimate: "low" | "medium" | "high";
    confidence: "low" | "medium" | "high";
    assumption: string;
  };
}

export type PersonaDistribution = Record<string, PersonaBucket[]>;

export interface PersonaPresetResponse {
  id: string;
  is_builtin: boolean;
  organization_id: string | null;
  name: string;
  description: string | null;
  distribution: PersonaDistribution;
  status: "active" | "archived";
  source_builtin_key: string | null;
  created_at: string | null;
  updated_at: string | null;
  archived_at: string | null;
}

export function listPersonaPresets(includeArchived = false): Promise<PersonaPresetResponse[]> {
  const query = includeArchived ? "?include_archived=true" : "";
  return apiFetch<PersonaPresetResponse[]>(`/api/personas/presets${query}`);
}

export function getPersonaPreset(presetId: string): Promise<PersonaPresetResponse> {
  return apiFetch<PersonaPresetResponse>(`/api/personas/presets/${encodeURIComponent(presetId)}`);
}

export interface CreatePersonaPresetRequest {
  name: string;
  description?: string | null;
  distribution: PersonaDistribution;
}

export function createPersonaPreset(
  body: CreatePersonaPresetRequest,
): Promise<PersonaPresetResponse> {
  return apiFetch<PersonaPresetResponse>("/api/personas/presets", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface UpdatePersonaPresetRequest {
  name?: string;
  description?: string | null;
  distribution?: PersonaDistribution;
}

export function updatePersonaPreset(
  presetId: string,
  body: UpdatePersonaPresetRequest,
): Promise<PersonaPresetResponse> {
  return apiFetch<PersonaPresetResponse>(`/api/personas/presets/${presetId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export interface CopyPersonaPresetRequest {
  name: string;
  description?: string | null;
}

export function copyPersonaPreset(
  presetId: string,
  body: CopyPersonaPresetRequest,
): Promise<PersonaPresetResponse> {
  return apiFetch<PersonaPresetResponse>(
    `/api/personas/presets/${encodeURIComponent(presetId)}/copy`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export function archivePersonaPreset(presetId: string): Promise<PersonaPresetResponse> {
  return apiFetch<PersonaPresetResponse>(`/api/personas/presets/${presetId}/archive`, {
    method: "POST",
  });
}

export interface PersonaSamplePreviewRequest {
  persona_count: number;
  preset_id?: string;
  distribution?: PersonaDistribution;
  seed?: number;
}

export interface PersonaCohortSegment {
  key: string;
  label: string;
  dimension_values: Record<string, string>;
  count: number;
  share: number;
}

export interface PersonaSamplePreviewResponse {
  generator_version: string;
  deterministic_seed: number;
  distribution_snapshot: Record<string, unknown>;
  segments: PersonaCohortSegment[];
  total_count: number;
  sample_hash: string;
}

export function previewPersonaSample(
  body: PersonaSamplePreviewRequest,
): Promise<PersonaSamplePreviewResponse> {
  return apiFetch<PersonaSamplePreviewResponse>("/api/personas/sample-preview", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// --- Yeni test kurulum sihirbazi ----------------------------------------------

export type WizardTestType = "existing_site_basic_ux" | "ab_comparison" | "accessibility_precheck";

// Sihirbazda secilebilen modul anahtarlari artik sabit bir union degil,
// analiz modulu katalogundan (bkz. getAnalysisModuleCatalog) dinamik olarak
// gelir; backend zaten gecerliligi dogrular.
export type WizardModuleKey = string;

export interface WizardDraftPayload {
  project_id?: string;
  name?: string;
  target_task?: string;
  test_type?: WizardTestType;
  current_url?: string;
  new_url?: string;
  persona_count?: number;
  target_audience?: string;
  persona_preset_id?: string;
  persona_distribution?: PersonaDistribution;
  modules?: WizardModuleKey[];
  authorization_confirmed?: boolean;
}

export type WizardDraftStatus = "draft" | "launched";

export interface WizardDraftResponse {
  id: string;
  organization_id: string;
  status: WizardDraftStatus;
  current_step: number;
  payload: WizardDraftPayload;
  missing_fields: string[];
  created_at: string;
  updated_at: string;
}

export function createWizardDraft(): Promise<WizardDraftResponse> {
  return apiFetch<WizardDraftResponse>("/api/tests/drafts", { method: "POST" });
}

export function getWizardDraft(draftId: string): Promise<WizardDraftResponse> {
  return apiFetch<WizardDraftResponse>(`/api/tests/drafts/${draftId}`);
}

export function patchWizardDraft(
  draftId: string,
  payload: WizardDraftPayload,
  currentStep?: number,
): Promise<WizardDraftResponse> {
  return apiFetch<WizardDraftResponse>(`/api/tests/drafts/${draftId}`, {
    method: "PATCH",
    body: JSON.stringify({
      payload,
      ...(currentStep !== undefined ? { current_step: currentStep } : {}),
    }),
  });
}

export interface WizardLaunchResponse {
  draft_id: string;
  status: WizardDraftStatus;
  test_definition_id: string;
  simulation_run_ids: string[];
  used_free_entitlement: boolean;
  reserved_chips: number;
  engine_status_message: string;
}

export function launchWizardDraft(draftId: string): Promise<WizardLaunchResponse> {
  return apiFetch<WizardLaunchResponse>(`/api/tests/drafts/${draftId}/launch`, {
    method: "POST",
  });
}

export interface QuoteRequest {
  persona_count: number;
  test_type: string;
  modules?: string[];
}

export interface QuoteLineItemResponse {
  key: string;
  label: string;
  quantity: number;
  unit_chip_cost: number;
  chip_cost: number;
  covered_by_free_entitlement: boolean;
}

export interface QuoteResponse {
  pricing_version: string;
  test_type: string;
  persona_count: number;
  modules: string[];
  free_entitlement_feature_key: string | null;
  free_entitlement_applicable: boolean;
  line_items: QuoteLineItemResponse[];
  required_chips: number;
  total_chips: number;
}

export function getQuote(body: QuoteRequest): Promise<QuoteResponse> {
  return apiFetch<QuoteResponse>("/api/billing/quote", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// --- Sentetik simulasyon calistirmalari ---------------------------------------
//
// Bu motor gercek insan davranisi uretmez (calibration_status daima
// "uncalibrated"); tum sonuclar sentetik senaryo tahminidir (bkz.
// docs/scientific-integrity.md ve docs/methodology.md).

export type SimulationRunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface UncertaintyMetric {
  distribution: string;
  point_estimate: number;
  low: number;
  mode: number;
  high: number;
}

export interface TaskDurationMetric extends UncertaintyMetric {
  unit: string;
  p10: number;
  p50: number;
  p90: number;
}

export interface ContrastCheckResult {
  pass: boolean;
  avg_ratio: number;
  min_ratio: number;
  threshold: number;
}

export interface RegionalInterestEntry {
  region_key: string;
  region_label: string;
  share: number;
  estimate: "low" | "medium" | "high";
  confidence: "low" | "medium" | "high";
  assumption: string;
  disclaimer: string;
}

export interface SimulationResult {
  engine_version: string;
  rules_version: string;
  fixture_version: string;
  generator_version: string | null;
  calibration_status: string;
  deterministic_seed: number;
  variant_role: string;
  url: string;
  metrics: {
    task_completion_probability: UncertaintyMetric;
    task_duration_seconds: TaskDurationMetric;
    misclick_probability: UncertaintyMetric;
    abandonment_probability: UncertaintyMetric;
    readability_score: number;
    contrast_check: ContrastCheckResult;
    regional_interest: RegionalInterestEntry[];
  };
  disclaimer: string;
  methodology_reference: string;
}

export interface SimulationRunResponse {
  id: string;
  organization_id: string;
  test_variant_id: string;
  status: SimulationRunStatus;
  progress_percent: number;
  progress_message: string | null;
  calibration_status: string;
  deterministic_seed: number;
  model_version: string;
  rules_version: string | null;
  fixture_version: string | null;
  error: string | null;
  result: SimulationResult | null;
  not_real_user_data_label: string;
  methodology_reference: string;
  attempt_count: number;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export function listSimulationRuns(testDefinitionId?: string): Promise<SimulationRunResponse[]> {
  const query = testDefinitionId
    ? `?test_definition_id=${encodeURIComponent(testDefinitionId)}`
    : "";
  return apiFetch<SimulationRunResponse[]>(`/api/simulations/runs${query}`);
}

export function getSimulationRun(runId: string): Promise<SimulationRunResponse> {
  return apiFetch<SimulationRunResponse>(`/api/simulations/runs/${runId}`);
}

export function cancelSimulationRun(runId: string): Promise<SimulationRunResponse> {
  return apiFetch<SimulationRunResponse>(`/api/simulations/runs/${runId}/cancel`, {
    method: "POST",
  });
}

export function retrySimulationRun(runId: string): Promise<SimulationRunResponse> {
  return apiFetch<SimulationRunResponse>(`/api/simulations/runs/${runId}/retry`, {
    method: "POST",
  });
}

export interface SimulationComparisonResponse {
  test_definition_id: string;
  variant_a_run: SimulationRunResponse;
  variant_b_run: SimulationRunResponse;
  comparison: {
    comparisons: Record<string, { variant_a: number; variant_b: number; delta: number }>;
    sampled_synthetic_persona_count: { variant_a: number; variant_b: number };
    calibration_status: string;
    note: string;
  };
  not_real_user_data_label: string;
  methodology_reference: string;
}

export function getSimulationComparison(
  testDefinitionId: string,
): Promise<SimulationComparisonResponse> {
  return apiFetch<SimulationComparisonResponse>(`/api/simulations/comparisons/${testDefinitionId}`);
}

// --- Simulasyon raporlari ------------------------------------------------------
//
// Rapor, tamamlanmis bir run'in degismez (immutable) snapshot'indan uretilir;
// run yeniden hesaplanmaz (bkz. backend/app/routers/reports.py).

export interface ReportListItemResponse {
  id: string;
  title: string;
  simulation_run_id: string;
  project_id: string;
  project_name: string;
  test_definition_id: string;
  test_definition_name: string;
  variant_name: string;
  variant_role: string;
  model_version: string;
  calibration_status: string;
  created_at: string;
}

export function listReports(params?: {
  projectId?: string;
  testDefinitionId?: string;
}): Promise<ReportListItemResponse[]> {
  const query = new URLSearchParams();
  if (params?.projectId) query.set("project_id", params.projectId);
  if (params?.testDefinitionId) query.set("test_definition_id", params.testDefinitionId);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return apiFetch<ReportListItemResponse[]>(`/api/reports${suffix}`);
}

export interface ReportInfoBox {
  not_real_user_data_label: string;
  model_version: string;
  calibration_status: string;
  generated_at: string;
  deterministic_seed: number;
  rules_version: string | null;
  fixture_version: string | null;
  input_summary: {
    url?: string | null;
    wizard_test_type?: string | null;
    persona_count?: number | null;
    target_audience?: string | null;
  };
}

export interface ReportPersonaSegment {
  key: string;
  label: string;
  count: number;
  share: number;
  small_sample_warning: boolean;
}

export interface ReportCriticalFinding {
  key: string;
  severity: "info" | "warning";
  text: string;
}

export interface ReportHeatmapRegionBox {
  x_pct: number;
  y_pct: number;
  width_pct: number;
  height_pct: number;
}

export type ReportHeatmapLevel = "low" | "medium" | "high";

export interface ReportHeatmapRegion {
  key: string;
  label: string;
  score: number;
  level: ReportHeatmapLevel;
  box: ReportHeatmapRegionBox | null;
}

export interface ReportHeatmapSection {
  available: boolean;
  label: string;
  grid: Record<string, unknown>[] | null;
  disclaimer: string | null;
  regions?: ReportHeatmapRegion[] | null;
  screenshot_url?: string | null;
  coordinates_available?: boolean;
  coordinates_unavailable_reason?: string | null;
}

export interface ReportCampaignCtaEntry {
  key: string;
  label: string;
  rank: number;
  above_fold: boolean;
  click_probability: {
    distribution: string;
    point_estimate: number;
    low: number;
    mode: number;
    high: number;
  };
}

export interface ReportCampaignCtaFinding {
  key: string;
  severity: "info" | "warning";
  text: string;
}

export interface ReportCampaignCta {
  ctas: ReportCampaignCtaEntry[];
  message_clarity_findings: ReportCampaignCtaFinding[];
  disclaimer: string;
}

export interface ReportNetworkDeviceProfile {
  profile_key: string;
  device_label: string;
  network_label: string;
  succeeded: boolean;
  error: string | null;
  timings: {
    dom_content_loaded_ms: number | null;
    load_event_ms: number | null;
    total_navigation_ms: number | null;
  } | null;
  accessibility_violation_count: number | null;
}

export interface ReportNetworkDevice {
  profiles: ReportNetworkDeviceProfile[];
  error_rate: number;
  disclaimer: string;
}

export interface ReportAccessibleChartSummary {
  chart_key: string;
  text: string;
}

export interface ReportAbComparison {
  comparisons: Record<string, { variant_a: number; variant_b: number; delta: number }>;
  sampled_synthetic_persona_count: { variant_a: number; variant_b: number };
  calibration_status: string;
  note: string;
  this_variant_role: "variant_a" | "variant_b";
  sibling_variant_name: string;
}

export interface ReportDetailResponse {
  id: string;
  title: string;
  created_at: string;
  project_id: string;
  project_name: string;
  test_definition_id: string;
  test_definition_name: string;
  variant_name: string;
  variant_role: string;
  info_box: ReportInfoBox;
  metrics: SimulationResult["metrics"];
  disclaimer: string;
  methodology_reference: string;
  ab_comparison: ReportAbComparison | null;
  persona_segments: ReportPersonaSegment[];
  persona_segment_note: string;
  critical_findings: ReportCriticalFinding[];
  heatmap: ReportHeatmapSection;
  campaign_cta: ReportCampaignCta | null;
  network_device: ReportNetworkDevice | null;
  accessible_chart_summaries: ReportAccessibleChartSummary[];
  export_json_url: string;
  export_csv_url: string;
}

export function getReport(reportId: string): Promise<ReportDetailResponse> {
  return apiFetch<ReportDetailResponse>(`/api/reports/${encodeURIComponent(reportId)}`);
}

export function getReportExportUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

// Rapor sahipligi/tenant izolasyonu backend'de dogrulanir (bkz.
// app.routers.reports.get_report_heatmap_screenshot); bu yalnizca goreli
// yolu mutlak URL'ye cevirir - harici bir kaynagi ASLA dogrudan doner.
export function getReportHeatmapScreenshotUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

// --- AI destekli açıklama (isteğe bağlı, otomatik üretilir) -------------------
//
// Bu bölüm bir "AI kararı" değildir; zaten hesaplanmış rapor metriklerinden
// otomatik üretilen, uzman değerlendirmesi gerektiren bir açıklamadır (bkz.
// docs/ai-policy.md ve backend/app/services/ai_explanation.py). Sağlayıcı
// yapılandırılmamışsa (varsayılan) bile deterministik bir şablon
// açıklaması döner; bu uç nokta hiçbir zaman ham HTML/cookie/token/e-posta
// göndermez veya almaz.

export interface AIExplanationFinding {
  text: string;
  metric_ids: string[];
}

export interface AIExplanationResponse {
  schema_version: string;
  calibration_status: string;
  short_summary: string;
  metric_basis: AIExplanationFinding[];
  possible_explanations: AIExplanationFinding[];
  suggested_verification_experiment: string;
  limitations: string;
  prompt_version: string;
  provider: string;
  model_name: string | null;
  generated_at: string;
}

export function generateReportAiExplanation(reportId: string): Promise<AIExplanationResponse> {
  return apiFetch<AIExplanationResponse>(
    `/api/reports/${encodeURIComponent(reportId)}/ai-explanation`,
    { method: "POST" },
  );
}

// --- Kimlik dogrulama --------------------------------------------------------

export interface SessionResponse {
  user_id: string;
  email: string;
  display_name: string | null;
  organization_id: string;
  organization_name: string;
  role: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  organization_name: string;
  display_name?: string;
}

export function register(body: RegisterRequest): Promise<SessionResponse> {
  return apiFetch<SessionResponse>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface LoginRequest {
  email: string;
  password: string;
}

export function login(body: LoginRequest): Promise<SessionResponse> {
  return apiFetch<SessionResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function refreshSession(): Promise<SessionResponse> {
  return apiFetch<SessionResponse>("/api/auth/refresh", { method: "POST" });
}

export function logout(): Promise<void> {
  return apiFetch<void>("/api/auth/logout", { method: "POST" });
}

export function getMe(): Promise<SessionResponse> {
  return apiFetch<SessionResponse>("/api/auth/me");
}

export interface PasswordResetRequestResponse {
  message: string;
  dev_reset_token: string | null;
}

export function requestPasswordReset(email: string): Promise<PasswordResetRequestResponse> {
  return apiFetch<PasswordResetRequestResponse>("/api/auth/password-reset/request", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export interface PasswordResetConfirmResponse {
  message: string;
}

export function confirmPasswordReset(
  token: string,
  newPassword: string,
): Promise<PasswordResetConfirmResponse> {
  return apiFetch<PasswordResetConfirmResponse>("/api/auth/password-reset/confirm", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword }),
  });
}

// --- Ayarlar ------------------------------------------------------------------

export type ThemePreference = "system" | "light" | "dark";

export interface MeSettingsResponse {
  user_id: string;
  email: string;
  display_name: string | null;
  language: string;
  timezone: string;
  theme: ThemePreference;
  compact_view: boolean;
  notify_simulation_completed: boolean;
  notify_simulation_failed: boolean;
  notify_report_ready: boolean;
  notify_low_chip_balance: boolean;
  low_chip_balance_threshold: number | null;
  updated_at: string;
}

export interface PatchMeSettingsRequest {
  display_name?: string;
  language?: string;
  timezone?: string;
  theme?: ThemePreference;
  compact_view?: boolean;
  notify_simulation_completed?: boolean;
  notify_simulation_failed?: boolean;
  notify_report_ready?: boolean;
  notify_low_chip_balance?: boolean;
  low_chip_balance_threshold?: number;
}

export function getMySettings(): Promise<MeSettingsResponse> {
  return apiFetch<MeSettingsResponse>("/api/settings/me");
}

export function updateMySettings(body: PatchMeSettingsRequest): Promise<MeSettingsResponse> {
  return apiFetch<MeSettingsResponse>("/api/settings/me", {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export interface OrganizationSettingsResponse {
  organization_id: string;
  name: string;
  slug: string;
  role: string;
  created_at: string;
  currency: string;
  default_persona_count: number;
  default_persona_preset_id: string | null;
  default_device_profile: string | null;
  default_modules: string[];
  default_target_audience: string | null;
  effective_default_persona_preset_id: string | null;
  effective_default_device_profile: string | null;
  effective_default_modules: string[];
  warnings: string[];
  can_edit_company: boolean;
  can_edit_defaults: boolean;
}

export interface PatchOrganizationSettingsRequest {
  name?: string;
  currency?: string;
  default_persona_count?: number;
  default_persona_preset_id?: string;
  default_device_profile?: string;
  default_modules?: string[];
  default_target_audience?: string;
}

export function getOrganizationSettings(): Promise<OrganizationSettingsResponse> {
  return apiFetch<OrganizationSettingsResponse>("/api/settings/organization");
}

export function updateOrganizationSettings(
  body: PatchOrganizationSettingsRequest,
): Promise<OrganizationSettingsResponse> {
  return apiFetch<OrganizationSettingsResponse>("/api/settings/organization", {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
