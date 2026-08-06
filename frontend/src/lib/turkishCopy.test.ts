import { describe, expect, it } from "vitest";
import { normalizeTurkishSystemCopy } from "./turkishCopy";

describe("normalizeTurkishSystemCopy", () => {
  it("eski aktivite açıklamalarını düzgün Türkçeye çevirir", () => {
    expect(normalizeTurkishSystemCopy("AI pipeline sonucu: tum varyantlar basarili")).toBe(
      "AI işlem hattı sonucu: tüm varyantlar başarılı",
    );
    expect(normalizeTurkishSystemCopy("AB/grup karsilastirmasi tuketildi")).toBe(
      "A/B grup karşılaştırması",
    );
  });

  it("sürümlenmiş analiz modülü metinlerini düzeltir", () => {
    expect(
      normalizeTurkishSystemCopy(
        "Mevcut bir sitenin hedef gorev akisini sentetik persona kohortlariyla degerlendiren temel kullanilabilirlik testi.",
      ),
    ).toBe(
      "Mevcut bir sitenin hedef görev akışını sentetik persona kohortlarıyla değerlendiren temel kullanılabilirlik testi.",
    );
  });
});
