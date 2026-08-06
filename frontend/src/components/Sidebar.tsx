import { type ComponentType, useEffect, useRef } from "react";
import { NavLink } from "react-router-dom";
import {
  ActivityIcon,
  ChipCoinIcon,
  FileTextIcon,
  FolderIcon,
  GearIcon,
  HelpCircleIcon,
  HomeIcon,
  LayersIcon,
  UsersIcon,
} from "./icons";

interface NavItem {
  to: string;
  label: string;
  end?: boolean;
  icon: ComponentType<{ className?: string }>;
}

// Ana işlemler, tekrar eden bir "Yeni Test" CTA'sı olmadan tek listede sunulur.
const PRIMARY_NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Genel Bakış", end: true, icon: HomeIcon },
  { to: "/projeler", label: "Projeler", icon: FolderIcon },
  { to: "/simulasyonlar", label: "Simülasyonlar", icon: ActivityIcon },
  { to: "/raporlar", label: "Raporlar", icon: FileTextIcon },
  { to: "/kullanim-ve-chip", label: "Çip Cüzdanı", icon: ChipCoinIcon },
];

const TOOLS_NAV_ITEMS: NavItem[] = [
  { to: "/personalar", label: "Personalar", icon: UsersIcon },
  { to: "/analiz-modulleri", label: "Analiz Modülleri", icon: LayersIcon },
];

const FOOTER_NAV_ITEMS: NavItem[] = [
  { to: "/ayarlar", label: "Ayarlar", icon: GearIcon },
  { to: "/yardim", label: "Yardım", icon: HelpCircleIcon },
];

function NavList({
  items,
  onNavigate,
  labelledBy,
}: {
  items: NavItem[];
  onNavigate: () => void;
  labelledBy?: string;
}) {
  return (
    <ul aria-labelledby={labelledBy}>
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <li key={item.to}>
            <NavLink to={item.to} end={item.end} onClick={onNavigate}>
              <Icon />
              <span>{item.label}</span>
            </NavLink>
          </li>
        );
      })}
    </ul>
  );
}

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
        <NavList items={PRIMARY_NAV_ITEMS} onNavigate={onClose} />

        <div className="sidebar__nav-group">
          <p className="sidebar__nav-label" id="sidebar-tools-label">
            Araçlar
          </p>
          <NavList items={TOOLS_NAV_ITEMS} onNavigate={onClose} labelledBy="sidebar-tools-label" />
        </div>

        <div className="sidebar__nav-group sidebar__nav-group--footer">
          <NavList items={FOOTER_NAV_ITEMS} onNavigate={onClose} />
        </div>
      </nav>
    </aside>
  );
}
