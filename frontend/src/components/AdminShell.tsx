import { type ReactNode, useEffect, useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { type AdminSummaryResponse, getAdminSummary } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import BrandLogo from "./BrandLogo";
import {
  ActivityIcon,
  BellIcon,
  ChipCoinIcon,
  CloseIcon,
  FileTextIcon,
  GearIcon,
  MenuIcon,
  ShieldCheckIcon,
} from "./icons";

const ADMIN_NAV_ITEMS = [
  { to: "/yonetim", label: "Yönetim Özeti", icon: ShieldCheckIcon, end: true },
  { to: "/yonetim/chip-talepleri", label: "Chip Talepleri", icon: ChipCoinIcon, end: false },
  { to: "/yonetim/ai-islemleri", label: "AI İşlemleri", icon: ActivityIcon, end: false },
  { to: "/yonetim/islem-kayitlari", label: "İşlem Kayıtları", icon: FileTextIcon, end: false },
] as const;

const ADMIN_SETTINGS_ITEM = { to: "/yonetim/ayarlar", label: "Ayarlar", icon: GearIcon } as const;

export default function AdminShell({ children }: { children: ReactNode }) {
  const { session, logout } = useAuth();
  const navigate = useNavigate();
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isNotificationsOpen, setIsNotificationsOpen] = useState(false);
  const [notificationSummary, setNotificationSummary] = useState<AdminSummaryResponse | null>(null);

  useEffect(() => {
    let cancelled = false;

    const loadNotifications = () => {
      getAdminSummary()
        .then((summary) => {
          if (!cancelled) setNotificationSummary(summary);
        })
        .catch(() => {
          // Üst çubuktaki bildirim özeti yüklenemezse yönetim sayfaları kendi
          // hata durumlarını göstermeye devam eder; gezinme engellenmez.
        });
    };

    loadNotifications();
    const intervalId = window.setInterval(loadNotifications, 30_000);
    window.addEventListener("focus", loadNotifications);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
      window.removeEventListener("focus", loadNotifications);
    };
  }, []);

  async function handleLogout() {
    await logout();
    navigate("/yonetici-girisi", { replace: true });
  }

  const pendingTopUps = notificationSummary?.pending_topup_count ?? 0;
  const failedAiPipelines = notificationSummary?.failed_ai_pipeline_count ?? 0;
  const notificationCount = pendingTopUps + failedAiPipelines;

  return (
    <div className="admin-shell">
      <a href="#admin-main-content" className="skip-link">
        İçeriğe geç
      </a>

      <header className="admin-topbar">
        <button
          type="button"
          className="admin-menu-toggle"
          aria-label={isMenuOpen ? "Yönetim menüsünü kapat" : "Yönetim menüsünü aç"}
          aria-expanded={isMenuOpen}
          onClick={() => setIsMenuOpen((current) => !current)}
        >
          {isMenuOpen ? <CloseIcon /> : <MenuIcon />}
        </button>
        <div className="admin-topbar__brand">
          <BrandLogo />
          <span className="admin-topbar__section">Yönetim</span>
        </div>
        <div className="admin-topbar__account">
          <div className="admin-notifications">
            <button
              type="button"
              className="admin-notifications__trigger"
              aria-label={`Bildirimler${notificationCount > 0 ? `, ${notificationCount} işlem bekliyor` : ""}`}
              aria-expanded={isNotificationsOpen}
              onClick={() => setIsNotificationsOpen((current) => !current)}
            >
              <BellIcon />
              {notificationCount > 0 && (
                <span className="admin-notifications__badge" aria-hidden="true">
                  {notificationCount > 99 ? "99+" : notificationCount}
                </span>
              )}
            </button>

            {isNotificationsOpen && (
              <div
                className="admin-notifications__panel"
                role="dialog"
                aria-label="Yönetim bildirimleri"
              >
                <div className="admin-notifications__header">
                  <strong>Bildirimler</strong>
                  <span>
                    {notificationCount > 0 ? `${notificationCount} işlem bekliyor` : "Güncel"}
                  </span>
                </div>
                {notificationCount === 0 ? (
                  <p className="admin-notifications__empty">
                    İnceleme bekleyen yeni bir işlem yok.
                  </p>
                ) : (
                  <div className="admin-notifications__list">
                    {pendingTopUps > 0 && (
                      <Link
                        to="/yonetim/chip-talepleri"
                        onClick={() => setIsNotificationsOpen(false)}
                      >
                        <span className="admin-notifications__icon">
                          <ChipCoinIcon />
                        </span>
                        <span>
                          <strong>{pendingTopUps} Chip talebi onay bekliyor</strong>
                          <small>Talepleri incelemek için açın.</small>
                        </span>
                      </Link>
                    )}
                    {failedAiPipelines > 0 && (
                      <Link
                        to="/yonetim/ai-islemleri"
                        onClick={() => setIsNotificationsOpen(false)}
                      >
                        <span className="admin-notifications__icon">
                          <ActivityIcon />
                        </span>
                        <span>
                          <strong>{failedAiPipelines} AI işlemi inceleme gerektiriyor</strong>
                          <small>Başarısız işlemlerin ayrıntılarını açın.</small>
                        </span>
                      </Link>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
          <span className="topbar__avatar" aria-label="Yönetici profili">
            Y
          </span>
          <span className="admin-topbar__identity">
            <strong>{session?.display_name ?? "Yönetici"}</strong>
            <small>Platform yöneticisi</small>
          </span>
          <button type="button" className="topbar__logout" onClick={handleLogout}>
            Çıkış yap
          </button>
        </div>
      </header>

      <div className="admin-shell__body">
        <aside
          className={`admin-sidebar${isMenuOpen ? " is-open" : ""}`}
          aria-label="Yönetim menüsü"
        >
          <div className="admin-sidebar__heading">
            <span>Platform Yönetimi</span>
          </div>
          <nav>
            <ul>
              {ADMIN_NAV_ITEMS.map((item) => {
                const Icon = item.icon;
                return (
                  <li key={item.to}>
                    <NavLink to={item.to} end={item.end} onClick={() => setIsMenuOpen(false)}>
                      <Icon />
                      <span>{item.label}</span>
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          </nav>
          <div className="admin-sidebar__bottom">
            <div className="admin-sidebar__notice">
              <ShieldCheckIcon />
              <p>Bu alandaki işlemler yönetici yetkisi ve denetim kaydı gerektirir.</p>
            </div>
            <nav className="admin-sidebar__footer" aria-label="Yönetim ayarları">
              <ul>
                <li>
                  <NavLink to={ADMIN_SETTINGS_ITEM.to} onClick={() => setIsMenuOpen(false)}>
                    <ADMIN_SETTINGS_ITEM.icon />
                    <span>{ADMIN_SETTINGS_ITEM.label}</span>
                  </NavLink>
                </li>
              </ul>
            </nav>
          </div>
        </aside>
        <button
          type="button"
          className={`admin-sidebar-backdrop${isMenuOpen ? " is-open" : ""}`}
          aria-label="Yönetim menüsü dışına tıkla ve kapat"
          onClick={() => setIsMenuOpen(false)}
        />
        <main id="admin-main-content" className="admin-main-content" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
