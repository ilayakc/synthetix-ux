import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { type ThemePreference, getMySettings } from "../api/client";

interface ThemeContextValue {
  persistedTheme: ThemePreference;
  previewTheme: (theme: ThemePreference) => void;
  markPersisted: (theme: ThemePreference) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function applyThemeToDom(theme: ThemePreference) {
  if (theme === "system") {
    document.documentElement.removeAttribute("data-theme");
  } else {
    document.documentElement.setAttribute("data-theme", theme);
  }
}

/**
 * Kimlik dogrulanmis kullanicinin tema tercihini uygular. `previewTheme`
 * Ayarlar sayfasindaki aninda onizleme icindir; kalici kayit hala normal
 * Kaydet akisi (PATCH /api/settings/me) uzerinden yapilir - bu context
 * yalnizca DOM'a `data-theme` uygular (bkz. styles/tokens.css).
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [persistedTheme, setPersistedTheme] = useState<ThemePreference>("system");
  const hydrated = useRef(false);

  useEffect(() => {
    let cancelled = false;
    getMySettings()
      .then((settings) => {
        if (cancelled) return;
        setPersistedTheme(settings.theme);
        applyThemeToDom(settings.theme);
      })
      .catch(() => {
        // Oturum henuz gecerli degilse ya da istek basarisiz olursa sistem
        // temasinda kalinir (mevcut prefers-color-scheme davranisi).
      })
      .finally(() => {
        hydrated.current = true;
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const previewTheme = useCallback((theme: ThemePreference) => {
    applyThemeToDom(theme);
  }, []);

  const markPersisted = useCallback((theme: ThemePreference) => {
    setPersistedTheme(theme);
    applyThemeToDom(theme);
  }, []);

  return (
    <ThemeContext.Provider value={{ persistedTheme, previewTheme, markPersisted }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("useTheme, ThemeProvider icinde kullanilmalidir");
  }
  return context;
}
