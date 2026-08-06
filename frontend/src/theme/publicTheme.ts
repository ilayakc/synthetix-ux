export type PublicTheme = "light" | "dark";

const PUBLIC_THEME_STORAGE_KEY = "synthetix-public-theme";

export function getPublicTheme(): PublicTheme {
  const stored = window.localStorage.getItem(PUBLIC_THEME_STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyPublicTheme(theme: PublicTheme): void {
  document.documentElement.setAttribute("data-theme", theme);
  window.localStorage.setItem(PUBLIC_THEME_STORAGE_KEY, theme);
}

export function initializePublicTheme(): void {
  applyPublicTheme(getPublicTheme());
}

export function clearPublicThemeForTests(): void {
  window.localStorage.removeItem(PUBLIC_THEME_STORAGE_KEY);
}
