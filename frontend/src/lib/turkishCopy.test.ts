import { describe, expect, it } from "vitest";
import { normalizeTurkishSystemCopy } from "./turkishCopy";

describe("normalizeTurkishSystemCopy", () => {
  it("eski aktivite açıklamalarını düzgün Türkçeye çevirir", () => {
    expect(normalizeTurkishSystemCopy("AI pipeline sonucu: tum varyantlar basarili")).toBe(
      "AI raporu tamamlandı",
    );
    expect(normalizeTurkishSystemCopy("AB/grup karsilastirmasi tuketildi")).toBe(
      "A/B grup karşılaştırması",
    );
  });

  it("eski simülasyon hata metinlerini okunabilir hale getirir", () => {
    expect(
      normalizeTurkishSystemCopy(
        "Bagli sayfa analizi basarisiz oldugu icin bu calistirma islenemedi",
      ),
    ).toBe("Bağlı sayfa analizi başarısız olduğu için bu çalıştırma işlenemedi");
    expect(
      normalizeTurkishSystemCopy(
        "simulasyon yeniden deneme: 02250d20-2124-4990-9a11-cfc23067f119",
      ),
    ).toBe("Simülasyon yeniden denendi");
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
