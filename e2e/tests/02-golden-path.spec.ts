import { test, expect } from "@playwright/test";
import { createProject, fillBasicUxWizardThroughSummary, registerNewOrganization } from "./helpers";
import { FIXTURE_SITE_URL } from "../fixtures/site/config";

test.describe("Altin yol: kayit -> proje -> 1000 persona ucretsiz test -> simulasyon -> rapor", () => {
  test("ucretsiz temel UX testi baslatilir ve rapor uretilir", async ({ page }) => {
    await registerNewOrganization(page);

    const projectName = `E2E Proje ${Date.now()}`;
    await createProject(page, projectName);

    await fillBasicUxWizardThroughSummary(page, {
      projectName,
      personaCount: 1000,
      fixtureUrl: FIXTURE_SITE_URL,
    });

    await page.getByRole("button", { name: "Ücretsiz hakkı kullan ve başlat" }).click();

    await expect(page.getByText("Test başlatıldı")).toBeVisible();
    await expect(page.getByText("Test, ücretsiz hakkınız kullanılarak başlatıldı.")).toBeVisible();

    // Simulasyon arka planda (worker) islenir; rapor listesinde belirene
    // kadar sayfayi periyodik olarak yeniden yukleyerek bekleriz.
    await page.goto("/raporlar");
    await expect(async () => {
      await page.reload();
      await expect(page.locator(".report-list-item").first()).toBeVisible();
    }).toPass({ timeout: 30_000, intervals: [1_000] });

    await page.locator(".report-list-item").first().click();

    // Bilimsel durustluk: sentetik/uncalibre uyari her raporda gorunur olmali.
    await expect(page.getByText(/sentetik/i).first()).toBeVisible();
  });
});
