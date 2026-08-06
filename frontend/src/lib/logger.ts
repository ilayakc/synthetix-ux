// Merkezi, yapilandirilmis loglama yardimcisi.
//
// Backend'deki `app.logging_config`/`app.logging_utils` ile AYNI okunabilir
// formati kullanir: `HH:MM:SS | SEVIYE   | kategori   | mesaj`. Tum
// sayfalarda/servislerde tekrar tekrar konsol renklendirme/format kodu
// yazmak yerine buradaki `logger` kullanilir.
//
// GUVENLIK: `logger.*` cagrilarina asla sifre, token, cookie, Authorization
// header'i, API anahtari veya istek/yanit govdesinin TAMAMI GECIRILMEMELIDIR
// - bkz. `maskSensitive` (gerektiginde bir nesneyi loglamadan once maskeler).

export type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";

const LEVEL_ORDER: Record<LogLevel, number> = {
  DEBUG: 0,
  INFO: 1,
  WARNING: 2,
  ERROR: 3,
  CRITICAL: 4,
};

// Tarayici konsolu renkleri (bkz. proje talimati): DEBUG gri, INFO yesil,
// WARNING turuncu/sari, ERROR kirmizi, CRITICAL kirmizi arka plan.
const LEVEL_STYLES: Record<LogLevel, string> = {
  DEBUG: "color: #888888",
  INFO: "color: #2e7d32",
  WARNING: "color: #ed6c02",
  ERROR: "color: #d32f2f",
  CRITICAL: "color: #ffffff; background-color: #d32f2f; padding: 0 2px",
};

const LEVEL_WIDTH = 8;
const CATEGORY_WIDTH = 10;

function resolveConfiguredLevel(): LogLevel {
  const raw = (import.meta.env.VITE_LOG_LEVEL ?? "INFO").toUpperCase();
  return raw in LEVEL_ORDER ? (raw as LogLevel) : "INFO";
}

let currentLevel: LogLevel = resolveConfiguredLevel();

export function setLogLevel(level: LogLevel): void {
  currentLevel = level;
}

function shouldLog(level: LogLevel): boolean {
  return LEVEL_ORDER[level] >= LEVEL_ORDER[currentLevel];
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function timestamp(): string {
  const now = new Date();
  return `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}

function consoleMethodFor(level: LogLevel): (...args: unknown[]) => void {
  switch (level) {
    case "DEBUG":
      return console.debug ?? console.log;
    case "WARNING":
      return console.warn;
    case "ERROR":
    case "CRITICAL":
      return console.error;
    default:
      return console.info ?? console.log;
  }
}

function emit(level: LogLevel, category: string, message: string, ...details: unknown[]): void {
  if (!shouldLog(level)) {
    return;
  }
  const line = `${timestamp()} | ${level.padEnd(LEVEL_WIDTH)} | ${category.padEnd(CATEGORY_WIDTH)} | ${message}`;
  const method = consoleMethodFor(level);
  method(`%c${line}`, LEVEL_STYLES[level], ...details);
}

export const logger = {
  debug(category: string, message: string, ...details: unknown[]): void {
    emit("DEBUG", category, message, ...details);
  },
  info(category: string, message: string, ...details: unknown[]): void {
    emit("INFO", category, message, ...details);
  },
  warn(category: string, message: string, ...details: unknown[]): void {
    emit("WARNING", category, message, ...details);
  },
  error(category: string, message: string, ...details: unknown[]): void {
    emit("ERROR", category, message, ...details);
  },
  critical(category: string, message: string, ...details: unknown[]): void {
    emit("CRITICAL", category, message, ...details);
  },
};

export function formatDuration(milliseconds: number): string {
  if (milliseconds < 1000) {
    return `${milliseconds.toFixed(1)} ms`;
  }
  return `${(milliseconds / 1000).toFixed(2)} s`;
}

export function getSlowRequestThresholdMs(): number {
  const raw = Number(import.meta.env.VITE_SLOW_REQUEST_THRESHOLD_MS ?? "1000");
  return Number.isFinite(raw) && raw > 0 ? raw : 1000;
}

// Log/hata mesajlarina eklenmeden once bir nesneden hassas alanlari
// (sifre, token, cookie, Authorization, API anahtari vb.) maskeler - bkz.
// backend `app.logging_utils.mask_sensitive` (ayni anahtar belirteclerini
// paylasir).
const SENSITIVE_KEY_MARKERS = [
  "password",
  "parola",
  "token",
  "authorization",
  "cookie",
  "secret",
  "api_key",
  "apikey",
  "access_key",
];

function isSensitiveKey(key: string): boolean {
  const lowered = key.toLowerCase();
  return SENSITIVE_KEY_MARKERS.some((marker) => lowered.includes(marker));
}

export function maskSensitive<T extends Record<string, unknown>>(data: T): T {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(data)) {
    if (isSensitiveKey(key)) {
      result[key] = "***";
    } else if (value && typeof value === "object" && !Array.isArray(value)) {
      result[key] = maskSensitive(value as Record<string, unknown>);
    } else {
      result[key] = value;
    }
  }
  return result as T;
}

/**
 * `fn`'in calisma suresini olcup INFO (veya esik asilirsa WARNING) loglar;
 * `fn` reddederse (rejects) sure ERROR seviyesinde kaydedilip hata OLDUGU
 * GIBI yeniden firlatilir (bkz. backend `app.logging_utils.log_duration` -
 * ayni desenin frontend karsiligi).
 */
export async function logTiming<T>(
  category: string,
  label: string,
  fn: () => Promise<T>,
  options?: { slowThresholdMs?: number },
): Promise<T> {
  const threshold = options?.slowThresholdMs ?? getSlowRequestThresholdMs();
  const start = performance.now();
  try {
    const result = await fn();
    const elapsed = performance.now() - start;
    if (elapsed > threshold) {
      logger.warn(
        category,
        `${label} took ${formatDuration(elapsed)} (slow, >${(threshold / 1000).toFixed(2)} s)`,
      );
    } else {
      logger.info(category, `${label} took ${formatDuration(elapsed)}`);
    }
    return result;
  } catch (error) {
    const elapsed = performance.now() - start;
    logger.error(category, `${label} failed after ${formatDuration(elapsed)}`, error);
    throw error;
  }
}
