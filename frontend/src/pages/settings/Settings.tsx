import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import {
  ApiError,
  type AnalysisModuleDefinition,
  type MeSettingsResponse,
  type OrganizationSettingsResponse,
  type PatchMeSettingsRequest,
  type PatchOrganizationSettingsRequest,
  type PersonaPresetResponse,
  getAnalysisModuleCatalog,
  getMySettings,
  getOrganizationSettings,
  listPersonaPresets,
  updateMySettings,
  updateOrganizationSettings,
} from "../../api/client";
import { useTheme } from "../../theme/ThemeContext";
import AppearanceTab from "./AppearanceTab";
import CompanyTab from "./CompanyTab";
import ProfileTab from "./ProfileTab";
import TestDefaultsTab from "./TestDefaultsTab";
import { type MeDraft, type OrgDraft, meDraftFrom, orgDraftFrom } from "./types";

const TABS = [
  { id: "profile", label: "Profilim" },
  { id: "company", label: "Şirket" },
  { id: "appearance", label: "Görünüm ve Bildirimler" },
] as const;

type TabId = (typeof TABS)[number]["id"];

function shallowDiff<T extends object>(snapshot: T, draft: T): Partial<T> {
  const diff: Partial<T> = {};
  for (const key of Object.keys(draft) as (keyof T)[]) {
    const a = snapshot[key];
    const b = draft[key];
    if (Array.isArray(a) && Array.isArray(b)) {
      if (JSON.stringify(a) !== JSON.stringify(b)) diff[key] = b;
    } else if (a !== b) {
      diff[key] = b;
    }
  }
  return diff;
}

