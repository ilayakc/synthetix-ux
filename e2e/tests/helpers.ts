import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";

export function uniqueEmail(): string {
  return `e2e-${Date.now()}-${Math.random().toString(16).slice(2, 8)}@example.com`;
}

export interface RegisteredUser {
  email: string;
  password: string;
  organizationName: string;
}

/** Kayit formunu doldurup gonderir; basari halinde Dashboard'a yonlendirilir. */
export async function registerNewOrganization(page: Page): Promise<RegisteredUser> {
  const user: RegisteredUser = {
    email: uniqueEmail(),
    password: "CorrectHorse123!",
    organizationName: `E2E Org ${Date.now()}`,
  };

  await page.goto("/kayit");
  await page.getByLabel("Şirket / organizasyon adı").fill(user.organizationName);
  await page.getByLabel("E-posta").fill(user.email);
  await page.getByLabel("Parola").fill(user.password);
  await page.getByRole("button", { name: "Hesap oluştur" }).click();

  await expect(page).toHaveURL("/");
  return user;
}

export async function createProject(page: Page, name: string): Promise<void> {
  await page.goto("/projeler");
  // Bos durumda (henuz hic proje yokken) buton metni "İlk projenizi
  // oluşturun", sonraki projelerde ise "Yeni Proje" olur (bkz. Projects.tsx).
  const emptyStateButton = page.getByRole("button", { name: "İlk projenizi oluşturun" });
  const newProjectButton = page.getByRole("button", { name: "Yeni Proje" });
  await expect(emptyStateButton.or(newProjectButton)).toBeVisible();
  if (await emptyStateButton.isVisible()) {
    await emptyStateButton.click();
  } else {
    await newProjectButton.click();
  }
  await page.getByLabel("Proje adı").fill(name);
  await page.getByRole("button", { name: "Oluştur", exact: true }).click();
  await expect(page.getByText(name).first()).toBeVisible();
}

/**
 * Sihirbazi acar, verilen persona sayisiyla temel UX testi taslagini
 * doldurur ve 5. adima (ozet) kadar ilerler. Baslatma (launch) islemini
 * cagirana birakir.
 */
export async function fillBasicUxWizardThroughSummary(
  page: Page,
  options: { projectName: string; personaCount: number; fixtureUrl: string },
): Promise<void> {
  await page.goto("/tests/new");
  // Vite dev sunucusu bu sayfanin modullerini (Step1..Step5, WizardStepper)
  // ilk ziyarette istek uzerine derler; etkilesimden once agin durulmasini
  // beklemek, sonraki `getByLabel(...)` cagrilarinin daha kararli calismasini
  // saglar (bkz. playwright.config.ts "retries" notu).
  await page.waitForLoadState("networkidle");

  // Adim 1: detaylar
  await page.getByLabel("Proje").selectOption({ label: options.projectName });
  await page.getByLabel("Test adı").fill("E2E temel UX testi");
  await page.getByLabel("Hedef görev").fill("Kullanıcının ana akışı tamamlaması");
  await page.getByLabel("Mevcut site: temel UX testi").check();
  await page.getByRole("button", { name: "İleri" }).click();

  // Adim 2: URL (yalnizca yerel fixture sitesi; gercek internet adresine
  // bagimlilik yok)
  await page.getByLabel("Test edilecek URL").fill(options.fixtureUrl);
  await page.getByRole("button", { name: "İleri" }).click();

  // Adim 3: personalar
  await page.getByLabel(/Persona sayısı/).fill(String(options.personaCount));
  await page.getByLabel("Hedef kitle").fill("Yeni B2B müşteri adayları");
  await page.getByRole("button", { name: "İleri" }).click();

  // Adim 4: modüller (hicbiri secilmez, ücretsiz kapsamda kalinir)
  await page.getByRole("button", { name: "İleri" }).click();

  // Adim 5: ozet + yetki onayi
  await page
    .getByLabel(/Bu URL'leri test etme yetkisine sahip olduğumu onaylıyorum/)
    .check();
}
