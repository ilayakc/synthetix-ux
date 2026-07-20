import { test, expect } from "@playwright/test";
import { createProject, fillBasicUxWizardThroughSummary, registerNewOrganization } from "./helpers";
import { FIXTURE_SITE_URL } from "../fixtures/site/config";

test.describe("Ucretsiz temel UX testi hakkinin tek kullanimlik olmasi", () => {
  test("ikinci temel UX testi denemesi, ilk ucretsiz hak tuketildikten sonra reddedilir", async ({
    page,
  }) => {
    await registerNewOrganization(page);

    const projectName = `E2E Tek Kullanim Projesi ${Date.now()}`;
    await createProject(page, projectName);

    // Ilk deneme: ucretsiz hakki tuketir.
    await fillBasicUxWizardThroughSummary(page, {
      projectName,
      personaCount: 500,
      fixtureUrl: FIXTURE_SITE_URL,
    });
    await page.getByRole("button", { name: "Ücretsiz hakkı kullan ve başlat" }).click();
    await expect(page.getByText("Test başlatıldı")).toBeVisible();

    // Ucretsiz hakkin artik kullanilamaz oldugunu Kullanim & Chip sayfasindan
    // dogrula. Launch aninda hak once REZERVE edilir; worker calistirmayi
    // isleyip basariyla tamamlayana kadar durum "Rezerve edildi" gorunebilir,
    // ancak her iki durumda da (rezerve/kullanildi) hak artik AVAILABLE
    // degildir - asil kural (tek kullanimlik) asagidaki ikinci deneme
    // reddiyle kanitlanir.
    await page.goto("/kullanim-ve-chip");
    await expect(
      page.locator(".dashboard-card", { hasText: "Ücretsiz Temel UX Testi" }),
    ).toContainText(/Rezerve edildi|Kullanıldı/);

    // Ikinci deneme: ayni ucretsiz hakki tekrar kullanmaya calisir; sirket
    // hala 0 Chip bakiyesinde oldugu icin bu istek reddedilmelidir.
    await fillBasicUxWizardThroughSummary(page, {
      projectName,
      personaCount: 500,
      fixtureUrl: FIXTURE_SITE_URL,
    });
    await page.getByRole("button", { name: "Chip ile başlat" }).click();

    await expect(page.getByText("Chip bakiyeniz bu testi başlatmak için yeterli değil")).toBeVisible();
    await expect(page.getByText("Test başlatıldı")).toHaveCount(0);
  });
});
