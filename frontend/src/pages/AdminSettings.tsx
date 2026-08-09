import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  type AdminPlatformSettingsResponse,
  type ReadyResponse,
  type ThemePreference,
  getAdminPlatformSettings,
  getReadiness,
  updateMySettings,
} from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { useTheme } from "../theme/ThemeContext";

const THEME_OPTIONS: Array<{
  value: ThemePreference;
  title: string;
  description: string;
}> = [
  { value: "system", title: "Sistem", description: "Cihazınızın görünüm tercihini izler." },
  { value: "light", title: "Açık", description: "Aydınlık ve ferah bir görünüm kullanır." },
  { value: "dark", title: "Koyu", description: "Düşük ışıkta daha rahat bir görünüm sunar." },
];

function StatusValue({ ok, yes, no }: { ok: boolean; yes: string; no: string }) {
  return (
    <span className={`admin-setting-status ${ok ? "is-ok" : "is-warning"}`}>{ok ? yes : no}</span>
  );
}

export default function AdminSettings() {
  const { session } = useAuth();
  const { persistedTheme, previewTheme, markPersisted } = useTheme();
  const [settings, setSettings] = useState<AdminPlatformSettingsResponse | null>(null);
  const [readiness, setReadiness] = useState<ReadyResponse | null>(null);
  const [theme, setTheme] = useState<ThemePreference>(persistedTheme);
  const [error, setError] = useState<string | null>(null);
  const [themeMessage, setThemeMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSavingTheme, setIsSavingTheme] = useState(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [settingsResponse, readinessResponse] = await Promise.all([
        getAdminPlatformSettings(),
        getReadiness(),
      ]);
      setSettings(settingsResponse);
      setReadiness(readinessResponse);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Yönetim ayarları yüklenemedi.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setTheme(persistedTheme);
  }, [persistedTheme]);

  function handleThemeSelection(nextTheme: ThemePreference) {
    setTheme(nextTheme);
    setThemeMessage(null);
    previewTheme(nextTheme);
  }

  function handleThemeDiscard() {
    setTheme(persistedTheme);
    setThemeMessage(null);
    previewTheme(persistedTheme);
  }

  async function handleThemeSave() {
    setIsSavingTheme(true);
    setThemeMessage(null);
    try {
      const updated = await updateMySettings({ theme });
      markPersisted(updated.theme);
      setTheme(updated.theme);
      setThemeMessage("Görünüm tercihiniz kaydedildi.");
    } catch (err) {
      previewTheme(persistedTheme);
      setTheme(persistedTheme);
      setThemeMessage(err instanceof ApiError ? err.message : "Görünüm tercihi kaydedilemedi.");
    } finally {
      setIsSavingTheme(false);
    }
  }

  return (
    <section className="admin-dashboard" aria-labelledby="admin-settings-heading">
      <header className="admin-dashboard__header">
        <div>
          <p className="admin-dashboard__eyebrow">Yönetici tercihleri</p>
          <h1 id="admin-settings-heading" className="page-heading">
            Ayarlar
          </h1>
          <p className="page-placeholder">
            Hesap bilgilerinizi görüntüleyin ve yönetim panelinin görünümünü kişiselleştirin.
          </p>
        </div>
      </header>

      {error && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}

      <section className="admin-settings-section" aria-labelledby="appearance-heading">
        <div className="admin-settings-section__header">
          <div>
            <h2 id="appearance-heading">Görünüm</h2>
            <p>Size en rahat gelen panel temasını seçin.</p>
          </div>
        </div>

        <div className="admin-theme-options" role="radiogroup" aria-label="Panel görünümü">
          {THEME_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={theme === option.value}
              className={`admin-theme-option${theme === option.value ? " is-selected" : ""}`}
              onClick={() => handleThemeSelection(option.value)}
            >
              <span className={`admin-theme-option__preview is-${option.value}`} aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
              <span className="admin-theme-option__copy">
                <strong>{option.title}</strong>
                <small>{option.description}</small>
              </span>
              <span className="admin-theme-option__check" aria-hidden="true" />
            </button>
          ))}
        </div>

        <div className="admin-settings-save-row">
          <div>
            {theme !== persistedTheme && (
              <span className="status-badge status-badge--draft" role="status">
                Kaydedilmemiş değişiklikler var
              </span>
            )}
            {themeMessage && <span role="status">{themeMessage}</span>}
          </div>
          <div className="settings-action-bar__buttons">
            <button
              type="button"
              className="btn-secondary"
              onClick={handleThemeDiscard}
              disabled={isSavingTheme || theme === persistedTheme}
            >
              Değişiklikleri geri al
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={() => void handleThemeSave()}
              disabled={isSavingTheme || theme === persistedTheme}
            >
              {isSavingTheme ? "Kaydediliyor…" : "Kaydet"}
            </button>
          </div>
        </div>
      </section>

      <div className="admin-settings-grid admin-settings-grid--information">
        <article className="admin-settings-card">
          <h2>Yönetici hesabı</h2>
          <p className="admin-settings-copy">
            Bu hesap platform genelindeki talepleri, AI işlemlerini ve denetim kayıtlarını yönetme
            yetkisine sahiptir.
          </p>
          <dl>
            <div>
              <dt>Ad</dt>
              <dd>{session?.display_name ?? "Yönetici"}</dd>
            </div>
            <div>
              <dt>E-posta</dt>
              <dd>{session?.email ?? "—"}</dd>
            </div>
            <div>
              <dt>Yetki</dt>
              <dd>Platform yöneticisi</dd>
            </div>
          </dl>
        </article>

        <article className="admin-settings-card">
          <h2>Yönetim alanı</h2>
          <p className="admin-settings-copy">
            Chip Talepleri bölümünden bakiye isteklerini sonuçlandırabilir, AI İşlemleri bölümünden
            rapor üretim durumunu izleyebilir ve İşlem Kayıtları bölümünden yönetici hareketlerini
            denetleyebilirsiniz.
          </p>
          <p className="admin-settings-note">
            Kritik işlemler güvenlik amacıyla kayıt altına alınır. Yönetici yetkileri normal
            kullanıcı çalışma alanlarından ayrıdır.
          </p>
        </article>
      </div>

      {isLoading && !settings ? (
        <p className="page-placeholder admin-settings-loading" role="status">
          Sistem bilgileri yükleniyor…
        </p>
      ) : settings ? (
        <details className="admin-settings-details">
          <summary>Sistem bilgilerini görüntüle</summary>
          <div className="admin-settings-details__header">
            <p>Bu bilgiler sistemin çalışmasını kontrol etmek içindir ve gizli anahtar içermez.</p>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void load()}
              disabled={isLoading}
            >
              {isLoading ? "Kontrol ediliyor…" : "Durumu yenile"}
            </button>
          </div>
          <div className="admin-settings-grid">
            <article className="admin-settings-card">
              <h2>Sistem durumu</h2>
              <dl>
                <div>
                  <dt>Çalışma ortamı</dt>
                  <dd>{settings.environment}</dd>
                </div>
                <div>
                  <dt>Veritabanı</dt>
                  <dd>
                    <StatusValue ok={readiness?.database === "ok"} yes="Hazır" no="Erişilemiyor" />
                  </dd>
                </div>
                <div>
                  <dt>Redis</dt>
                  <dd>
                    <StatusValue ok={readiness?.redis === "ok"} yes="Hazır" no="Erişilemiyor" />
                  </dd>
                </div>
              </dl>
            </article>

            <article className="admin-settings-card">
              <h2>AI raporlama</h2>
              <dl>
                <div>
                  <dt>Modül</dt>
                  <dd>
                    <StatusValue ok={settings.ai_report.enabled} yes="Etkin" no="Devre dışı" />
                  </dd>
                </div>
                <div>
                  <dt>Sağlayıcı</dt>
                  <dd>{settings.ai_report.provider}</dd>
                </div>
                <div>
                  <dt>Hazırlık</dt>
                  <dd>
                    <StatusValue
                      ok={settings.ai_report.provider_ready}
                      yes="İşlemeye hazır"
                      no="Yapılandırma gerekli"
                    />
                  </dd>
                </div>
              </dl>
            </article>

            <article className="admin-settings-card admin-settings-card--wide">
              <h2>Güvenlik ve veri saklama</h2>
              <dl>
                <div>
                  <dt>Erişim oturumu</dt>
                  <dd>{settings.security.access_token_ttl_minutes} dakika</dd>
                </div>
                <div>
                  <dt>Yenileme oturumu</dt>
                  <dd>{settings.security.refresh_token_ttl_days} gün</dd>
                </div>
                <div>
                  <dt>Rapor ekran görüntüsü saklama</dt>
                  <dd>{settings.operations.report_screenshot_retention_days} gün</dd>
                </div>
              </dl>
              <p className="admin-settings-note">
                Sunucu ayarları başlangıçta ortam değişkenlerinden okunur. Gizli anahtarlar bu
                ekranda gösterilmez ve buradan değiştirilemez.
              </p>
            </article>
          </div>
        </details>
      ) : null}
    </section>
  );
}
