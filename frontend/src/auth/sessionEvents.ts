const SESSION_EXPIRED_EVENT = "synthetix:session-expired";

/** Bir API cagrisi (auth uc noktalari disinda) 401 ile basarisiz oldugunda cagrilir. */
export function notifySessionExpired(): void {
  window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
}

export function onSessionExpired(handler: () => void): () => void {
  window.addEventListener(SESSION_EXPIRED_EVENT, handler);
  return () => window.removeEventListener(SESSION_EXPIRED_EVENT, handler);
}
