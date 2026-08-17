import { describe, expect, it } from "vitest";

import { sanitizeDraftForAutosave } from "./autosave";

describe("sanitizeDraftForAutosave", () => {
  it("bos/gecersiz hedef gorevi autosave PATCH'inden cikarir", () => {
    // Bos hedef gorev backend'e gonderilmez (beklenen 422 gurultu uretmesin).
    expect(sanitizeDraftForAutosave({ target_task: "   " })).not.toHaveProperty("target_task");
    // Yalnizca noktalama iceren anlamsiz hedef gorev de gonderilmez.
    expect(sanitizeDraftForAutosave({ target_task: "..." })).not.toHaveProperty("target_task");
  });

  it("gecerli hedef gorevi korur", () => {
    const valid = "Kirmizi spor ayakkabiyi bul ve sepete ekle";
    expect(sanitizeDraftForAutosave({ target_task: valid }).target_task).toBe(valid);
  });

  it("gecersiz/eksik URL'leri autosave PATCH'inden cikarir", () => {
    expect(sanitizeDraftForAutosave({ current_url: "" })).not.toHaveProperty("current_url");
    expect(sanitizeDraftForAutosave({ current_url: "not-a-url" })).not.toHaveProperty(
      "current_url",
    );
    expect(sanitizeDraftForAutosave({ new_url: "ftp://x" })).not.toHaveProperty("new_url");
  });

  it("gecerli http(s) URL'leri korur ve ilgisiz alanlara dokunmaz", () => {
    const result = sanitizeDraftForAutosave({
      current_url: "https://example.com",
      name: "Test",
      persona_count: 1000,
    });
    expect(result.current_url).toBe("https://example.com");
    expect(result.name).toBe("Test");
    expect(result.persona_count).toBe(1000);
  });
});
