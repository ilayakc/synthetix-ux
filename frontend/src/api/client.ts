import { notifySessionExpired } from "../auth/sessionEvents";
import { formatDuration, getSlowRequestThresholdMs, logger, logTiming } from "../lib/logger";

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

const GENERIC_ERROR_MESSAGE = "İstek işlenirken bir hata oluştu.";

// Backend'in eski hata sözleşmelerinde Türkçe karakter kullanılmayan metinler
// bulunuyor. API kodlarına (ör. `ai_report_not_ready`) dokunmadan yalnızca
// kullanıcıya gösterilecek bağımsız kelimeleri düzeltir.
function normalizeTurkishErrorCopy(value: string): string {
  const replacements: Array<[RegExp, string]> = [
    [/\bGecersiz\b/g, "Geçersiz"],
    [/\bgecersiz\b/g, "geçersiz"],
    [/\bgecerli\b/g, "geçerli"],
    [/\bbulunamadi\b/g, "bulunamadı"],
    [/\bsuresi\b/g, "süresi"],
    [/\bdolmus\b/g, "dolmuş"],
    [/\bislem\b/g, "işlem"],
    [/\bicin\b/g, "için"],
    [/\bdogrulamasi\b/g, "doğrulaması"],
    [/\bbasarisiz\b/g, "başarısız"],
    [/\bUretim\b/g, "Üretim"],
    [/\bArsivlenmis\b/g, "Arşivlenmiş"],
    [/\bguncellenemez\b/g, "güncellenemez"],
    [/\bTasarim\b/g, "Tasarım"],
    [/\bgorseli\b/g, "görseli"],
    [/\bartik\b/g, "artık"],
    [/\bkullanilabilir\b/g, "kullanılabilir"],
    [/\bdegil\b/g, "değil"],
    [/\bgoruntusu\b/g, "görüntüsü"],
    [/\bCalistirma\b/g, "Çalıştırma"],
    [/\bUcretsiz\b/g, "Ücretsiz"],
    [/\bKarsilastirma\b/g, "Karşılaştırma"],
    [/\byalnizca\b/g, "yalnızca"],
    [/\bvaryantin\b/g, "varyantın"],
    [/\bbasariyla\b/g, "başarıyla"],
    [/\btamamlanmis\b/g, "tamamlanmış"],
    [/\bBaslatilmis\b/g, "Başlatılmış"],
    [/\bdegistirilemez\b/g, "değiştirilemez"],
    [/\barasinda\b/g, "arasında"],
    [/\bolmalidir\b/g, "olmalıdır"],
    [/\bBos\b/g, "Boş"],
    [/\byuklenemez\b/g, "yüklenemez"],
    [/\bkayitli\b/g, "kayıtlı"],
    [/\bguvenlik\b/g, "güvenlik"],
    [/\bsonlandirildi\b/g, "sonlandırıldı"],
    [/\blutfen\b/g, "lütfen"],
    [/\bgiris\b/g, "giriş"],
    [/\bolusturulamadi\b/g, "oluşturulamadı"],
  ];
  return replacements.reduce(
    (text, [pattern, replacement]) => text.replace(pattern, replacement),
    value,
  );
}

// FastAPI `detail` alani her zaman string degildir: 422 dogrulama
// hatalarinda `{loc, msg, type}` nesnelerinden olusan bir dizi, bazi
// durumlarda ise tek bir nesne donebilir. Ham nesneyi/diziyi dogrudan
// mesaj olarak kullanmak `String(...)` -> "[object Object]" sonucuna
// yol acar; bu yuzden burada kullaniciya gosterilecek kisa, guvenli bir
// metne cevriliyor.
function describeErrorDetail(detail: unknown): string | null {
  if (typeof detail === "string") {
    return normalizeTurkishErrorCopy(detail);
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item && typeof item.msg === "string"
          ? normalizeTurkishErrorCopy(item.msg)
          : null,
      )
      .filter((msg): msg is string => Boolean(msg));
    return messages.length > 0 ? messages.join("; ") : GENERIC_ERROR_MESSAGE;
  }
  if (detail && typeof detail === "object") {
    return GENERIC_ERROR_MESSAGE;
  }
  return null;
}

