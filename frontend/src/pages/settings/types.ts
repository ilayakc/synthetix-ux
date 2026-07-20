import type { OrganizationSettingsResponse, MeSettingsResponse } from "../../api/client";

export type MeDraft = Pick<
  MeSettingsResponse,
  | "display_name"
  | "language"
  | "timezone"
  | "theme"
  | "compact_view"
  | "notify_simulation_completed"
  | "notify_simulation_failed"
  | "notify_report_ready"
  | "notify_low_chip_balance"
  | "low_chip_balance_threshold"
>;

export type OrgDraft = Pick<
  OrganizationSettingsResponse,
  | "name"
  | "currency"
  | "default_persona_count"
  | "default_persona_preset_id"
  | "default_device_profile"
  | "default_modules"
  | "default_target_audience"
>;

export function meDraftFrom(response: MeSettingsResponse): MeDraft {
  return {
    display_name: response.display_name,
    language: response.language,
    timezone: response.timezone,
    theme: response.theme,
    compact_view: response.compact_view,
    notify_simulation_completed: response.notify_simulation_completed,
    notify_simulation_failed: response.notify_simulation_failed,
    notify_report_ready: response.notify_report_ready,
    notify_low_chip_balance: response.notify_low_chip_balance,
    low_chip_balance_threshold: response.low_chip_balance_threshold,
  };
}

export function orgDraftFrom(response: OrganizationSettingsResponse): OrgDraft {
  return {
    name: response.name,
    currency: response.currency,
    default_persona_count: response.default_persona_count,
    default_persona_preset_id: response.default_persona_preset_id,
    default_device_profile: response.default_device_profile,
    default_modules: response.default_modules,
    default_target_audience: response.default_target_audience,
  };
}

export const DEVICE_PROFILE_LABELS: Record<string, string> = {
  desktop: "Masaüstü",
  mobile: "Mobil",
  tablet: "Tablet",
};

export const CURRENCY_LABELS: Record<string, string> = {
  TRY: "Türk Lirası (TRY)",
  USD: "ABD Doları (USD)",
  EUR: "Euro (EUR)",
};
