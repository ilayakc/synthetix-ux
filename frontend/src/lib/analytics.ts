import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { recordAnalyticsPageView } from "../api/client";

// Gizlilik dostu analitik izni (consent) + sayfa görüntüleme takibi.
//
// - İzin `localStorage`'da tutulur (zorunlu olmayan analitik çerezi ile uyumlu).
// - Kullanıcı reddederse HİÇBİR page_view gönderilmez (pazarlama analitiği
//   üretilmez). İzin verirse `consent: true` gönderilir; karar verilmemişse
//   `consent: false` gönderilir ve backend politikası (ANALYTICS_REQUIRE_CONSENT)
//   kaydın işlenip işlenmeyeceğine karar verir.
// - Hassas veri gönderilmez: yalnızca yol + referrer + UTM/ref; IP/parmak izi yok.

const CONSENT_KEY = "analytics_consent";

export type ConsentState = "granted" | "denied" | null;

const listeners = new Set<() => void>();

export function getConsent(): ConsentState {
  try {
    const value = localStorage.getItem(CONSENT_KEY);
    return value === "granted" || value === "denied" ? value : null;
  } catch {
    return null;
  }
}

export function setConsent(value: "granted" | "denied"): void {
  try {
    localStorage.setItem(CONSENT_KEY, value);
  } catch {
    // localStorage erişilemezse sessizce yut (analitik zorunlu değildir).
  }
  for (const listener of listeners) listener();
}

function subscribeConsent(callback: () => void): () => void {
  listeners.add(callback);
  return () => {
    listeners.delete(callback);
  };
}

export function useConsent(): ConsentState {
  const [state, setState] = useState<ConsentState>(() => getConsent());
  useEffect(() => subscribeConsent(() => setState(getConsent())), []);
  return state;
}

function parseUtm(search: string) {
  const params = new URLSearchParams(search);
  return {
    utm_source: params.get("utm_source"),
    utm_medium: params.get("utm_medium"),
    utm_campaign: params.get("utm_campaign"),
    utm_content: params.get("utm_content"),
    utm_term: params.get("utm_term"),
    ref: params.get("ref"),
  };
}

function newEventId(): string | undefined {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
  } catch {
    // yut
  }
  return undefined;
}

// App içinde TEK bir yerde çağrılır (bkz. App.tsx). Her rota değişiminde bir
// page_view gönderir. İzin reddedildiyse hiç göndermez.
export function useAnalyticsPageViews(): void {
  const location = useLocation();
  const lastNavRef = useRef<string | null>(null);

  useEffect(() => {
    const consent = getConsent();
    if (consent === "denied") return;

    // StrictMode'un efekti iki kez çalıştırmasına karşı: aynı navigasyon için
    // yalnızca bir kez gönder (event_id ile birlikte backend zaten dedup eder).
    const navKey = `${location.key}:${location.pathname}:${location.search}`;
    if (lastNavRef.current === navKey) return;
    lastNavRef.current = navKey;

    recordAnalyticsPageView({
      path: location.pathname,
      referrer: typeof document !== "undefined" ? document.referrer || null : null,
      consent: consent === "granted",
      event_id: newEventId(),
      ...parseUtm(location.search),
    });
  }, [location.key, location.pathname, location.search]);
}
