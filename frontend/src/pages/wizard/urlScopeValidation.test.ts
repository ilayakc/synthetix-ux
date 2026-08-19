import { describe, expect, it } from "vitest";
import { analysisUrlScopeRejectionReason } from "./urlScopeValidation";

describe("analysisUrlScopeRejectionReason", () => {
  it.each([
    "https://www.youtube.com/watch?v=abc",
    "https://subdomain.instagram.com/example",
    "https://www.google.com/maps/place/example",
    "https://www.amazon.com/product/example",
    "https://www.trendyol.com/example",
    "https://example.com/catalog.pdf?download=1",
  ])("öngörülebilir ağır veya desteklenmeyen URL'yi reddeder: %s", (url) => {
    expect(analysisUrlScopeRejectionReason(url)).toMatch(/ücretsiz demo kapasitesinin dışında/);
  });

  it.each([
    "https://example.com",
    "https://company.example/landing-page",
    "https://shop.example/products/example",
  ])("standart HTML sayfasına izin verir: %s", (url) => {
    expect(analysisUrlScopeRejectionReason(url)).toBeNull();
  });

  it("biçim hatasını mevcut URL doğrulamasına bırakır", () => {
    expect(analysisUrlScopeRejectionReason("not-a-url")).toBeNull();
  });
});
