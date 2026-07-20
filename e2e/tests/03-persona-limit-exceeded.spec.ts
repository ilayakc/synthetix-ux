import { test, expect } from "@playwright/test";
import { createProject, fillBasicUxWizardThroughSummary, registerNewOrganization } from "./helpers";
import { FIXTURE_SITE_URL } from "../fixtures/site/config";

test.describe("1.000 persona ucretsiz sinirinin asilmasi", () => {
  test("1.001 persona ile baslatma, 0 Chip bakiyesiyle reddedilir", async ({ page }) => {
    await registerNewOrganization(page);

    const projectName = `E2E Sinir Projesi ${Date.now()}`;
    await createProject(page, projectName);

    await fillBasicUxWizardThroughSummary(page, {
      projectName,
      personaCount: 1001,
      fixtureUrl: FIXTURE_SITE_URL,
    });

    await page.getByRole("button", { name: "Chip ile başlat" }).click();

    // Sirket 0 Chip bakiyesiyle basladigi icin (bkz. docs/product-rules.md),
    // 1.000 uzerindeki her test Chip gerektirir ve bakiye yetersizdir.
    await expect(page.getByText("Chip bakiyeniz bu testi başlatmak için yeterli değil")).toBeVisible();
    // Sihirbaz "Test başlatıldı" ekranina GECMEMELI; taslak hala aktif adimda.
    await expect(page.getByText("Test başlatıldı")).toHaveCount(0);
  });
});
