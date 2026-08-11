"""AI etkilesim isi haritasi (ai_interaction_heatmap) cekirdek testleri.

Gorev talimatindaki safsel (DB'siz) kabul kriterlerini kapsar:
1  "Hesap olustur" gorevinde "Uye ol" > "Cok Satanlar".
2  "Cok satan urunleri incele" gorevinde ilgili urun alani one cikabilir.
3  Buyuk urun karti YALNIZCA boyutu nedeniyle yuksek oncelik alamaz.
4  Bilinmeyen candidate_id -> sonuc reddedilir.
5  AI koordinat dondurse bile sema reddeder.
6  Gorevle eslesen aday yoksa hotspot uydurulmaz.
10 Ayni fingerprint deterministiktir (ikinci uretim ayni sonucu verir).
11 Gorev degisirse fingerprint (ve sonuc) degisir.
14 Gercek tiklama/goz takibi iddiasi gosterilmez.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.page_analysis import PageAnalysisSourceKind
from app.services.ai_interaction_heatmap import (
    INTERACTION_HEATMAP_DISCLAIMER,
    NO_STRONG_MATCH_WARNING,
    AIHotspotSelection,
    AIInteractionOutput,
    InteractionCandidate,
    InvalidInteractionOutputError,
    build_interaction_candidates,
    build_interaction_heatmap,
    compute_interaction_heatmap_fingerprint,
    rank_interaction_hotspots,
    validate_interaction_output,
)


def _candidate(
    candidate_id: str,
    label: str,
    *,
    kind: str = "cta_link",
    role: str = "link",
    x: float = 0.1,
    y: float = 0.2,
    w: float = 0.1,
    h: float = 0.03,
    above_fold: bool = True,
) -> InteractionCandidate:
    return InteractionCandidate(
        candidate_id=candidate_id,
        label=label,
        interaction_kind=kind,
        role=role,
        x=x,
        y=y,
        w=w,
        h=h,
        above_fold=above_fold,
    )


def _score_of(output: AIInteractionOutput, candidate_id: str) -> float:
    for hotspot in output.hotspots:
        if hotspot.candidate_id == candidate_id:
            return hotspot.score
    raise AssertionError(f"{candidate_id} hotspot listesinde yok")


# --- 1: hedef gorev onceligi -------------------------------------------------


def test_account_task_ranks_signup_above_bestsellers() -> None:
    candidates = [
        _candidate("candidate-1", "Uye ol", kind="button"),
        _candidate("candidate-2", "Cok Satanlar", kind="content_link"),
    ]
    output = rank_interaction_hotspots(candidates, target_task="Yeni hesap olustur")

    assert output.hotspots, "en az bir gorevle-ilgili aday bulunmali"
    assert output.hotspots[0].candidate_id == "candidate-1"
    assert output.hotspots[0].task_relevance == "direct"
    # "Cok Satanlar" gorevle ilgisiz -> hotspot listesine hic girmez.
    assert all(h.candidate_id != "candidate-2" for h in output.hotspots)


# --- 2: farkli hedef gorev -> farkli one cikan alan --------------------------


def test_bestsellers_task_surfaces_bestsellers_candidate() -> None:
    candidates = [
        _candidate("candidate-1", "Uye ol", kind="button"),
        _candidate("candidate-2", "Cok Satanlar", kind="content_link"),
    ]
    output = rank_interaction_hotspots(candidates, target_task="Cok satan urunleri incele")

    assert output.hotspots[0].candidate_id == "candidate-2"
    assert output.hotspots[0].task_relevance == "direct"


# --- 3: boyut tek basina oncelik veremez -------------------------------------


def test_large_irrelevant_card_cannot_outrank_task_match() -> None:
    candidates = [
        # Kucuk ama gorevle DOGRUDAN ilgili.
        _candidate("candidate-1", "Uye ol", kind="button", x=0.8, y=0.05, w=0.08, h=0.02),
        # Cok buyuk, ilk ekranda, ama gorevle ilgisiz jenerik urun karti.
        _candidate(
            "candidate-2",
            "One cikan gorsel",
            kind="image_link",
            x=0.0,
            y=0.0,
            w=0.9,
            h=0.6,
            above_fold=True,
        ),
    ]
    output = rank_interaction_hotspots(candidates, target_task="Hesap olustur")

    assert output.hotspots[0].candidate_id == "candidate-1"
    # Buyuk kart gorevle ilgisiz oldugu icin bir hotspot bile olusturmaz.
    assert all(h.candidate_id != "candidate-2" for h in output.hotspots)


# --- 4: bilinmeyen candidate_id reddedilir -----------------------------------


def test_unknown_candidate_id_is_rejected() -> None:
    candidates = [_candidate("candidate-1", "Uye ol", kind="button")]
    bogus = AIInteractionOutput(
        summary="x",
        hotspots=[
            AIHotspotSelection(
                candidate_id="candidate-999",
                score=0.9,
                confidence="high",
                reason="uydurma",
                task_relevance="direct",
            )
        ],
        unmatched_task_warning=None,
    )
    with pytest.raises(InvalidInteractionOutputError):
        validate_interaction_output(bogus, candidates)


def test_duplicate_candidate_id_is_rejected() -> None:
    candidates = [_candidate("candidate-1", "Uye ol", kind="button")]
    dup = AIInteractionOutput(
        summary="x",
        hotspots=[
            AIHotspotSelection(
                candidate_id="candidate-1", score=0.9, confidence="high", reason="a", task_relevance="direct"
            ),
            AIHotspotSelection(
                candidate_id="candidate-1", score=0.8, confidence="medium", reason="b", task_relevance="related"
            ),
        ],
        unmatched_task_warning=None,
    )
    with pytest.raises(InvalidInteractionOutputError):
        validate_interaction_output(dup, candidates)


# --- 5: AI koordinat dondurse bile sema reddeder -----------------------------


def test_ai_supplied_coordinates_are_rejected_by_schema() -> None:
    with pytest.raises(ValidationError):
        AIHotspotSelection.model_validate(
            {
                "candidate_id": "candidate-1",
                "score": 0.5,
                "confidence": "high",
                "reason": "koordinat sizdirma denemesi",
                "task_relevance": "direct",
                "x": 0.42,
                "y": 0.42,
                "w": 0.1,
                "h": 0.1,
            }
        )


# --- 6: guclu eslesme yoksa hotspot uydurulmaz -------------------------------


def test_no_matching_candidate_leaves_map_empty_with_warning() -> None:
    candidates = [
        _candidate("candidate-1", "Cok Satanlar", kind="content_link"),
        _candidate("candidate-2", "Blog yazilari", kind="content_link"),
    ]
    output = rank_interaction_hotspots(candidates, target_task="Yeni hesap olustur")

    assert output.hotspots == []
    assert output.unmatched_task_warning == NO_STRONG_MATCH_WARNING


# --- 10/11: fingerprint determinizmi ve gorev-duyarliligi --------------------


def test_fingerprint_is_deterministic_for_same_inputs() -> None:
    candidates = [_candidate("candidate-1", "Uye ol", kind="button")]
    kwargs = dict(
        content_sha256="abc123",
        target_task="Hesap olustur",
        target_audience="Yeni kullanicilar",
        persona_distribution={"genc": 0.5, "yetiskin": 0.5},
        candidates=candidates,
        user_confirmed_candidate_id=None,
        model_name="dom-visual-ranker",
    )
    assert compute_interaction_heatmap_fingerprint(**kwargs) == compute_interaction_heatmap_fingerprint(**kwargs)


def test_changing_target_task_changes_fingerprint_and_result() -> None:
    candidates = [
        _candidate("candidate-1", "Uye ol", kind="button"),
        _candidate("candidate-2", "Cok Satanlar", kind="content_link"),
    ]
    base = dict(
        candidates=candidates,
        target_audience=None,
        persona_distribution=None,
        content_sha256="sha",
        confirmed_candidate_id=None,
        model_name="dom-visual-ranker",
        provider_name="deterministic",
    )
    account = build_interaction_heatmap(target_task="Yeni hesap olustur", **base)
    bestsellers = build_interaction_heatmap(target_task="Cok satan urunleri incele", **base)

    assert account.fingerprint != bestsellers.fingerprint
    assert account.hotspots[0].candidate_id != bestsellers.hotspots[0].candidate_id


def test_same_fingerprint_selector_called_once_per_build() -> None:
    """Cache katmani (DB) ayni fingerprint icin ikinci uretimi engeller; saf
    katmanda tek bir build tek bir secici cagrisi yapar (ikinci provider cagrisi
    yalnizca fingerprint DEGISIRSE gerekir)."""

    candidates = [_candidate("candidate-1", "Uye ol", kind="button")]
    calls = {"n": 0}

    def selector(cands, task, confirmed_id):  # noqa: ANN001, ANN202
        calls["n"] += 1
        return rank_interaction_hotspots(cands, target_task=task, confirmed_candidate_id=confirmed_id)

    build_interaction_heatmap(
        candidates=candidates,
        target_task="Hesap olustur",
        target_audience=None,
        persona_distribution=None,
        content_sha256="sha",
        confirmed_candidate_id=None,
        model_name="m",
        provider_name="p",
        selector=selector,
    )
    assert calls["n"] == 1


# --- 14: gercek tiklama/goz takibi iddiasi yok -------------------------------


def test_result_carries_synthetic_disclaimer_not_real_click_claim() -> None:
    candidates = [_candidate("candidate-1", "Uye ol", kind="button")]
    result = build_interaction_heatmap(
        candidates=candidates,
        target_task="Hesap olustur",
        target_audience=None,
        persona_distribution=None,
        content_sha256="sha",
        confirmed_candidate_id=None,
        model_name="dom-visual-ranker",
        provider_name="deterministic",
    )
    assert result.disclaimer == INTERACTION_HEATMAP_DISCLAIMER
    assert "göz takibi verisi değildir" in result.disclaimer
    assert "sentetik" in result.disclaimer.lower()
    assert result.method_label == "DOM ve görsel özellik destekli AI sıralaması"


# --- kullanici onayli CTA guclu kanittir -------------------------------------


def test_user_confirmed_candidate_gets_bonus_but_others_still_evaluated() -> None:
    candidates = [
        _candidate("candidate-1", "Secilen CTA", kind="user_confirmed_cta", role="user_confirmed"),
        _candidate("candidate-2", "Uye ol", kind="button"),
    ]
    output = rank_interaction_hotspots(
        candidates, target_task="Cok satan urunleri incele", confirmed_candidate_id="candidate-1"
    )
    # Onaylı aday gorevle ilgisiz olsa da (task none) bonus sayesinde listelenir.
    assert any(h.candidate_id == "candidate-1" for h in output.hotspots)


# --- aday cikarici (DOM) -----------------------------------------------------


def test_build_candidates_from_dom_features_assigns_stable_ids() -> None:
    features = {
        "element_boxes": [
            {"role": "button", "label": "Uye ol", "interaction_kind": "button", "x": 100, "y": 40, "width": 120, "height": 36},
            {"role": "link", "label": "Cok Satanlar", "interaction_kind": "content_link", "x": 100, "y": 400, "width": 140, "height": 24},
            # container_link -> disarida birakilir.
            {"role": "link", "label": "Sarmalayici", "interaction_kind": "container_link", "x": 0, "y": 0, "width": 1200, "height": 900},
        ]
    }
    candidates, confirmed = build_interaction_candidates(
        features=features,
        image_width=1280,
        image_height=1600,
        source_kind=PageAnalysisSourceKind.URL,
    )
    assert confirmed is None
    labels = [(c.candidate_id, c.label) for c in candidates]
    assert labels == [("candidate-1", "Uye ol"), ("candidate-2", "Cok Satanlar")]
    # Fraksiyon koordinatlar [0,1] araliginda.
    assert all(0.0 <= c.x <= 1.0 and 0.0 <= c.w <= 1.0 for c in candidates)