async function rawFetch<T>(path: string, init?: RequestInit): Promise<T> {
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

  // Merkezi istek loglamasi: her `apiFetch`/`rawFetch` cagrisi bu tek
  // noktadan gectigi icin her sayfada tekrar tekrar ayni loglama kodu
  // yazilmaz (bkz. frontend/src/lib/logger.ts). `/api/auth/*` istekleri
  // "auth" kategorisinde, digerleri "api" kategorisinde loglanir. Istek/yanit
  // GOVDESI (sifre dahil olabilecek login/register body'si dahil) ASLA
  // loglanmaz - yalnizca metod, yol, durum kodu ve sure.
  const logCategory = path.startsWith(AUTH_PATH_PREFIX) ? "auth" : "api";
  const slowThresholdMs = getSlowRequestThresholdMs();
  const requestStart = performance.now();

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      method,
      credentials: "include",
      headers,
    });
  } catch (error) {
    const elapsed = performance.now() - requestStart;
    logger.error(logCategory, `${method} ${path} failed after ${formatDuration(elapsed)}`, error);
    throw error;
  }

  const elapsedMs = performance.now() - requestStart;
  const durationText = formatDuration(elapsedMs);
  if (elapsedMs > slowThresholdMs) {
    logger.warn(
      logCategory,
      `${method} ${path} -> ${response.status} took ${durationText} (slow, >${(slowThresholdMs / 1000).toFixed(2)} s)`,
    );
  } else {
    logger.info(logCategory, `${method} ${path} -> ${response.status} took ${durationText}`);
  }

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // yanit govdesi yok/JSON degil; body null kalir
    }

    const detail = (body as { detail?: unknown } | null)?.detail;
    const message =
      describeErrorDetail(detail) ??
      `API isteği başarısız: ${response.status} ${response.statusText}`;
    logger.error(logCategory, `${method} ${path} -> ${response.status} failed: ${message}`);
    throw new ApiError(response.status, message, body);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

// Erisim tokeni suresi dolan (15 dk) ancak yenileme tokeni gecerli olan bir
// oturumda, arka planda alinan tek bir 401 dogrudan "oturum suresi doldu"
// olarak yorumlanmamali; once sessizce `/api/auth/refresh` denenir ve
// basarili olursa orijinal istek bir kez daha tekrarlanir. Ayni anda
// birden fazla istek 401 alirsa yenileme tek seferde paylasilir.
let pendingRefresh: Promise<void> | null = null;

function refreshAccessToken(): Promise<void> {
  if (!pendingRefresh) {
    pendingRefresh = rawFetch<SessionResponse>("/api/auth/refresh", { method: "POST" })
      .then(() => undefined)
      .finally(() => {
        pendingRefresh = null;
      });
  }
  return pendingRefresh;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    return await rawFetch<T>(path, init);
  } catch (error) {
    const isUnauthorized = error instanceof ApiError && error.status === 401;
    if (!isUnauthorized || path.startsWith(AUTH_PATH_PREFIX)) {
      throw error;
    }

    try {
      await refreshAccessToken();
    } catch {
      notifySessionExpired();
      throw error;
    }

    try {
      return await rawFetch<T>(path, init);
    } catch (retryError) {
      if (retryError instanceof ApiError && retryError.status === 401) {
        notifySessionExpired();
      }
      throw retryError;
    }
  }
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
  price_try: number | null;
}

export interface ChipPackageListResponse {
  package_version: string;
  packages: ChipPackage[];
}

export function getChipPackages(): Promise<ChipPackageListResponse> {
  return apiFetch<ChipPackageListResponse>("/api/billing/chip-packages");
}

export type TopUpRequestStatus = "pending" | "approved" | "rejected";

