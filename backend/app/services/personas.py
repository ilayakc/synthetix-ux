"""Persona tanimi, dagilimi ve deterministik cohort/segment ornekleme servisi.

Bu modul yalnizca *kimin* test edilecegi sorusuna cevap verir: demografik
dagilim tanimlarini dogrular, hazir (builtin) presetleri saglar ve bir
dagilim + persona sayisindan deterministik bir cohort/segment ozeti uretir.
Personalarin *nasil davranacagi* (simulasyon sonucu, gorev basarisi,
donusum vb.) bu asamanin kapsami disindadir (bkz. Prompt 7).

Onemli tasarim kararlari:

- 100-50.000 persona icin her kisi ayri bir kalici satir olarak
  materyalize edilmez; bunun yerine dagilim, kartezyen kombinasyonlardan
  olusan sinirli sayida cohort/segment'e (ornegin "25-34 yas x TR x mobil")
  ayristirilir ve yalnizca segment basina sayim tutulur. Bu, bellek
  kullanimini persona sayisindan degil segment sayisindan (MAX_SEGMENTS ile
  sinirli) bagimsiz kilar.
- Segment sayimlari "en buyuk kalan" (largest remainder) yontemiyle
  hesaplanir: bu tamamen aritmetiktir (rastgelelik icermez), bu yuzden ayni
  dagilim + persona sayisi her zaman ayni sayimlari uretir. `deterministic_seed`
  yalnizca esit kalan (tie) durumlarinda hangi segmentin fazladan +1 alacagini
  belirlemek icin kullanilir (bkz. `_break_ties`) - boylece kucuk segmentler
  sistematik olarak hep ayni sirada avantajli/dezavantajli olmaz, ama ayni
  seed her zaman ayni sonucu verir.
- Stereotip yasagi: bir dagilim tanimi, yas/bolge/erisilebilirlik gibi
  korunan niteliklerden dogrulanmamis davranis/gelir/zeka/hedefleme sonucu
  cikaran hicbir alan tasiyamaz (bkz. docs/scientific-integrity.md).
  Bolgesel ilgi yalnizca acikca "senaryo bazli tahmini ilgi" olarak,
  sunucu tarafindan sabitlenmis bir belirsizlik uyarisiyla birlikte
  tutulabilir; gercek pazar talebi olarak sunulamaz.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from dataclasses import dataclass

GENERATOR_VERSION = "persona-sampler-2026.1"

# --- Persona boyutlari (tumu istege baglidir) --------------------------------
AGE_RANGE = "age_range"
REGION = "region"
DEVICE_CLASS = "device_class"
DIGITAL_LITERACY = "digital_literacy"
ACCESSIBILITY_NEED = "accessibility_need"

PERSONA_DIMENSIONS = (AGE_RANGE, REGION, DEVICE_CLASS, DIGITAL_LITERACY, ACCESSIBILITY_NEED)

DIMENSION_LABELS: dict[str, str] = {
    AGE_RANGE: "Yaş aralığı",
    REGION: "Ülke / bölge",
    DEVICE_CLASS: "Cihaz sınıfı",
    DIGITAL_LITERACY: "Dijital yatkınlık düzeyi",
    ACCESSIBILITY_NEED: "Erişilebilirlik ihtiyacı",
}

WEIGHT_SUM_TOLERANCE = 0.01
MIN_PERSONA_COUNT = 100
MAX_PERSONA_COUNT = 50_000
# Kartezyen kombinasyon (segment) sayisinin ust siniri: bellek kullanimini
# persona sayisindan bagimsiz, sabit bir tavanla sinirlar.
MAX_SEGMENTS = 2000
MAX_BUCKETS_PER_DIMENSION = 25

REGIONAL_INTEREST_DISCLAIMER = (
    "Senaryo bazli tahmini ilgi: bu bir varsayimdir, dogrulanmis arastirma "
    "verisine dayanmaz ve gercek pazar talebi olarak yorumlanmamalidir."
)
REGIONAL_INTEREST_CONFIDENCE_LEVELS = ("low", "medium", "high")

# Bir kovanin (bucket) uzerinde asla bulunmamasi gereken; demografik
# nitelikten dogrulanmamis davranis/gelir/zeka/hedefleme sonucu cikaran ya
# da ayrimci skorlama ureten alan adlari (case-insensitive, bkz.
# docs/scientific-integrity.md ve docs/product-rules.md).
BANNED_INFERENCE_KEYS = {
    "income",
    "gelir",
    "salary",
    "maas",
    "net_worth",
    "servet",
    "intelligence",
    "zeka",
    "iq",
    "education_inferred",
    "behavior_prediction",
    "davranis_tahmini",
    "predicted_behavior",
    "conversion_probability",
    "donusum_olasiligi",
    "credit_score",
    "kredi_notu",
    "risk_score",
    "target_score",
    "hedefleme_skoru",
    "discriminatory_score",
    "purchase_likelihood",
    "satin_alma_olasiligi",
    "spending_power",
}

# Bolgesel ilgi metninde gecmesi yasaklanan, kalibrasyon gerektiren/kesin
# iddia ifadeleri (bkz. docs/scientific-integrity.md "Yasaklanan iddialar").
BANNED_CLAIM_PHRASES = (
    "gercek pazar talebi",
    "gerçek pazar talebi",
    "dogrulanmis",
    "doğrulanmış",
    "bilimsel olarak kanitlanmis",
    "bilimsel olarak kanıtlanmış",
    "istatistiksel olarak anlamli",
    "istatistiksel olarak anlamlı",
)


class PersonaValidationError(ValueError):
    """Bir persona dagilim/preset tanimi gecersiz oldugunda firlatilir (400)."""


# --- Dogrulama ----------------------------------------------------------------


def _normalize(text: str) -> str:
    return text.strip().lower()


def _deep_scan_for_banned_keys(node: object, path: str = "") -> None:
    if isinstance(node, dict):
        for raw_key, value in node.items():
            key = str(raw_key)
            if key.lower() in BANNED_INFERENCE_KEYS:
                raise PersonaValidationError(
                    f"'{path}{key}' alani izin verilmeyen bir demografik cikarim "
                    "iceriyor (stereotip/ayrimcilik yasagi, bkz. "
                    "docs/scientific-integrity.md)"
                )
            _deep_scan_for_banned_keys(value, f"{path}{key}.")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _deep_scan_for_banned_keys(item, f"{path}[{index}].")


def _validate_scenario_interest(dimension: str, bucket_key: str, scenario_interest: object) -> None:
    if not isinstance(scenario_interest, dict):
        raise PersonaValidationError(
            f"'{dimension}.{bucket_key}.scenario_interest' bir nesne (object) olmalidir"
        )

    assumption = scenario_interest.get("assumption")
    if not isinstance(assumption, str) or not assumption.strip():
        raise PersonaValidationError(
            f"'{dimension}.{bucket_key}.scenario_interest' acik bir 'assumption' "
            "(varsayim) metni tasimalidir"
        )
    normalized_assumption = _normalize(assumption)
    for phrase in BANNED_CLAIM_PHRASES:
        if phrase in normalized_assumption:
            raise PersonaValidationError(
                f"'{dimension}.{bucket_key}.scenario_interest.assumption' kalibrasyon "
                f"gerektiren bir iddia iceriyor ('{phrase}'); bkz. docs/scientific-integrity.md"
            )

    confidence = scenario_interest.get("confidence")
    if confidence not in REGIONAL_INTEREST_CONFIDENCE_LEVELS:
        raise PersonaValidationError(
            f"'{dimension}.{bucket_key}.scenario_interest.confidence' "
            f"{REGIONAL_INTEREST_CONFIDENCE_LEVELS} degerlerinden biri olmalidir"
        )

    estimate = scenario_interest.get("estimate")
    if estimate not in ("low", "medium", "high"):
        raise PersonaValidationError(
            f"'{dimension}.{bucket_key}.scenario_interest.estimate' "
            "'low'/'medium'/'high' degerlerinden biri olmalidir"
        )

    # Uyari metni her zaman sunucu tarafinda sabitlenir; cagiranin gonderdigi
    # herhangi bir 'disclaimer' degeri (varsa) yok sayilir - boylece bu alan
    # "gercek pazar talebi" gibi bir iddiaya donusturulerek spoof edilemez.
    scenario_interest["disclaimer"] = REGIONAL_INTEREST_DISCLAIMER


def _validate_buckets(dimension: str, buckets: object) -> None:
    if not isinstance(buckets, list) or not buckets:
        raise PersonaValidationError(f"'{dimension}' bos olmayan bir liste olmalidir")
    if len(buckets) > MAX_BUCKETS_PER_DIMENSION:
        raise PersonaValidationError(f"'{dimension}' en fazla {MAX_BUCKETS_PER_DIMENSION} kova icerebilir")

    seen_keys: set[str] = set()
    total_weight = 0.0
    for bucket in buckets:
        if not isinstance(bucket, dict):
            raise PersonaValidationError(f"'{dimension}' icindeki her kova bir nesne olmalidir")

        key = bucket.get("key")
        if not isinstance(key, str) or not key.strip():
            raise PersonaValidationError(f"'{dimension}' icindeki her kova bir 'key' tasimalidir")
        if key in seen_keys:
            raise PersonaValidationError(f"'{dimension}' icinde yinelenen kova anahtari: '{key}'")
        seen_keys.add(key)

        label = bucket.get("label")
        if not isinstance(label, str) or not label.strip():
            raise PersonaValidationError(f"'{dimension}.{key}' bir 'label' tasimalidir")

        weight = bucket.get("weight")
        if not isinstance(weight, int | float) or isinstance(weight, bool):
            raise PersonaValidationError(f"'{dimension}.{key}' agirligi sayisal olmalidir")
        if weight <= 0:
            raise PersonaValidationError(
                f"'{dimension}.{key}' agirligi bos veya negatif olamaz (0'dan buyuk olmalidir)"
            )
        total_weight += float(weight)

        if dimension == REGION and "scenario_interest" in bucket:
            _validate_scenario_interest(dimension, key, bucket["scenario_interest"])

    if abs(total_weight - 100.0) > WEIGHT_SUM_TOLERANCE:
        raise PersonaValidationError(
            f"'{dimension}' agirliklarinin toplami 100 olmalidir (mevcut: {total_weight:g})"
        )


def _validate_age_ranges(buckets: list[dict]) -> None:
    ranges: list[tuple[int, int, str]] = []
    for bucket in buckets:
        min_age = bucket.get("min_age")
        max_age = bucket.get("max_age")
        if (
            not isinstance(min_age, int)
            or not isinstance(max_age, int)
            or isinstance(min_age, bool)
            or isinstance(max_age, bool)
        ):
            raise PersonaValidationError(
                f"'{AGE_RANGE}.{bucket.get('key')}' tam sayi 'min_age'/'max_age' tasimalidir"
            )
        if min_age < 0 or max_age < min_age:
            raise PersonaValidationError(
                f"'{AGE_RANGE}.{bucket.get('key')}' gecersiz yas araligi: {min_age}-{max_age}"
            )
        ranges.append((min_age, max_age, bucket["key"]))

    ranges.sort(key=lambda item: item[0])
    for (min_a, max_a, key_a), (min_b, max_b, key_b) in zip(ranges, ranges[1:], strict=False):
        if min_b <= max_a:
            raise PersonaValidationError(
                f"yas araliklari cakisiyor: '{key_a}' ({min_a}-{max_a}) ve " f"'{key_b}' ({min_b}-{max_b})"
            )


def validate_distribution(distribution: object) -> None:
    """Bir persona dagilim tanimini dogrular; gecersizse `PersonaValidationError` firlatir.

    `distribution`, en fazla `PERSONA_DIMENSIONS` icindeki anahtarlari tasiyan
    bir dict'tir; her deger `{"key", "label", "weight", ...}` kovalarindan
    olusan bir listedir. Butun boyutlar istege baglidir (bos bir dict veya
    yalnizca bazi boyutlari iceren bir dict gecerlidir).
    """

    if not isinstance(distribution, dict):
        raise PersonaValidationError("distribution bir nesne (object) olmalidir")

    unknown = set(distribution) - set(PERSONA_DIMENSIONS)
    if unknown:
        raise PersonaValidationError(f"Bilinmeyen boyut(lar): {', '.join(sorted(unknown))}")

    _deep_scan_for_banned_keys(distribution)

    for dimension, buckets in distribution.items():
        _validate_buckets(dimension, buckets)
        if dimension == AGE_RANGE:
            _validate_age_ranges(buckets)


# --- Hazir (builtin) presetler --------------------------------------------


@dataclass(frozen=True)
class BuiltinPreset:
    key: str
    name: str
    description: str
    distribution: dict


def _general_web_users_distribution() -> dict:
    return {
        AGE_RANGE: [
            {"key": "18_24", "label": "18-24", "weight": 20, "min_age": 18, "max_age": 24},
            {"key": "25_34", "label": "25-34", "weight": 30, "min_age": 25, "max_age": 34},
            {"key": "35_44", "label": "35-44", "weight": 25, "min_age": 35, "max_age": 44},
            {"key": "45_64", "label": "45-64", "weight": 20, "min_age": 45, "max_age": 64},
            {"key": "65_plus", "label": "65+", "weight": 5, "min_age": 65, "max_age": 100},
        ],
        DEVICE_CLASS: [
            {"key": "mobile", "label": "Mobil", "weight": 60},
            {"key": "desktop", "label": "Masaüstü", "weight": 35},
            {"key": "tablet", "label": "Tablet", "weight": 5},
        ],
        DIGITAL_LITERACY: [
            {"key": "low", "label": "Düşük", "weight": 15},
            {"key": "medium", "label": "Orta", "weight": 55},
            {"key": "high", "label": "Yüksek", "weight": 30},
        ],
    }


def _mobile_first_gen_z_distribution() -> dict:
    return {
        AGE_RANGE: [
            {"key": "13_17", "label": "13-17", "weight": 15, "min_age": 13, "max_age": 17},
            {"key": "18_24", "label": "18-24", "weight": 55, "min_age": 18, "max_age": 24},
            {"key": "25_28", "label": "25-28", "weight": 30, "min_age": 25, "max_age": 28},
        ],
        DEVICE_CLASS: [
            {"key": "mobile", "label": "Mobil", "weight": 90},
            {"key": "tablet", "label": "Tablet", "weight": 7},
            {"key": "desktop", "label": "Masaüstü", "weight": 3},
        ],
        DIGITAL_LITERACY: [
            {"key": "medium", "label": "Orta", "weight": 25},
            {"key": "high", "label": "Yüksek", "weight": 75},
        ],
    }


def _accessibility_focused_seniors_distribution() -> dict:
    return {
        AGE_RANGE: [
            {"key": "55_64", "label": "55-64", "weight": 35, "min_age": 55, "max_age": 64},
            {"key": "65_74", "label": "65-74", "weight": 40, "min_age": 65, "max_age": 74},
            {"key": "75_plus", "label": "75+", "weight": 25, "min_age": 75, "max_age": 100},
        ],
        DEVICE_CLASS: [
            {"key": "desktop", "label": "Masaüstü", "weight": 45},
            {"key": "tablet", "label": "Tablet", "weight": 35},
            {"key": "mobile", "label": "Mobil", "weight": 20},
        ],
        DIGITAL_LITERACY: [
            {"key": "low", "label": "Düşük", "weight": 40},
            {"key": "medium", "label": "Orta", "weight": 45},
            {"key": "high", "label": "Yüksek", "weight": 15},
        ],
        ACCESSIBILITY_NEED: [
            {"key": "none", "label": "Belirtilmemiş", "weight": 40},
            {"key": "visual", "label": "Görme ile ilgili", "weight": 30},
            {"key": "motor", "label": "Motor/hareket ile ilgili", "weight": 15},
            {"key": "cognitive", "label": "Bilişsel/dikkatle ilgili", "weight": 15},
        ],
    }


BUILTIN_PRESETS: dict[str, BuiltinPreset] = {
    preset.key: preset
    for preset in (
        BuiltinPreset(
            key="general_web_users",
            name="Genel web kullanıcıları",
            description="Genel amaçlı, dengeli bir yaş/cihaz/dijital yatkınlık dağılımı.",
            distribution=_general_web_users_distribution(),
        ),
        BuiltinPreset(
            key="mobile_first_gen_z",
            name="Mobil öncelikli Gen Z",
            description="Ağırlıklı olarak genç, mobil cihaz kullanan, yüksek dijital yatkınlığa sahip kitle.",
            distribution=_mobile_first_gen_z_distribution(),
        ),
        BuiltinPreset(
            key="accessibility_focused_seniors",
            name="Erişilebilirlik odaklı, ileri yaş",
            description="İleri yaş grubu ve çeşitli erişilebilirlik ihtiyaçları ağırlıklı bir dağılım.",
            distribution=_accessibility_focused_seniors_distribution(),
        ),
    )
}

BUILTIN_PRESET_ID_PREFIX = "builtin:"


def builtin_preset_id(key: str) -> str:
    return f"{BUILTIN_PRESET_ID_PREFIX}{key}"


def is_builtin_preset_id(preset_id: str) -> bool:
    return preset_id.startswith(BUILTIN_PRESET_ID_PREFIX)


def get_builtin_preset(preset_id: str) -> BuiltinPreset:
    key = preset_id[len(BUILTIN_PRESET_ID_PREFIX) :]
    try:
        return BUILTIN_PRESETS[key]
    except KeyError as exc:
        raise PersonaValidationError(f"Bilinmeyen hazir preset: {preset_id}") from exc


def list_builtin_presets() -> tuple[BuiltinPreset, ...]:
    return tuple(BUILTIN_PRESETS.values())


# --- Deterministik cohort/segment ornekleme -----------------------------------


@dataclass(frozen=True)
class CohortSegment:
    key: str
    label: str
    dimension_values: dict
    count: int
    share: float


@dataclass(frozen=True)
class CohortSampleResult:
    generator_version: str
    deterministic_seed: int
    distribution_snapshot: dict
    segments: tuple
    total_count: int
    sample_hash: str


# --- Kalici temsili persona projeksiyonu (bkz. app.models.personas.Persona) --


# Bir simulasyon icin DB'ye yazilacak (persist edilecek) en fazla temsili
# persona sayisi. `sample_cohorts` en fazla MAX_SEGMENTS (2000) segment
# uretebilir; bu projeksiyon onu goruntulenebilir/incelenebilir kucuk bir
# kumeye indirger. `input_snapshot["persona_sample"]` (segment/cohort listesi)
# bu projeksiyonun YERINE GECMEZ - motor icin kaynak gercekligi olmaya devam
# eder; bu yalnizca gorunurluk/inceleme/ileriki AI pipeline'i icin turetilmis,
# daha kaba bir kalici goruntudur.
MAX_REPRESENTATIVE_PERSONAS = 100


@dataclass(frozen=True)
class PersonaProjection:
    """Kalici `personas` tablosuna yazilacak bir satirin (henuz DB'ye
    yazilmamis) saf veri temsili. `app.models.personas.Persona` ile
    birebir alan eslesir (id/simulation_run_id/created_at haric - bunlar
    persist adiminda cagiran tarafca atanir)."""

    index: int
    label: str
    attributes: dict
    population_weight: int


def _segment_distance(a: CohortSegment, b: CohortSegment) -> int:
    """Iki segment arasindaki kategorik Hamming mesafesi (farkli boyut sayisi)."""

    keys = set(a.dimension_values) | set(b.dimension_values)
    return sum(1 for key in keys if a.dimension_values.get(key) != b.dimension_values.get(key))


def _nearest_representative_key(segment: CohortSegment, representatives: tuple[CohortSegment, ...]) -> str:
    """`segment`'e (kategorik Hamming mesafesiyle) en yakin temsilcinin key'ini dondurur.

    Esit mesafeli birden fazla aday varsa, en kucuk (lexicographic) segment
    key'ine sahip olan secilir - bu, `representatives`'in siralamasindan/
    cagri sirasindan bagimsiz, deterministik bir tie-break saglar.
    """

    best = min(representatives, key=lambda rep: (_segment_distance(segment, rep), rep.key))
    return best.key


def build_representative_personas(
    sample: CohortSampleResult,
    max_personas: int = MAX_REPRESENTATIVE_PERSONAS,
) -> tuple[PersonaProjection, ...]:
    """Bir `CohortSampleResult`'i en fazla `max_personas` kalici temsili
    personaya (saf, DB'siz) indirger.

    - Segment sayisi `max_personas`'i asmiyorsa: her segment birebir bir
      personaya donusur (zorla `max_personas`'e tamamlanmaz - dogal segment
      sayisi korunur, sahte cesitlilik uretilmez).
    - Segment sayisi `max_personas`'i asiyorsa: population count'u en yuksek
      `max_personas` segment temsilci secilir; kalan (secilmeyen) segmentler,
      kategorik Hamming mesafesiyle en yakin temsilciye deterministik olarak
      birlestirilir (count'lari temsilcinin population_weight'ine eklenir).

    Sonuc, segmentlerin (veya `sample.segments` tuple'inin) girdi sirasindan
    bagimsizdir: kanonik sira daima segment `key`'ine gore artan siralamadir.
    Girdi (`sample`) mutate edilmez. Ayni `sample` + `max_personas` her
    zaman ayni sirali sonucu uretir (sample_cohorts zaten deterministiktir;
    bu fonksiyon ek rastgelelik icermez).
    """

    if not isinstance(sample, CohortSampleResult):
        raise PersonaValidationError("sample bir CohortSampleResult olmalidir")
    if not sample.segments:
        raise PersonaValidationError("cohort sample bos segment listesi iceremez")
    if not isinstance(sample.total_count, int) or sample.total_count <= 0:
        raise PersonaValidationError("cohort sample total_count pozitif bir tam sayi olmalidir")
    if not isinstance(max_personas, int) or isinstance(max_personas, bool) or max_personas <= 0:
        raise PersonaValidationError("max_personas pozitif bir tam sayi olmalidir")

    # Kanonik sira: girdi tuple'inin fiili sirasindan bagimsiz olmak icin
    # segment key'ine gore artan siralama. `sample.segments` burada
    # kopyalanarak islenir, girdi mutate edilmez.
    segments_sorted = tuple(sorted(sample.segments, key=lambda segment: segment.key))

    if len(segments_sorted) <= max_personas:
        representative_segments = segments_sorted
        weights: dict[str, int] = {segment.key: segment.count for segment in segments_sorted}
    else:
        # En buyuk count'a sahip ilk `max_personas` segment temsilci olur;
        # esitliklerde kanonik key (kucukten buyuge) tie-break eder.
        selected = sorted(segments_sorted, key=lambda segment: (-segment.count, segment.key))[:max_personas]
        selected_keys = {segment.key for segment in selected}
        representative_segments = tuple(sorted(selected, key=lambda segment: segment.key))
        weights = {segment.key: segment.count for segment in representative_segments}

        remainder_segments = [segment for segment in segments_sorted if segment.key not in selected_keys]
        for segment in remainder_segments:
            nearest_key = _nearest_representative_key(segment, representative_segments)
            weights[nearest_key] += segment.count

    personas = tuple(
        PersonaProjection(
            index=index,
            label=segment.label,
            attributes=dict(segment.dimension_values),
            population_weight=weights[segment.key],
        )
        for index, segment in enumerate(representative_segments)
    )

    if not (1 <= len(personas) <= max_personas):
        raise PersonaValidationError(
            f"persona projeksiyonu 1 ile {max_personas} arasinda persona icermelidir "
            f"(hesaplanan: {len(personas)})"
        )
    for persona in personas:
        if persona.population_weight <= 0:
            raise PersonaValidationError("her persona population_weight'i pozitif olmalidir")

    total_weight = sum(persona.population_weight for persona in personas)
    if total_weight != sample.total_count:
        raise PersonaValidationError(
            "persona projeksiyonu agirlik toplami cohort sample total_count ile eslesmiyor "
            f"(beklenen: {sample.total_count}, hesaplanan: {total_weight})"
        )

    return personas


def _iter_bucket_combinations(distribution: dict, dimensions: list[str]):
    """Kartezyen kombinasyonlari (segmentleri) MAX_SEGMENTS'i asmadan uretir."""

    if not dimensions:
        yield ()
        return

    total = 1
    for dimension in dimensions:
        total *= len(distribution[dimension])
        if total > MAX_SEGMENTS:
            raise PersonaValidationError(
                f"Secilen boyut kombinasyonu {MAX_SEGMENTS} segment sinirini asiyor "
                "(daha az boyut veya daha az kova secin)"
            )

    def _recurse(index: int, acc: tuple):
        if index == len(dimensions):
            yield acc
            return
        dimension = dimensions[index]
        for bucket in distribution[dimension]:
            yield from _recurse(index + 1, acc + ((dimension, bucket),))

    yield from _recurse(0, ())


def _allocate_counts(combo_weights: list[float], total_count: int, seed: int) -> list[int]:
    """En buyuk kalan (largest remainder) yontemiyle deterministik tamsayi paylastirma.

    Esit kalanli (tie) segmentler arasindaki secim, `seed` ile tohumlanmis
    bir siralamayla belirlenir: ayni seed her zaman ayni sonucu, farkli
    seed'ler ise farkli (ama toplami her zaman `total_count`'a esit) bir
    tie-break sirasi uretir.
    """

    raw = [total_count * (weight / 100.0) for weight in combo_weights]
    base = [int(value) for value in raw]
    remainder_order = list(range(len(combo_weights)))
    rng = random.Random(seed)
    rng.shuffle(remainder_order)
    remainder_order.sort(key=lambda i: raw[i] - base[i], reverse=True)

    remaining = total_count - sum(base)
    counts = list(base)
    for i in remainder_order[:remaining]:
        counts[i] += 1
    return counts


def _hash_snapshot(snapshot: dict, deterministic_seed: int) -> str:
    payload = json.dumps(
        {"snapshot": snapshot, "seed": deterministic_seed, "generator_version": GENERATOR_VERSION},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sample_cohorts(
    distribution: dict,
    persona_count: int,
    deterministic_seed: int,
) -> CohortSampleResult:
    """Bir dagilim + persona sayisindan deterministik bir cohort/segment ozeti uretir.

    Her kisi icin ayri bir satir uretmez (bkz. modul dokstring'i): yalnizca
    segment basina (dagilimdaki boyutlarin kartezyen kombinasyonu) sayim
    hesaplar. Ayni `distribution` + `persona_count` + `deterministic_seed`
    her zaman ayni segment sayimlarini ve ayni `sample_hash`'i uretir.
    """

    validate_distribution(distribution)

    if not isinstance(persona_count, int) or isinstance(persona_count, bool) or persona_count <= 0:
        raise PersonaValidationError("persona_count pozitif bir tam sayi olmalidir")
    if not (MIN_PERSONA_COUNT <= persona_count <= MAX_PERSONA_COUNT):
        raise PersonaValidationError(
            f"persona_count {MIN_PERSONA_COUNT} ile {MAX_PERSONA_COUNT} arasinda olmalidir"
        )
    if not isinstance(deterministic_seed, int) or isinstance(deterministic_seed, bool):
        raise PersonaValidationError("deterministic_seed bir tam sayi olmalidir")

    dimensions_present = [d for d in PERSONA_DIMENSIONS if d in distribution]

    if not dimensions_present:
        segments: tuple[CohortSegment, ...] = (
            CohortSegment(
                key="genel",
                label="Genel (boyut belirtilmedi)",
                dimension_values={},
                count=persona_count,
                share=1.0,
            ),
        )
    else:
        combos = list(_iter_bucket_combinations(distribution, dimensions_present))
        combo_weights = [_combo_weight(combo) for combo in combos]
        counts = _allocate_counts(combo_weights, persona_count, deterministic_seed)

        segments = tuple(
            CohortSegment(
                key="__".join(bucket["key"] for _, bucket in combo),
                label=" × ".join(bucket["label"] for _, bucket in combo),
                dimension_values={dim: bucket["key"] for dim, bucket in combo},
                count=count,
                share=round(count / persona_count, 6),
            )
            for combo, count in zip(combos, counts, strict=False)
            if count > 0
        )

    # Derin kopya: cagiran taraf `distribution` girdisini daha sonra
    # mutasyona ugratsa bile bu sonucun `distribution_snapshot`'i
    # degismez (immutable) kalir.
    snapshot = {
        "distribution": copy.deepcopy(distribution),
        "persona_count": persona_count,
        "dimensions": list(dimensions_present),
    }

    return CohortSampleResult(
        generator_version=GENERATOR_VERSION,
        deterministic_seed=deterministic_seed,
        distribution_snapshot=snapshot,
        segments=segments,
        total_count=persona_count,
        sample_hash=_hash_snapshot(snapshot, deterministic_seed),
    )


def _combo_weight(combo: tuple) -> float:
    weight = 1.0
    for _dimension, bucket in combo:
        weight *= float(bucket["weight"]) / 100.0
    return weight * 100.0


def iter_segment_batches(result: CohortSampleResult, batch_size: int = 500):
    """Segmentleri sabit boyutlu partiler (batch) halinde dondurur.

    Segment sayisi zaten `MAX_SEGMENTS` ile sinirlidir; bu yardimci,
    cagiran tarafin (ornegin bir API yaniti veya ileride bir tuketici
    servisin) buyuk sonuclari tek seferde bellekte tutmak yerine sabit
    boyutlu partiler halinde islemesine/aktarmasina izin verir.
    """

    segments = result.segments
    for start in range(0, len(segments), batch_size):
        yield segments[start : start + batch_size]


__all__ = [
    "GENERATOR_VERSION",
    "PERSONA_DIMENSIONS",
    "DIMENSION_LABELS",
    "AGE_RANGE",
    "REGION",
    "DEVICE_CLASS",
    "DIGITAL_LITERACY",
    "ACCESSIBILITY_NEED",
    "MIN_PERSONA_COUNT",
    "MAX_PERSONA_COUNT",
    "MAX_SEGMENTS",
    "REGIONAL_INTEREST_DISCLAIMER",
    "REGIONAL_INTEREST_CONFIDENCE_LEVELS",
    "BuiltinPreset",
    "BUILTIN_PRESETS",
    "CohortSegment",
    "CohortSampleResult",
    "PersonaValidationError",
    "builtin_preset_id",
    "is_builtin_preset_id",
    "get_builtin_preset",
    "list_builtin_presets",
    "validate_distribution",
    "sample_cohorts",
    "iter_segment_batches",
    "MAX_REPRESENTATIVE_PERSONAS",
    "PersonaProjection",
    "build_representative_personas",
]
