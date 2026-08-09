import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getUsageSummary } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ChipCoinIcon, CloseIcon, LogOutIcon, MenuIcon } from "./icons";
import BrandLogo from "./BrandLogo";

interface TopbarProps {
  isMenuOpen: boolean;
  onToggleMenu: () => void;
}

export default function Topbar({ isMenuOpen, onToggleMenu }: TopbarProps) {
  const { session, logout } = useAuth();
  const navigate = useNavigate();
  const [chipBalance, setChipBalance] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    getUsageSummary()
      .then((data) => {
        if (!cancelled) setChipBalance(data.chip_balance);
      })
      .catch(() => {
        // Chip bakiyesi ust cubukta gosterilemezse sessizce atlanir; Kullanim
        // ve Chip sayfasi kendi hata/tekrar-dene durumunu ayrica gosterir.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleLogout() {
    await logout();
    navigate("/giris", { replace: true });
  }

  const initial = (session?.display_name?.trim()?.[0] ?? session?.email[0] ?? "?").toUpperCase();

  return (
    <header className="topbar">
      <button
        type="button"
        className="menu-toggle"
        aria-label={isMenuOpen ? "Menüyü kapat" : "Menüyü aç"}
        aria-expanded={isMenuOpen}
        aria-controls="primary-navigation"
        onClick={onToggleMenu}
      >
        {isMenuOpen ? <CloseIcon /> : <MenuIcon />}
      </button>

      <BrandLogo className="topbar__brand" />

      <div className="topbar__actions">
        {chipBalance !== null && (
          <Link to="/kullanim-ve-chip" className="topbar__chip-pill" title="Çip Cüzdanı bakiyeniz">
            <ChipCoinIcon />
            <span>{chipBalance} Chip</span>
          </Link>
        )}

        {session && (
          <div className="topbar__account">
            <span className="topbar__avatar" aria-hidden="true">
              {initial}
            </span>
            <span className="topbar__identity">
              <strong>{session.display_name}</strong>
              <small>{session.organization_name}</small>
            </span>
            <button
              type="button"
              className="topbar__logout"
              aria-label="Çıkış yap"
              title="Çıkış yap"
              onClick={handleLogout}
            >
              <LogOutIcon />
              <span>Çıkış yap</span>
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
