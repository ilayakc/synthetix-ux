import { useEffect, useRef } from "react";
import { Link, NavLink } from "react-router-dom";
import {
  ActivityIcon,
  ChipCoinIcon,
  FileTextIcon,
  FolderIcon,
  GearIcon,
  HelpCircleIcon,
  HomeIcon,
  LayersIcon,
  PlusIcon,
  UsersIcon,
} from "./icons";

const NAV_ITEMS = [
  { to: "/", label: "Genel Bakış", end: true, icon: HomeIcon },
  { to: "/projeler", label: "Projeler", icon: FolderIcon },
  { to: "/tests/new", label: "Yeni Test", icon: PlusIcon },
  { to: "/personalar", label: "Personalar", icon: UsersIcon },
  { to: "/analiz-modulleri", label: "Analiz Modülleri", icon: LayersIcon },
  { to: "/simulasyonlar", label: "Simülasyonlar", icon: ActivityIcon },
  { to: "/raporlar", label: "Raporlar", icon: FileTextIcon },
  { to: "/kullanim-ve-chip", label: "Kullanım ve Chip", icon: ChipCoinIcon },
  { to: "/ayarlar", label: "Ayarlar", icon: GearIcon },
  { to: "/yardim", label: "Yardım", icon: HelpCircleIcon },
];

interface SidebarProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function Sidebar({ isOpen, onClose }: SidebarProps) {
  const asideRef = useRef<HTMLElement | null>(null);

  // Mobil cekmece acikken: Escape ile kapanir ve Tab odagi menu disina
  // kacmaz (bkz. gereksinim - "klavye odagi menu disina kacmasin"). Masaustunde
  // `isOpen` zaten hep false kaldigindan (hamburger CSS ile gizlenir) bu
  // dinleyici devreye girmez.
  useEffect(() => {
    if (!isOpen) return;

    const asideEl = asideRef.current;
    const focusable = asideEl?.querySelectorAll<HTMLElement>("a[href], button:not([disabled])");
    focusable?.[0]?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  return (
    <aside
      id="primary-navigation"
      ref={asideRef}
      className={`sidebar${isOpen ? " is-open" : ""}`}
      aria-label="Ana menü"
    >
      <div className="sidebar__brand">
        <span className="sidebar__brand-mark" aria-hidden="true">
          SX
        </span>
        <span className="sidebar__brand-text">
          <span className="sidebar__brand-name">Synthetix UX</span>
          <span className="sidebar__brand-tagline">Sentetik kullanıcı araştırma platformu</span>
        </span>
      </div>

      <nav className="sidebar__nav">
        <ul>
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <li key={item.to}>
                <NavLink to={item.to} end={item.end} onClick={onClose}>
                  <Icon />
                  <span>{item.label}</span>
                </NavLink>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="sidebar__footer">
        <Link to="/tests/new" className="sidebar__cta" onClick={onClose}>
          <PlusIcon />
          Yeni Test Başlat
        </Link>
      </div>
    </aside>
  );
}
