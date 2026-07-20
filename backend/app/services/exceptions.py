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


class ModuleProcessingError(Exception):
    """Bir gelismis analiz modulunun (network_device_test/campaign_cta_test/
    synthetic_attention_estimate) islenmesi kurtarilamaz bir hatayla basarisiz oldugunda
    firlatilir. `app.services.simulation_worker.process_run` bunu `SimulationInputError`
    ile ayni sekilde ele alir: tum run 'failed' isaretlenir, rezervasyon serbest
    birakilir (Chip haksiz tuketilmez)."""
