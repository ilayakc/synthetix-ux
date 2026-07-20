import { type ReactNode, useState } from "react";
import IntegrityBanner from "./IntegrityBanner";
import Topbar from "./Topbar";
import Sidebar from "./Sidebar";

interface AppShellProps {
  children: ReactNode;
}

export default function AppShell({ children }: AppShellProps) {
  const [isMenuOpen, setIsMenuOpen] = useState(false);

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        İçeriğe geç
      </a>
      <IntegrityBanner />
      <Topbar isMenuOpen={isMenuOpen} onToggleMenu={() => setIsMenuOpen((open) => !open)} />
      <div className="app-body">
        <Sidebar isOpen={isMenuOpen} onClose={() => setIsMenuOpen(false)} />
        <div
          className={`sidebar-backdrop${isMenuOpen ? " is-open" : ""}`}
          onClick={() => setIsMenuOpen(false)}
          aria-hidden="true"
        />
        <main id="main-content" className="main-content" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
