import { test, expect } from "@playwright/test";
import { registerNewOrganization } from "./helpers";

test.describe("Kayit", () => {
  test("yeni sirket 0 Chip ve 2 ucretsiz hak ile baslar", async ({ page }) => {
    await registerNewOrganization(page);

    await page.goto("/kullanim-ve-chip");

    await expect(page.getByRole("heading", { name: "Chip Bakiyesi" })).toBeVisible();
    // Chip Bakiyesi karti 0 gostermeli.
    await expect(page.locator(".dashboard-card", { hasText: "Chip Bakiyesi" })).toContainText("0");

    // Iki ucretsiz hak: temel UX testi ve erisilebilirlik on kontrolu.
    await expect(
      page.locator(".dashboard-card", { hasText: "Ücretsiz Temel UX Testi" }),
    ).toContainText("Kullanılabilir");
    await expect(
      page.locator(".dashboard-card", { hasText: "Ücretsiz Erişilebilirlik Ön Kontrolü" }),
    ).toContainText("Kullanılabilir");
  });
});