export default function Settings() {
  const { markPersisted, previewTheme } = useTheme();

  const [activeTab, setActiveTab] = useState<TabId>("profile");
  const tabRefs = useRef<Record<TabId, HTMLButtonElement | null>>({
    profile: null,
    company: null,
    appearance: null,
  });

  const [meSnapshot, setMeSnapshot] = useState<MeSettingsResponse | null>(null);
  const [meDraft, setMeDraft] = useState<MeDraft | null>(null);
  const [orgSnapshot, setOrgSnapshot] = useState<OrganizationSettingsResponse | null>(null);
  const [orgDraft, setOrgDraft] = useState<OrgDraft | null>(null);
  const [personaPresets, setPersonaPresets] = useState<PersonaPresetResponse[]>([]);
  const [moduleCatalog, setModuleCatalog] = useState<AnalysisModuleDefinition[]>([]);

  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const savedNoticeTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;

    Promise.all([
      getMySettings(),
      getOrganizationSettings(),
      listPersonaPresets(),
      getAnalysisModuleCatalog(),
    ])
      .then(([me, org, presets, catalog]) => {
        if (cancelled) return;
        setMeSnapshot(me);
        setMeDraft(meDraftFrom(me));
        setOrgSnapshot(org);
        setOrgDraft(orgDraftFrom(org));
        setPersonaPresets(presets);
        setModuleCatalog(catalog.modules);
      })
      .catch(() => {
        if (!cancelled) setLoadError("Ayarlar yüklenemedi.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return () => {
      if (savedNoticeTimeout.current) clearTimeout(savedNoticeTimeout.current);
    };
  }, []);

  const isMeDirty =
    meSnapshot && meDraft
      ? Object.keys(shallowDiff(meDraftFrom(meSnapshot), meDraft)).length > 0
      : false;
  const isOrgDirty =
    orgSnapshot && orgDraft
      ? Object.keys(shallowDiff(orgDraftFrom(orgSnapshot), orgDraft)).length > 0
      : false;
  const isDirty = isMeDirty || isOrgDirty;

  const handleMeChange = <K extends keyof MeDraft>(field: K, value: MeDraft[K]) => {
    setMeDraft((current) => (current ? { ...current, [field]: value } : current));
  };

  const handleOrgChange = <K extends keyof OrgDraft>(field: K, value: OrgDraft[K]) => {
    setOrgDraft((current) => (current ? { ...current, [field]: value } : current));
  };

  const showSavedNotice = () => {
    setSavedNotice("Ayarlar kaydedildi.");
    if (savedNoticeTimeout.current) clearTimeout(savedNoticeTimeout.current);
    savedNoticeTimeout.current = setTimeout(() => setSavedNotice(null), 3000);
  };

  const handleSave = async () => {
    if (!meSnapshot || !meDraft || !orgSnapshot || !orgDraft) return;
    setIsSaving(true);
    setSaveError(null);
    try {
      let nextMe = meSnapshot;
      let nextOrg = orgSnapshot;

      const meChanges = shallowDiff(meDraftFrom(meSnapshot), meDraft);
      if (Object.keys(meChanges).length > 0) {
        nextMe = await updateMySettings(meChanges as unknown as PatchMeSettingsRequest);
      }

      const orgChanges = shallowDiff(orgDraftFrom(orgSnapshot), orgDraft);
      if (Object.keys(orgChanges).length > 0) {
        // Backend'de bu iki alan icin bos metin ("") "temizle" anlamina gelir,
        // `null`/gonderilmemis alan "dokunma" anlamina gelir (bkz.
        // app/services/settings.py) - burada taslaktaki `null` degerini
        // acikca bos metne cevirmemiz gerekir, aksi halde kullanicinin
        // "Yok" secimi sessizce yok sayilir.
        const nullableStringFields: (keyof typeof orgChanges)[] = [
          "default_persona_preset_id",
          "default_device_profile",
          "default_target_audience",
        ];
        const requestBody: Record<string, unknown> = { ...orgChanges };
        for (const field of nullableStringFields) {
          if (field in orgChanges && requestBody[field] === null) {
            requestBody[field] = "";
          }
        }
        nextOrg = await updateOrganizationSettings(requestBody as PatchOrganizationSettingsRequest);
      }

      setMeSnapshot(nextMe);
      setMeDraft(meDraftFrom(nextMe));
      setOrgSnapshot(nextOrg);
      setOrgDraft(orgDraftFrom(nextOrg));
      markPersisted(nextMe.theme);
      showSavedNotice();
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Ayarlar kaydedilemedi.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDiscard = () => {
    if (!meSnapshot || !orgSnapshot) return;
    setMeDraft(meDraftFrom(meSnapshot));
    setOrgDraft(orgDraftFrom(orgSnapshot));
    previewTheme(meSnapshot.theme);
    setSaveError(null);
  };

  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const currentIndex = TABS.findIndex((tab) => tab.id === activeTab);
    let nextIndex: number | null = null;

    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % TABS.length;
    else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + TABS.length) % TABS.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = TABS.length - 1;

    if (nextIndex !== null) {
      event.preventDefault();
      const nextTab = TABS[nextIndex].id;
      setActiveTab(nextTab);
      tabRefs.current[nextTab]?.focus();
    }
  };

  if (isLoading) {
    return (
      <section aria-labelledby="settings-heading">
        <h1 id="settings-heading" className="page-heading">
          Ayarlar
        </h1>
        <p className="page-placeholder">Yükleniyor…</p>
      </section>
    );
  }

  if (loadError || !meSnapshot || !meDraft || !orgSnapshot || !orgDraft) {
    return (
      <section aria-labelledby="settings-heading">
        <h1 id="settings-heading" className="page-heading">
          Ayarlar
        </h1>
        <p className="auth-error" role="alert">
          {loadError ?? "Ayarlar yüklenemedi."}
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="settings-heading">
      <h1 id="settings-heading" className="page-heading">
        Ayarlar
      </h1>

      <div className="settings-action-bar">
        <div>
          {isDirty && (
            <span className="status-badge status-badge--draft" role="status">
              Kaydedilmemiş değişiklikler var
            </span>
          )}
          {savedNotice && (
            <p className="auth-notice" role="status">
              {savedNotice}
            </p>
          )}
          {saveError && (
            <p className="auth-error" role="alert">
              {saveError}
            </p>
          )}
        </div>
        <div className="settings-action-bar__buttons">
          <button
            type="button"
            className="btn-secondary"
            onClick={handleDiscard}
            disabled={!isDirty || isSaving}
          >
            Değişiklikleri geri al
          </button>
          <button
            type="button"
            className="auth-submit"
            onClick={handleSave}
            disabled={!isDirty || isSaving}
          >
            {isSaving ? "Kaydediliyor…" : "Kaydet"}
          </button>
        </div>
      </div>

      <div className="settings-tablist" role="tablist" aria-label="Ayarlar sekmeleri">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            ref={(el) => {
              tabRefs.current[tab.id] = el;
            }}
            id={`settings-tab-${tab.id}`}
            role="tab"
            type="button"
            aria-selected={activeTab === tab.id}
            aria-controls={`settings-panel-${tab.id}`}
            tabIndex={activeTab === tab.id ? 0 : -1}
            className={`settings-tab${activeTab === tab.id ? " settings-tab--active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
            onKeyDown={handleTabKeyDown}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div
        id="settings-panel-profile"
        role="tabpanel"
        aria-labelledby="settings-tab-profile"
        hidden={activeTab !== "profile"}
        className="settings-tabpanel"
      >
        <ProfileTab
          snapshot={meSnapshot}
          draft={meDraft}
          onChange={handleMeChange}
          disabled={isSaving}
        />
      </div>

      <div
        id="settings-panel-company"
        role="tabpanel"
        aria-labelledby="settings-tab-company"
        hidden={activeTab !== "company"}
        className="settings-tabpanel"
      >
        <CompanyTab
          snapshot={orgSnapshot}
          draft={orgDraft}
          onChange={handleOrgChange}
          savingDisabled={isSaving}
        />
        <details className="settings-advanced">
          <summary>Gelişmiş ayarlar</summary>
          <div className="settings-advanced__content">
            <h2>Test varsayılanları</h2>
            <p className="page-placeholder">
              Yeni test sihirbazında kullanılacak başlangıç değerlerini belirleyin.
            </p>
            <TestDefaultsTab
              snapshot={orgSnapshot}
              draft={orgDraft}
              onChange={handleOrgChange}
              savingDisabled={isSaving}
              personaPresets={personaPresets}
              moduleCatalog={moduleCatalog}
            />
          </div>
        </details>
      </div>

      <div
        id="settings-panel-appearance"
        role="tabpanel"
        aria-labelledby="settings-tab-appearance"
        hidden={activeTab !== "appearance"}
        className="settings-tabpanel"
      >
        <AppearanceTab draft={meDraft} onChange={handleMeChange} disabled={isSaving} />
      </div>
    </section>
  );
}
