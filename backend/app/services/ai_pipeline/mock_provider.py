"""Deterministik `MockAIProvider` (Faz 2B).

Bu saglayici, gercek bir LLM cagrisi YAPMADAN, yalnizca girdinin saf bir
fonksiyonu olarak deterministik/sablon tabanli cikti uretir. Ayni
`(stage_type, prompt, input_payload)` HER ZAMAN byte-for-byte ayni ciktiyi
verir.

KRITIK PURITY KURALLARI (testlerle dogrulanir): bu modul ag/tasima
kutuphaneleri import etmez, dosya/ortam degiskeni okumaz ve sozde-rastgele
uretec kullanmaz. Tum "rastgelelik" yalnizca Faz 2A'nin `hash_payload`
fonksiyonundan turetilir (Python builtin karma fonksiyonu kararli degildir,
KULLANILMAZ).
"""

from __future__ import annotations

import time

from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline import validation as ai_validation
from app.services.ai_pipeline.errors import AIPipelineError
from app.services.ai_pipeline.hashing import PromptDescriptor, hash_payload
from app.services.ai_pipeline.provider import (
    ProviderResult,
    compute_configuration_fingerprint,
    validate_stage_contract,
)
from app.services.ai_pipeline.provider_errors import AIProviderInvalidOutputError
from app.services.ai_pipeline.schemas import (
    UX_REPORT_DISCLAIMER,
    FindingPriority,
    FrictionHypothesis,
    LikelyCompletion,
    PersonaBehaviorBatchOutput,
    PersonaBehaviorEstimate,
    ScenarioInterpretation,
    TaskStep,
    UXFinding,
    UXReport,
)
from app.services.ai_pipeline.stage_inputs import (
    PersonaBehaviorBatchInput,
    ScenarioInterpretationInput,
    UXReportInput,
)

# "no need" sentinel bucket'i (bkz. app.services.personas ACCESSIBILITY_NEED
# preset'i - "none" = Belirtilmemis). Bu deger erisilebilirlik ihtiyaci
# YOK anlamina gelir.
_ACCESSIBILITY_NO_NEED_SENTINELS = frozenset({"none", "yok", ""})


def _stable_int(payload: object, salt: str = "") -> int:
    """Deterministik, kararli bir tamsayi uretir - builtin karma veya
    sozde-rastgele uretec YERINE yalnizca `hash_payload` kullanir."""

    return int(hash_payload({"payload": payload, "salt": salt})[:8], 16)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


