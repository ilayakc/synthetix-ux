export const UNSUPPORTED_ANALYSIS_URL_MESSAGE =
  "Bu URL ücretsiz demo kapasitesinin dışında. Video/sosyal medya, harita, büyük pazar yeri " +
  "ve doğrudan dosya bağlantıları analiz edilemez. Daha hafif bir kurumsal, tanıtım veya " +
  "ürün alt sayfası URL'si girin; test başlatılmadı ve Chip harcanmadı.";

const MAX_ANALYSIS_URL_LENGTH = 2048;
const BLOCKED_HOSTS = [
  "youtube.com",
  "youtu.be",
  "vimeo.com",
  "dailymotion.com",
  "twitch.tv",
  "netflix.com",
  "primevideo.com",
  "disneyplus.com",
  "tiktok.com",
  "instagram.com",
  "facebook.com",
  "twitter.com",
  "x.com",
  "maps.google.com",
  "trendyol.com",
  "hepsiburada.com",
  "n11.com",
  "aliexpress.com",
  "temu.com",
];
const BLOCKED_FILE_EXTENSIONS = [
  ".avi",
  ".m4a",
  ".mkv",
  ".mov",
  ".mp3",
  ".mp4",
  ".mpeg",
  ".pdf",
  ".rar",
  ".tar",
  ".wav",
  ".webm",
  ".zip",
];

function hostMatches(hostname: string, blockedHost: string): boolean {
  return hostname === blockedHost || hostname.endsWith(`.${blockedHost}`);
}

export function analysisUrlScopeRejectionReason(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const candidate = value.trim();
  if (!candidate) return null;
  if (candidate.length > MAX_ANALYSIS_URL_LENGTH) return UNSUPPORTED_ANALYSIS_URL_MESSAGE;

  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    return null;
  }

  const hostname = parsed.hostname.toLowerCase().replace(/\.$/, "");
  if (BLOCKED_HOSTS.some((blockedHost) => hostMatches(hostname, blockedHost))) {
    return UNSUPPORTED_ANALYSIS_URL_MESSAGE;
  }
  if (hostname.startsWith("amazon.") || hostname.includes(".amazon.")) {
    return UNSUPPORTED_ANALYSIS_URL_MESSAGE;
  }

  const path = parsed.pathname.toLowerCase().replace(/\/$/, "");
  if (BLOCKED_FILE_EXTENSIONS.some((extension) => path.endsWith(extension))) {
    return UNSUPPORTED_ANALYSIS_URL_MESSAGE;
  }
  if (
    (hostname === "google.com" || hostname.endsWith(".google.com")) &&
    (path === "/maps" || path.startsWith("/maps/"))
  ) {
    return UNSUPPORTED_ANALYSIS_URL_MESSAGE;
  }
  return null;
}
