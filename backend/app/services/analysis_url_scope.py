"""Free-demo URL scope validation for browser-based page analysis.

The URL text cannot reveal the exact runtime memory cost of an arbitrary page.
This module therefore blocks inputs that are predictably outside the product's
HTML-page analysis scope before a draft can reserve an entitlement or Chip.
The analyzer memory guard remains the final safety boundary for other dynamic
pages whose cost can only be known while rendering.
"""

from dataclasses import dataclass
from urllib.parse import urlparse

MAX_ANALYSIS_URL_LENGTH = 2_048

BLOCKED_HOSTS = (
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
)

BLOCKED_FILE_EXTENSIONS = (
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
)

UNSUPPORTED_ANALYSIS_URL_MESSAGE = (
    "Bu URL ücretsiz demo kapasitesinin dışında. Video/sosyal medya, harita, büyük pazar yeri "
    "ve doğrudan dosya bağlantıları analiz edilemez. Daha hafif bir kurumsal, tanıtım veya "
    "ürün alt sayfası URL'si girin; test başlatılmadı ve Chip harcanmadı."
)


@dataclass(frozen=True)
class UnsupportedAnalysisUrlError(ValueError):
    field: str
    message: str = UNSUPPORTED_ANALYSIS_URL_MESSAGE
    code: str = "UNSUPPORTED_ANALYSIS_URL"

    def __str__(self) -> str:
        return self.message


def _host_matches(hostname: str, blocked_host: str) -> bool:
    return hostname == blocked_host or hostname.endswith(f".{blocked_host}")


def rejection_reason(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if len(candidate) > MAX_ANALYSIS_URL_LENGTH:
        return UNSUPPORTED_ANALYSIS_URL_MESSAGE

    parsed = urlparse(candidate)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if any(_host_matches(hostname, blocked) for blocked in BLOCKED_HOSTS):
        return UNSUPPORTED_ANALYSIS_URL_MESSAGE
    if hostname.startswith("amazon.") or ".amazon." in hostname:
        return UNSUPPORTED_ANALYSIS_URL_MESSAGE

    path = parsed.path.lower().rstrip("/")
    if any(path.endswith(extension) for extension in BLOCKED_FILE_EXTENSIONS):
        return UNSUPPORTED_ANALYSIS_URL_MESSAGE
    if hostname == "google.com" or hostname.endswith(".google.com"):
        if path == "/maps" or path.startswith("/maps/"):
            return UNSUPPORTED_ANALYSIS_URL_MESSAGE
    return None


def validate_analysis_url(value: object, *, field: str) -> None:
    reason = rejection_reason(value)
    if reason:
        raise UnsupportedAnalysisUrlError(field=field, message=reason)


__all__ = [
    "BLOCKED_FILE_EXTENSIONS",
    "BLOCKED_HOSTS",
    "MAX_ANALYSIS_URL_LENGTH",
    "UNSUPPORTED_ANALYSIS_URL_MESSAGE",
    "UnsupportedAnalysisUrlError",
    "rejection_reason",
    "validate_analysis_url",
]