class MockAIProvider:
    """Testler ve gelistirme icin deterministik, ag-siz, DB-siz AI saglayicisi.

    `AIProvider` protokolunu uygular. Gercek bir provider'in aksine, ciktisi
    tamamen girdinin fonksiyonudur ve her zaman kendi ic cross-validasyonunu
    da yapar (yine de executor'in kendi bagimsiz re-validasyonu ayrica calisir).
    """

    provider_name = "mock"
    model_name = "deterministic-template-v1"
    is_mock = True
    # Sabit/deterministik - Mock'ta gercek reasoning effort/structured-output
    # surumu yoktur, bu nedenle sabit etiketler kullanilir (bkz. AIProvider.
    # configuration_fingerprint dokstring'i). Her `MockAIProvider` orneginde
    # AYNI deger uretilir.
    configuration_fingerprint = compute_configuration_fingerprint(
        provider_name="mock",
        model_name="deterministic-template-v1",
        reasoning_effort="deterministic",
        structured_output_mode="mock-template-v1",
    )

    async def generate_structured(
        self,
        *,
        stage_type: AIPipelineStageType,
        batch_index: int | None,
        prompt: PromptDescriptor,
        input_payload: object,
        output_schema: type,
    ) -> ProviderResult:
        validate_stage_contract(
            stage_type=stage_type,
            input_payload=input_payload,  # type: ignore[arg-type]
            output_schema=output_schema,
        )
        start = time.perf_counter()
        if stage_type is AIPipelineStageType.SCENARIO_INTERPRETATION:
            output: object = self._generate_scenario(input_payload)  # type: ignore[arg-type]
        elif stage_type is AIPipelineStageType.PERSONA_BEHAVIOR:
            output = self._generate_persona_behavior(input_payload, batch_index)  # type: ignore[arg-type]
        else:
            output = self._generate_ux_report(input_payload)  # type: ignore[arg-type]
        duration_ms = int(round((time.perf_counter() - start) * 1000))
        return ProviderResult(
            output=output,
            provider_name=self.provider_name,
            model_name=self.model_name,
            is_mock=True,
            configuration_fingerprint=self.configuration_fingerprint,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=0.0,
            request_duration_ms=duration_ms,
            provider_request_id=None,
        )

    # --- Stage 3 -----------------------------------------------------------------

    def _generate_scenario(self, input_payload: ScenarioInterpretationInput) -> ScenarioInterpretation:
        evidence_ids = sorted(input_payload.evidence.evidence_ids())
        target_task = _truncate(input_payload.target_task, 200)

        # Evidence az ise 1, yeterliyse 2 adim (en fazla birkac).
        step_count = 1 if len(evidence_ids) < 2 else 2
        steps: list[TaskStep] = []
        for n in range(1, step_count + 1):
            if n == 1:
                refs = tuple(evidence_ids[:2]) if len(evidence_ids) >= 2 else (evidence_ids[0],)
            else:
                refs = (evidence_ids[-1],)
            steps.append(
                TaskStep(
                    step_id=f"step:{n}",
                    instruction=_truncate(
                        f"'{target_task}' hedefine ulasmak icin sayfadaki ilgili ana eylemi "
                        f"gerceklestir (adim {n}).",
                        500,
                    ),
                    success_criterion=_truncate(
                        f"Adim {n} icin '{target_task}' yolunda beklenen ilerleme gozlenir.",
                        300,
                    ),
                    evidence_references=refs,
                )
            )

        # 0/1/2 hipotez (yeterli evidence referansi varsa).
        hypotheses: list[FrictionHypothesis] = []
        hypothesis_count = min(2, len(evidence_ids))
        for n in range(1, hypothesis_count + 1):
            evidence_id = evidence_ids[(n - 1) % len(evidence_ids)]
            hypotheses.append(
                FrictionHypothesis(
                    hypothesis_id=f"hyp:{n}",
                    description=_truncate(
                        f"Kanıt '{evidence_id}' ile ilişkili olası bir sürtünme noktası "
                        f"(sentetik tahmin, doğrulanmamış).",
                        500,
                    ),
                    evidence_references=(evidence_id,),
                )
            )

        limitations = (
            "Mock/şablon sağlayıcı deterministik senaryo yorumları üretir; "
            "bağlamsal çeşitliliği sınırlıdır."
        )
        if len(evidence_ids) < 2:
            limitations += " Kanıt kapsamı sınırlıdır; bu nedenle adım ve hipotez sayısı düşüktür."

        output = ScenarioInterpretation(
            steps=tuple(steps),
            success_criteria=(
                _truncate(f"'{target_task}' hedefi icin temel eylem tamamlanabilir olmalidir.", 300),
            ),
            friction_hypotheses=tuple(hypotheses),
            limitations=_truncate(limitations, 1000),
        )

        try:
            ai_validation.validate_scenario_interpretation(scenario=output, evidence=input_payload.evidence)
        except AIPipelineError as exc:
            raise AIProviderInvalidOutputError(str(exc)) from exc
        return output

    # --- Stage 4 -----------------------------------------------------------------

    def _generate_persona_behavior(
        self, input_payload: PersonaBehaviorBatchInput, batch_index: int | None
    ) -> PersonaBehaviorBatchOutput:
        batch_input_hash = hash_payload(input_payload)
        evidence_ids = sorted(input_payload.evidence.evidence_ids())
        hypothesis_ids = sorted(input_payload.scenario.hypothesis_ids())
        # Kontrast ile ilgili bir evidence var mi? (erisilebilirlik endisesini
        # bu somut evidence'a baglamak icin).
        contrast_evidence = next((eid for eid in evidence_ids if eid.startswith("metric:contrast")), None)

        results: list[PersonaBehaviorEstimate] = []
        for persona in input_payload.batch.personas:
            selector = _stable_int(batch_input_hash, salt=f"persona:{persona.index}")

            likely_completion = [
                LikelyCompletion.HIGH,
                LikelyCompletion.MEDIUM,
                LikelyCompletion.LOW,
            ][selector % 3]

            evidence_references = (evidence_ids[selector % len(evidence_ids)],)

            if hypothesis_ids and selector % 2 == 0:
                affected_hypothesis_ids: tuple[str, ...] = (hypothesis_ids[selector % len(hypothesis_ids)],)
            else:
                affected_hypothesis_ids = ()

            # Erisilebilirlik endisesi: yalnizca persona erisilebilirlik ihtiyaci
            # BEYAN ETMISSE ve somut bir kontrast evidence'i varsa. Persona'nin
            # yas/bolge/cihaz/yatkinlik gibi diger nitelikleri ASLA anilmaz.
            access_value = persona.attributes.get("accessibility_need", "")
            has_need = bool(access_value) and access_value.strip().lower() not in (
                _ACCESSIBILITY_NO_NEED_SENTINELS
            )
            if has_need and contrast_evidence is not None:
                accessibility_concerns: tuple[str, ...] = (
                    _truncate(
                        f"Erisilebilirlik ihtiyaci beyan eden personalar icin "
                        f"'{contrast_evidence}' ile iliskili olasi bir engel (sentetik tahmin).",
                        300,
                    ),
                )
            else:
                accessibility_concerns = ()

            confidence = 0.2 + (selector % 41) / 100

            results.append(
                PersonaBehaviorEstimate(
                    persona_index=persona.index,
                    likely_completion=likely_completion,
                    affected_hypothesis_ids=affected_hypothesis_ids,
                    accessibility_concerns=accessibility_concerns,
                    confidence=confidence,
                    evidence_references=evidence_references,
                )
            )

        output = PersonaBehaviorBatchOutput(
            batch_index=input_payload.batch.batch_index,
            persona_results=tuple(results),
            limitations=(
                "Mock/şablon sağlayıcı deterministik persona davranışı tahminleri üretir; "
                "bağlamsal çeşitliliği sınırlıdır."
            ),
        )

        try:
            ai_validation.validate_persona_behavior_batch(
                batch=input_payload.batch,
                output=output,
                evidence=input_payload.evidence,
                scenario=input_payload.scenario,
            )
        except AIPipelineError as exc:
            raise AIProviderInvalidOutputError(str(exc)) from exc
        return output

    # --- Stage 6 -----------------------------------------------------------------

    def _generate_ux_report(self, input_payload: UXReportInput) -> UXReport:
        evidence_ids = sorted(input_payload.evidence.evidence_ids())
        # WeightedIssue per-issue evidence referansi TASIMADIGI icin, jenerik
        # olarak evidence kumesinin ilk id'sini kullaniriz (uydurma per-issue
        # evidence izleme YAPILMAZ - bu bilincli bir sadelestirmedir).
        generic_reference = (evidence_ids[0],)

        issues = input_payload.aggregation.common_issues
        findings: list[UXFinding] = []

        # Bulgu YOKKEN sentetik bir "sorun yok" bulgusu UYDURMAYIZ; findings bos
        # kalir (bkz. urun kurali: sorun yoksa bulgu uretme). Bunun yerine durumu
        # summary/limitations icinde durustce belirtiriz.
        for issue in issues[:10]:
            if issue.affected_share >= 0.5:
                priority = FindingPriority.HIGH
            elif issue.affected_share >= 0.2:
                priority = FindingPriority.MEDIUM
            else:
                priority = FindingPriority.LOW

            percent = round(issue.affected_share * 100)
            findings.append(
                UXFinding(
                    finding_id=_truncate(f"finding:{issue.hypothesis_id}", 100),
                    priority=priority,
                    finding=_truncate(
                        f"'{_truncate(issue.description, 300)}' - %{percent} oraninda sentetik "
                        f"persona etkileniyor gibi gorunuyor (dogrulanmamis tahmin).",
                        500,
                    ),
                    source_stage="aggregation",
                    evidence_references=generic_reference,
                    estimated_affected_users=issue.affected_users,
                    recommendation=("Bu sürtünme noktasını gerçek kullanıcı testiyle doğrulamayı düşünün."),
                    confidence=issue.weighted_confidence,
                )
            )

        if findings:
            summary = f"{len(findings)} sentetik bulgu üretildi."
            limitations = (
                "Mock/şablon sağlayıcı deterministik çıktılar üretir; bağlamsal çeşitlilik "
                "ve genelleme kapasitesi sınırlıdır."
            )
        else:
            # Bilincli olarak bos findings: bu, sayfanin gercek kullanilabilirlik
            # sorunu OLMADIGI anlamina GELMEZ; yalnizca bu sentetik agregasyonun
            # agirlikli bir ortak sorun yuzeye cikarmadigini belirtir.
            summary = (
                "Bu sentetik çalışmada ağırlıklı bir ortak sorun tespit edilmedi; "
                "agregasyon aşaması raporlanabilir bir bulgu üretmedi."
            )
            limitations = (
                "Mock/şablon sağlayıcı deterministik çıktılar üretir. Bulgu bulunmaması, "
                "tasarımın sorunsuz olduğu veya kalite garantisi sunduğu anlamına gelmez."
            )

        output = UXReport(
            summary=_truncate(summary, 1000),
            findings=tuple(findings),
            limitations=_truncate(limitations, 1000),
            disclaimer=UX_REPORT_DISCLAIMER,
        )

        try:
            ai_validation.validate_ux_report(
                report=output,
                evidence=input_payload.evidence,
                total_population=input_payload.aggregation.total_population,
            )
        except AIPipelineError as exc:
            raise AIProviderInvalidOutputError(str(exc)) from exc
        return output


__all__ = ["MockAIProvider"]