export interface TopUpRequestResponse {
  id: string;
  package_key: string;
  chip_amount: number;
  status: TopUpRequestStatus;
  created_at: string;
  reviewed_at: string | null;
  review_note: string | null;
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

// --- Platform yönetimi -------------------------------------------------------

export interface AdminSummaryResponse {
  organization_count: number;
  user_count: number;
  pending_topup_count: number;
  failed_ai_pipeline_count: number;
}

export interface AdminTopUpRequestResponse {
  id: string;
  organization_id: string;
  organization_name: string;
  requested_by_email: string;
  requested_by_display_name: string | null;
  package_key: string;
  chip_amount: number;
  status: TopUpRequestStatus;
  created_at: string;
  reviewed_at: string | null;
  reviewed_by_email: string | null;
  review_note: string | null;
}

export interface AdminPlatformSettingsResponse {
  environment: string;
  ai_report: {
    enabled: boolean;
    provider: "disabled" | "mock" | "openai" | "ollama";
    provider_ready: boolean;
    model: string | null;
  };
  chip_topups: {
    review_mode: "manual_admin_review";
    approval_credits_once: boolean;
    rejection_note_required: boolean;
  };
  security: {
    secure_cookies: boolean;
    access_token_ttl_minutes: number;
    refresh_token_ttl_days: number;
    login_rate_limit_max_attempts: number;
    login_rate_limit_window_minutes: number;
  };
  operations: {
    log_level: string;
    analyzer_timeout_seconds: number;
    report_screenshot_retention_days: number;
  };
}

export function getAdminSummary(): Promise<AdminSummaryResponse> {
  return apiFetch<AdminSummaryResponse>("/api/admin/summary");
}

export function getAdminPlatformSettings(): Promise<AdminPlatformSettingsResponse> {
  return apiFetch<AdminPlatformSettingsResponse>("/api/admin/settings");
}

export function listAdminTopUpRequests(
  status?: TopUpRequestStatus,
): Promise<AdminTopUpRequestResponse[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiFetch<AdminTopUpRequestResponse[]>(`/api/admin/topup-requests${query}`);
}

export function reviewAdminTopUpRequest(
  requestId: string,
  action: "approve" | "reject",
  note?: string,
): Promise<AdminTopUpRequestResponse> {
  return apiFetch<AdminTopUpRequestResponse>(
    `/api/admin/topup-requests/${encodeURIComponent(requestId)}/review`,
    {
      method: "POST",
      body: JSON.stringify({ action, note: note?.trim() || null }),
    },
  );
}

// --- Analiz modulu kataloğu (surumlu, salt okunur) -----------------------------

export type ModuleMeasurementType = "technical_measurement" | "synthetic_estimate";

export type ModuleSourceType = "url" | "screenshot" | "ai_generated";

export interface AnalysisModuleDefinition {
  key: string;
  name: string;
  description: string;
  outputs: string[];
  measurement_type: ModuleMeasurementType;
  chip_cost: number;
  free_entitlement_feature_key: string | null;
  post_entitlement_cost_label?: string | null;
  estimated_duration_minutes: number;
  selectable_in_wizard: boolean;
  supported_source_types: ModuleSourceType[];
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

export function deleteProject(projectId: string): Promise<void> {
  return apiFetch<void>(`/api/projects/${projectId}`, { method: "DELETE" });
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

// Mevcut tasarimin (current_*) kaynagi: bir URL veya yuklenen bir ekran
// goruntusu (design asset). Eski taslaklarda bu alan hic bulunmayabilir;
// backend bu durumda "url" varsayar (bkz. backend/app/services/test_wizard.py
// effective_source_type). Ekran goruntusu kaynagi bu surumde yalnizca
// "existing_site_basic_ux" icin secilebilir ve henuz analiz motoruna
// baglanmadigi icin bu kaynakla GERCEK bir test BASLATILAMAZ (bkz.
// DesignSourcePicker ve Step5Summary).
export type WizardCurrentSourceType = "url" | "screenshot";

// "Tasarim B" (new_*) kaynagi: A/B karsilastirmasinda yalnizca gecerlidir.
// Eski taslaklarda bu alan hic bulunmayabilir; backend bu durumda "url"
// varsayar (bkz. backend/app/services/test_wizard.py
// effective_new_source_type). "ai_generated" yalnizca Tasarim B tarafi icin
// gecerlidir (Tasarim A hicbir zaman AI ile uretilemez). Bu kaynaklarin
// hicbiriyle GERCEK bir test BASLATILAMAZ (bkz. DesignBSourcePicker ve
// Step5Summary).
export type WizardNewSourceType = "url" | "screenshot" | "ai_generated";

// Kullanicinin bir CTA adayini onaylamasi VEYA kendi kutusunu cizmesi (Paket
// 4C+4D). Draft+slot (current/new) bagliminda tutulur - `design_asset_id`
// GLOBAL bir asset ozelligi degildir, ayni asset farkli taslaklarda/taraflarda
// kullanilabilir (bkz. backend/app/services/test_wizard.py). `box` 0-1
// normalize kutu (analiz ediligin gorselin oranina gore); `verified_content_sha256`
// HER ZAMAN sunucu tarafinda hesaplanir - client tarafinda gonderilen/uretilen
// hicbir hash degeri kullanilmaz/gonderilmemelidir (sunucu zaten yok sayar).
export type CtaAnnotationSelectionSource = "candidate_confirmation" | "manual_box";

export interface CtaAnnotationBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface CtaAnnotationInput {
  design_asset_id: string;
  box: CtaAnnotationBox;
  selection_source: CtaAnnotationSelectionSource;
  source_candidate_index?: number;
}

export interface CtaAnnotation extends CtaAnnotationInput {
  verified_content_sha256: string;
}

export interface WizardDraftPayload {
  project_id?: string;
  name?: string;
  target_task?: string;
  test_type?: WizardTestType;
  current_url?: string;
  current_source_type?: WizardCurrentSourceType;
  current_design_asset_id?: string;
  current_cta_annotation?: CtaAnnotation | null;
  new_url?: string;
  new_source_type?: WizardNewSourceType;
  new_design_asset_id?: string;
  new_ai_generation_id?: string;
  new_cta_annotation?: CtaAnnotation | null;
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
  // Zorla reddedilmeyen ama kullaniciya gosterilmesi gereken durumlar (ör.
  // CTA secimi neredeyse tum gorseli kapliyor) - bkz. backend PatchDraftRequest.
  warnings: string[];
}

export function createWizardDraft(): Promise<WizardDraftResponse> {
  return apiFetch<WizardDraftResponse>("/api/tests/drafts", { method: "POST" });
}

export function listWizardDrafts(): Promise<WizardDraftResponse[]> {
  return apiFetch<WizardDraftResponse[]>("/api/tests/drafts");
}

export function getWizardDraft(draftId: string): Promise<WizardDraftResponse> {
  return apiFetch<WizardDraftResponse>(`/api/tests/drafts/${draftId}`);
}

export function deleteWizardDraft(draftId: string): Promise<void> {
  return apiFetch<void>(`/api/tests/drafts/${draftId}`, { method: "DELETE" });
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

export interface WizardLaunchWarning {
  code: string;
  slot: "current" | "new";
  message: string;
}

export interface WizardLaunchResponse {
  draft_id: string;
  status: WizardDraftStatus;
  test_definition_id: string;
  simulation_run_ids: string[];
  used_free_entitlement: boolean;
  reserved_chips: number;
  engine_status_message: string;
  warnings: WizardLaunchWarning[];
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
  ai_report_chips?: number;
  interaction_heatmap_chips?: number;
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
  retryable: boolean;
  failure_code: string | null;
  not_real_user_data_label: string;
  methodology_reference: string;
  attempt_count: number;
  // "Kimler simüle edildi?" panelindeki (bkz. GET .../runs/{run_id}/personas)
  // dağılım bütünlük göstergesi için beklenen kullanıcı sayısı - eski/legacy
  // run'larda veya beklenmeyen bir tipte backend bunu `null` döner (bkz.
  // app.routers.simulations._extract_persona_count). Alan HER ZAMAN JSON'da
  // bulunur (Pydantic `default=None` olsa da response'ta anahtar dusulmez),
  // bu yuzden opsiyonel (`?`) DEGIL, `number | null` olarak tanimlanir.
  persona_count: number | null;
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

// Bir calistirmaya ait, kalici olarak kaydedilmis (bkz. app.models.personas.
// Persona / app.services.test_wizard.launch_draft) temsili persona satirlari -
// gercek kisiler DEGILDIR, secilen hedef kitle dagilimini temsil eden sentetik
// kullanici gruplaridir. En fazla 100 satir doner, `index` alanina gore artan
// siralidir; persona secimi yapilmamis (veya eski) bir calistirma icin bos
// dizi doner (404 degil).
export interface SimulationRunPersonaResponse {
  id: string;
  simulation_run_id: string;
  index: number;
  label: string;
  attributes: Record<string, string>;
  population_weight: number;
  created_at: string;
}

export function getSimulationRunPersonas(runId: string): Promise<SimulationRunPersonaResponse[]> {
  return apiFetch<SimulationRunPersonaResponse[]>(
    `/api/simulations/runs/${encodeURIComponent(runId)}/personas`,
  );
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
    source_type?: "url" | "screenshot" | "ai_generated" | null;
    wizard_test_type?: string | null;
    persona_count?: number | null;
    target_audience?: string | null;
    target_task?: string | null;
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

export type ReportHeatmapOverlayKind = "semantic_region" | "synthetic_visual_attention";
export type ReportFeatureSource = "dom" | "visual_heuristic";

export interface ReportVisualAttentionCell {
  x: number;
  y: number;
  w: number;
  h: number;
  intensity: number;
}

export interface ReportHeatmapSection {
  available: boolean;
  label: string;
  overlay_kind: ReportHeatmapOverlayKind;
  feature_source: ReportFeatureSource | null;
  grid: Record<string, unknown>[] | null;
  disclaimer: string | null;
  regions?: ReportHeatmapRegion[] | null;
  click_grid?: Record<string, unknown>[] | null;
  click_regions?: ReportHeatmapRegion[] | null;
  visual_cells?: ReportVisualAttentionCell[] | null;
  algorithm_version?: string | null;
  image_width?: number | null;
  image_height?: number | null;
  content_sha256?: string | null;
  screenshot_url?: string | null;
  coordinates_available?: boolean;
  coordinates_unavailable_reason?: string | null;
}

export type InteractionHeatmapStatus =
  | "not_requested"
  | "queued"
  | "running"
  | "succeeded"
  | "failed";

export interface ReportInteractionHeatmapHotspot {
  candidate_id: string;
  label: string;
  interaction_kind: string;
  role: string;
  x: number;
  y: number;
  w: number;
  h: number;
  above_fold: boolean;
  score: number;
  confidence: "high" | "medium" | "low";
  reason: string;
  task_relevance: "direct" | "related" | "weak" | "none";
}

export interface ReportInteractionHeatmapSection {
  requested: boolean;
  status: InteractionHeatmapStatus;
  available: boolean;
  title: string;
  provider: string | null;
  model_name: string | null;
  method: string | null;
  method_label: string | null;
  summary: string | null;
  hotspots: ReportInteractionHeatmapHotspot[];
  unmatched_task_warning: string | null;
  disclaimer: string;
  error_code: string | null;
  status_note: string | null;
  screenshot_url: string | null;
  image_width: number | null;
  image_height: number | null;
  coordinates_available: boolean;
}

export type CtaOverlayClassification =
  "dom_interactive_candidate" | "visual_cta_candidate" | "user_confirmed_cta";

export interface ReportCtaOverlayBox {
  classification: CtaOverlayClassification;
  label: string;
  interaction_kind?:
    | "form_action"
    | "button"
    | "cta_link"
    | "content_link"
    | "image_link"
    | "navigation_action"
    | "navigation_link"
    | "pagination_control"
    | "container_link"
    | null;
  x: number;
  y: number;
  w: number;
  h: number;
  heuristic_score?: number | null;
}

export interface ReportCtaOverlaySection {
  available: boolean;
  feature_source: ReportFeatureSource | null;
  boxes: ReportCtaOverlayBox[];
  screenshot_url?: string | null;
  coordinates_available?: boolean;
  coordinates_unavailable_reason?: string | null;
  disclaimer: string;
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
  sibling_report_id: string;
  this_source_type: "url" | "screenshot" | "ai_generated";
  sibling_source_type: "url" | "screenshot" | "ai_generated";
  sibling_heatmap: ReportHeatmapSection;
  sibling_interaction_heatmap?: ReportInteractionHeatmapSection;
  sibling_cta_overlay: ReportCtaOverlaySection;
  same_snapshot_sha256: boolean;
}

export interface ReportDetailResponse {
  id: string;
  // Bu raporun ait olduğu SimulationRun. Tamamlanmış `ai_report` pipeline'ına
  // (getAiPipelineStatus/getAiReport) ulaşmak için gereklidir; standart rapor
  // içeriğini değiştirmez (bkz. backend app.routers.reports.ReportDetailResponse).
  simulation_run_id: string;
  ai_report_requested?: boolean;
  interaction_heatmap_requested?: boolean;
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
  interaction_heatmap?: ReportInteractionHeatmapSection;
  cta_overlay: ReportCtaOverlaySection;
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

// --- AI raporu pipeline (ai_report): salt-okunur durum + nihai UX raporu ------
//
// Backend sözleşmeleri (bkz. backend/app/services/ai_pipeline/queries.py):
//   GET /api/simulations/runs/{run_id}/ai-pipeline -> AIPipelineStatusResponse
//   GET /api/simulations/runs/{run_id}/ai-report    -> AIReportResponse
// Bu, standart raporu sadeleştiren "AI destekli açıklama" özelliğinden TAMAMEN
// AYRIDIR. Pipeline yoksa /ai-pipeline 404 (detail "ai_pipeline_not_found" veya
// "Calistirma bulunamadi") döner; rapor henüz hazır değilse /ai-report 409
// (detail "ai_report_not_ready") döner. Tenant izolasyonu backend'de
// `_load_scoped_run` ile zorlanır; frontend buna dayanmaz. Alanlar backend
// Pydantic modelleriyle BİREBİR eşleşir; tahminî alan eklenmez.

// AIPipelineStatus (queued/running/succeeded/failed/partial/cancelled) ve stage
// enum'ları lowercase string olarak döner; ileride yeni değer eklenebileceği
// için `status`/`stage_type` alanları `string` bırakılır (kırılgan union yok).
export interface AIPipelineStageSummary {
  stage_type: string;
  status: string;
  batch_index: number | null;
  attempt_count: number;
  error_code: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AIPipelineStatusResponse {
  pipeline_id: string;
  simulation_run_id: string;
  status: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  cancel_requested: boolean;
  expected_stage_count: number;
  completed_stage_count: number;
  succeeded_stage_count: number;
  running_stage_count: number;
  queued_stage_count: number;
  failed_stage_count: number;
  progress_percent: number;
  report_available: boolean;
  stages: AIPipelineStageSummary[];
}

export type AIReportPriority = "high" | "medium" | "low";

export interface AIReportFinding {
  finding_id: string;
  priority: AIReportPriority;
  finding: string;
  source_stage: string;
  evidence_references: string[];
  affected_persona_groups: string[];
  estimated_affected_users: number;
  recommendation: string;
  confidence: number;
}

export interface UXReport {
  report_version: string;
  summary: string;
  findings: AIReportFinding[];
  limitations: string;
  disclaimer: string;
}

export interface AIReportResponse {
  pipeline_id: string;
  simulation_run_id: string;
  generated_at: string | null;
  content_format: string;
  synthetic_disclaimer: string;
  provider: string | null;
  model_name: string | null;
  instruction_version: string | null;
  report: UXReport;
}

export function getAiPipelineStatus(runId: string): Promise<AIPipelineStatusResponse> {
  return apiFetch<AIPipelineStatusResponse>(
    `/api/simulations/runs/${encodeURIComponent(runId)}/ai-pipeline`,
  );
}

export function getAiReport(runId: string): Promise<AIReportResponse> {
  return apiFetch<AIReportResponse>(`/api/simulations/runs/${encodeURIComponent(runId)}/ai-report`);
}

// --- Yüklenen tasarım ekran görüntüleri (design assets) -----------------------
//
// Yalnızca PNG/JPEG/WebP kabul edilir; dosya backend'de gerçekten decode
// edilerek doğrulanır ve EXIF/metadata temizlenir (bkz.
// backend/app/services/design_assets.py). Bu uç nokta bir `multipart/form-data`
// isteği olduğu için `apiFetch`'in sabit JSON `Content-Type` header'ını
// kullanamaz (tarayıcının `boundary` içeren `Content-Type`'ı kendisinin
// belirlemesine izin vermek için header hiç ayarlanmaz); ilerleme (progress)
// bildirimi de fetch API ile mümkün olmadığı için ayrı bir `XMLHttpRequest`
// tabanlı yardımcı kullanılır (CSRF header'ı ve `credentials`/`withCredentials`
// davranışı `rawFetch` ile aynı şekilde korunur).

export type DesignAssetContentType = "image/png" | "image/jpeg" | "image/webp";
export type DesignAssetStatus = "active" | "deleted";

export interface DesignAssetResponse {
  id: string;
  organization_id: string;
  content_type: DesignAssetContentType;
  byte_size: number;
  width: number;
  height: number;
  label: string | null;
  status: DesignAssetStatus;
  has_image: boolean;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface UploadDesignAssetOptions {
  label?: string;
  onProgress?: (loadedBytes: number, totalBytes: number) => void;
}

interface RawUploadAttemptResult {
  status: number;
  statusText: string;
  body: unknown;
}

function _performUploadAttempt(
  file: File,
  options: UploadDesignAssetOptions | undefined,
): Promise<RawUploadAttemptResult> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);
    if (options?.label) {
      formData.append("label", options.label);
    }

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE_URL}/api/design-assets`);
    xhr.withCredentials = true;

    // Her deneme (ilk çağrı ve 401 sonrası tekrar deneme) çağrı anındaki
    // güncel `csrf_token` cookie'sini okur; bir yenileme (refresh) CSRF
    // cookie'sini rotate etmiş olsa bile tekrar deneme eski/geçersiz bir
    // token kullanmaz.
    const csrfToken = readCookie(CSRF_COOKIE_NAME);
    if (csrfToken) {
      xhr.setRequestHeader("X-CSRF-Token", csrfToken);
    }

    if (options?.onProgress) {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          options.onProgress?.(event.loaded, event.total);
        }
      };
    }

    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        // yanıt gövdesi JSON değil; body null kalır
      }
      resolve({ status: xhr.status, statusText: xhr.statusText, body });
    };

    xhr.onerror = () => {
      reject(new ApiError(0, "Yükleme sırasında ağ hatası oluştu", null));
    };

    xhr.send(formData);
  });
}

function _uploadResultToError(result: RawUploadAttemptResult): ApiError {
  const detail = (result.body as { detail?: unknown } | null)?.detail;
  const message =
    describeErrorDetail(detail) ?? `Yükleme başarısız: ${result.status} ${result.statusText}`;
  return new ApiError(result.status, message, result.body);
}

// `apiFetch` ile aynı sessiz 401 yenileme/tekrar deneme deseni (bkz.
// `refreshAccessToken`/`apiFetch` yukarıda): erişim tokeni tam yükleme
// sırasında dolmuşsa, `/api/auth/refresh` bir kez denenir ve başarılı olursa
// yükleme TAM OLARAK BİR KEZ tekrarlanır (sonsuz döngü yoktur - ikinci
// denemenin sonucu, başarılı da olsa başarısız da olsa doğrudan döndürülür/
// fırlatılır). İlerleme (progress) callback'i her iki denemede de aynı
// şekilde çalışmaya devam eder.
export async function uploadDesignAsset(
  file: File,
  options?: UploadDesignAssetOptions,
): Promise<DesignAssetResponse> {
  // `_performUploadAttempt` `rawFetch`'i (dolayisiyla merkezi istek
  // loglamasini) kullanmaz - XHR tabanli oldugu icin ayri, ozel bir
  // `logTiming` sarmalayicisi kullanilir (bkz. frontend/src/lib/logger.ts).
  return logTiming("api", "POST /api/design-assets", async () => {
    const first = await _performUploadAttempt(file, options);
    if (first.status >= 200 && first.status < 300) {
      return first.body as DesignAssetResponse;
    }
    if (first.status !== 401) {
      throw _uploadResultToError(first);
    }

    try {
      await refreshAccessToken();
    } catch {
      notifySessionExpired();
      throw _uploadResultToError(first);
    }

    const retry = await _performUploadAttempt(file, options);
    if (retry.status >= 200 && retry.status < 300) {
      return retry.body as DesignAssetResponse;
    }
    if (retry.status === 401) {
      notifySessionExpired();
    }
    throw _uploadResultToError(retry);
  });
}

export function getDesignAsset(assetId: string): Promise<DesignAssetResponse> {
  return apiFetch<DesignAssetResponse>(`/api/design-assets/${encodeURIComponent(assetId)}`);
}

// Rapor/ekran görüntüsü desenleriyle aynı şekilde (bkz.
// `getReportHeatmapScreenshotUrl`), tenant izolasyonu backend'de doğrulanır;
// bu yalnızca göreli yolu mutlak URL'ye çevirir.
export function getDesignAssetPreviewUrl(assetId: string): string {
  return `${API_BASE_URL}/api/design-assets/${encodeURIComponent(assetId)}/preview`;
}

export function deleteDesignAsset(assetId: string): Promise<void> {
  return apiFetch<void>(`/api/design-assets/${encodeURIComponent(assetId)}`, { method: "DELETE" });
}

// --- Sayfa analizi (page-analyses) — DesignAsset ekran görüntüleri için yerel
// görsel analiz (Paket 4C+4D) ------------------------------------------------
//
// Bu analiz TAMAMEN yerel/deterministik OpenCV işlemesinden gelir (bkz.
// backend/app/services/image_visual_analysis.py); hiçbir gerçek kullanıcı
// tıklaması/göz takibi verisi DEĞİLDİR — yalnızca aday sıralama skoru ve
// piksel tabanlı sentetik dikkat tahminidir.

export type PageAnalysisStatus = "queued" | "running" | "succeeded" | "failed";

export interface VisualCtaCandidateBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface VisualCtaCandidateColor {
  r: number;
  g: number;
  b: number;
}

export interface VisualCtaCandidate {
  kind: "visual_cta_candidate";
  box: VisualCtaCandidateBox;
  // GERÇEK bir tıklama olasılığı veya istatistiksel güven değildir - yalnızca
  // aday SIRALAMA skorudur (bkz. docs/methodology.md).
  heuristic_score: number;
  dominant_color: VisualCtaCandidateColor;
  regional_visual_contrast_estimate: number;
  size_component: number;
  position_component: number;
  contrast_component: number;
  evidence: string[];
  algorithm_version: string;
}

export interface SyntheticAttentionCell {
  x: number;
  y: number;
  w: number;
  h: number;
  intensity: number;
}

export interface SyntheticAttentionEstimate {
  cells: SyntheticAttentionCell[];
  feature_source: string;
  algorithm_version: string;
  disclaimer: string;
}

export interface DesignAssetVisualFeatures {
  feature_source: "visual_heuristic";
  algorithm_version: string;
  visual_cta_candidates: VisualCtaCandidate[];
  candidate_disclaimer: string;
  synthetic_attention_estimate: SyntheticAttentionEstimate;
  limitations: string[];
}

export interface PageAnalysisResponse {
  id: string;
  organization_id: string;
  source_kind: "url" | "design_asset";
  url: string | null;
  design_asset_id: string | null;
  design_asset_still_linked: boolean | null;
  status: PageAnalysisStatus;
  attempt_count: number;
  error: string | null;
  snapshot_version: string | null;
  analyzer_version: string | null;
  source: string | null;
  features: DesignAssetVisualFeatures | Record<string, unknown> | null;
  has_screenshot: boolean;
  image_width: number | null;
  image_height: number | null;
  screenshot_content_type: string | null;
  content_sha256: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export function createPageAnalysisForDesignAsset(
  designAssetId: string,
): Promise<PageAnalysisResponse> {
  return apiFetch<PageAnalysisResponse>("/api/page-analyses", {
    method: "POST",
    body: JSON.stringify({ design_asset_id: designAssetId }),
  });
}

export function getPageAnalysis(analysisId: string): Promise<PageAnalysisResponse> {
  return apiFetch<PageAnalysisResponse>(`/api/page-analyses/${encodeURIComponent(analysisId)}`);
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
  // Organizasyon-ici `role`den TAMAMEN AYRI: platform genelinde yonetici mi.
  // Yalnizca UI amaclidir (ornegin bir yonetici menusunu gostermek icin) -
  // gercek yetkilendirme her zaman backend'de dogrudan veritabanindan yapilir.
  is_platform_admin: boolean;
  // Herkese acik canli demo hesabi kalici veri degistiremez.
  is_demo?: boolean;
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

// Parola gerektirmeden herkese acik "Canli demo" hesabina tek-tik giris.
// Yalnizca sunucuda DEMO_LOGIN_ENABLED acikken calisir; aksi halde 404 doner.
export function demoLogin(): Promise<SessionResponse> {
  return apiFetch<SessionResponse>("/api/auth/demo-login", { method: "POST" });
}

export function refreshSession(): Promise<SessionResponse> {
  return rawFetch<SessionResponse>("/api/auth/refresh", { method: "POST" });
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
