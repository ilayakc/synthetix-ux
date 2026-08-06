import { describe, expect, it } from "vitest";
import type { WizardDraftPayload } from "../../api/client";
import {
  AI_REPORT_MODULE_KEY,
  aiReportRequiresPersonaSelection,
  hasPersonaSelection,
} from "./aiReportPersona";

describe("aiReportPersona", () => {
  it("modul anahtari backend ile ayni ('ai_report')", () => {
    expect(AI_REPORT_MODULE_KEY).toBe("ai_report");
  });

  describe("hasPersonaSelection", () => {
    it("preset secilmisse true", () => {
      expect(hasPersonaSelection({ persona_preset_id: "builtin:general_web_users" })).toBe(true);
    });

    it("bos olmayan ozel dagilim secilmisse true", () => {
      const payload: WizardDraftPayload = {
        persona_distribution: { device_class: [{ key: "mobile", label: "Mobil", weight: 50 }] },
      };
      expect(hasPersonaSelection(payload)).toBe(true);
    });

    it("hicbir secim yoksa false", () => {
      expect(hasPersonaSelection({})).toBe(false);
    });

    it("BOS bir ozel dagilim ({}) 'secim yok' sayilir (backend ile ayni truthiness)", () => {
      expect(hasPersonaSelection({ persona_distribution: {} })).toBe(false);
    });
  });

  describe("aiReportRequiresPersonaSelection", () => {
    it("ai_report secili + persona secimi yok -> true (engellenir)", () => {
      expect(aiReportRequiresPersonaSelection({ modules: ["ai_report"] })).toBe(true);
    });

    it("ai_report secili + bos dagilim ({}) -> true (engellenir)", () => {
      expect(
        aiReportRequiresPersonaSelection({ modules: ["ai_report"], persona_distribution: {} }),
      ).toBe(true);
    });

    it("ai_report secili + preset -> false (devam edilebilir)", () => {
      expect(
        aiReportRequiresPersonaSelection({
          modules: ["ai_report"],
          persona_preset_id: "builtin:general_web_users",
        }),
      ).toBe(false);
    });

    it("ai_report secili + ozel dagilim -> false (devam edilebilir)", () => {
      expect(
        aiReportRequiresPersonaSelection({
          modules: ["ai_report"],
          persona_distribution: { device_class: [{ key: "mobile", label: "Mobil", weight: 50 }] },
        }),
      ).toBe(false);
    });

    it("ai_report SECILI DEGIL + persona secimi yok -> false (temel akis etkilenmez)", () => {
      expect(aiReportRequiresPersonaSelection({ modules: ["network_device_test"] })).toBe(false);
      expect(aiReportRequiresPersonaSelection({ modules: [] })).toBe(false);
      expect(aiReportRequiresPersonaSelection({})).toBe(false);
    });
  });
});
