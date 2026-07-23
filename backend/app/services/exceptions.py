"""Chip/entitlement servis katmaninin firlattigi is kurali istisnalari."""


class ChipServiceError(Exception):
    """Tum chip/entitlement servis hatalari icin taban sinif."""


class OrganizationNotFoundError(ChipServiceError):
    pass


class InsufficientChipBalanceError(ChipServiceError):
    """Rezervasyon istenen miktar, kullanilabilir bakiyeyi asiyor."""


class ChipReservationNotFoundError(ChipServiceError):
    pass


class InvalidChipReservationStateError(ChipServiceError):
    """Rezervasyon, istenen gecis (consume/release) icin uygun durumda degil."""


class EntitlementNotFoundError(ChipServiceError):
    pass


class EntitlementUnavailableError(ChipServiceError):
    """Hak zaten rezerve/tuketilmis (tek kullanimlik hak ikinci kez kullanilamaz)."""


class InvalidEntitlementStateError(ChipServiceError):
    """Entitlement, istenen gecis (consume/release) icin uygun durumda degil."""


class PersonaLimitExceededError(ChipServiceError):
    """Persona sayisi, ucretsiz temel UX testi hakkinin izin verdigi siniri asiyor."""


class SimulationRunNotFoundError(Exception):
    """Istenen SimulationRun bulunamadi (veya baska bir organizasyona ait)."""


class InvalidSimulationStateError(Exception):
    """SimulationRun, istenen gecis (cancel/retry) icin uygun durumda degil."""


class PageAnalysisNotFoundError(Exception):
    """Istenen PageAnalysis bulunamadi (veya baska bir organizasyona ait)."""


class UnauthorizedPageAnalysisError(Exception):
    """Kullanici, URL'yi analiz etme yetkisini onaylamadan istek gonderdi."""


class DesignAssetNotFoundError(Exception):
    """Istenen DesignAsset bulunamadi (veya baska bir organizasyona ait)."""


class DesignAssetUnavailableError(Exception):
    """DesignAsset sahiplik kontrolunu gecti ama artik kullanilabilir durumda
    degil (silinmis, saklama suresi dolmus veya ikili verisi zaten purge
    edilmis) - bkz. app.services.design_assets.ensure_asset_usable."""


class PageAnalysisSourceConflictError(Exception):
    """Istekte 'url' ve 'design_asset_id' ayni anda gonderildi ya da hicbiri
    gonderilmedi (tam olarak biri zorunludur)."""


class InvalidImageError(Exception):
    """Yuklenen dosya, izin verilen bir gorsel formatinda (PNG/JPEG/WebP) gecerli bir
    gorsel olarak decode edilemedi (bkz. app.services.design_assets)."""


class ImageTooLargeError(Exception):
    """Yuklenen dosya, izin verilen boyut/cozunurluk sinirlarini asiyor."""


class DesignGenerationJobNotFoundError(Exception):
    """Istenen DesignGenerationJob bulunamadi (veya baska bir organizasyona ait)."""


class ImageGenerationNotConfiguredError(Exception):
    """Gercek bir gorsel uretim saglayicisi yapilandirilmamis (varsayilan 'none' durumu)."""


class ImageGenerationRequestError(Exception):
    """Kullanicinin AI ile tasarim varyanti istegi (prompt/onay/kaynak asset) gecersiz."""


class InvalidDesignGenerationStateError(Exception):
    """DesignGenerationJob, istenen gecis (cancel/delete) icin uygun durumda degil."""


class PageAnalysisDependencyError(Exception):
    """Bir SimulationRun'in bagli oldugu PageAnalysis eksik/baska organizasyona
    ait/henuz sonuclanmamis/basarisiz/gecersiz oldugunda firlatilir (bkz.
    app.services.simulation_worker._load_page_feature_input). `app.services.
    simulation_worker.process_run` bunu yakalayip run'i FAILED yapar; hicbir
    sarti altinda fixture'a sessizce duselmez (bkz. Paket 4B kapsami)."""


class ModuleProcessingError(Exception):
    """Bir gelismis analiz modulunun (network_device_test/campaign_cta_test/
    synthetic_attention_estimate) islenmesi kurtarilamaz bir hatayla basarisiz oldugunda
    firlatilir. `app.services.simulation_worker.process_run` bunu `SimulationInputError`
    ile ayni sekilde ele alir: tum run 'failed' isaretlenir, rezervasyon serbest
    birakilir (Chip haksiz tuketilmez)."""
