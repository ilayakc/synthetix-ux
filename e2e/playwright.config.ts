import { defineConfig, devices } from "@playwright/test";

// Varsayilan: host'tan, izole compose.e2e.env stack'inin yayinladigi
// portla (bkz. docs/testing.md "E2E"). `scripts/verify.ps1`, Playwright'i
// resmi Docker imajiyla (host'ta Node.js gerektirmemek icin) calistirdiginda
// `E2E_BASE_URL`'i Docker Desktop'in host gateway'i uzerinden ayni portla
// (`http://host.docker.internal:5273`) ayarlar - boylece tarayici, gercek
// bir host tarayicisiyla AYNI "site"ten (ayni ana bilgisayar adi, farkli
// port) erismis gibi davranir ve SameSite=Lax oturum cookie'leri calisir.
const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:5273";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  // Vite'in dev sunucusu (unbundled ESM) her sayfa gecisinde modulleri
  // istek uzerine derler; bir sayfaya ilk kez ugrarken (henuz derlenmemis
  // modullerle) bu, Docker Desktop altinda degisken/yavas olabilir. Bir
  // kerelik yeniden deneme, bu ortam kaynakli gecikmeyi gercek bir
  // regresyonla karistirmadan absorbe eder.
  retries: 1,
  workers: 1,
  timeout: 45_000,
  reporter: [["list"]],
  globalSetup: "./global-setup.ts",
  globalTeardown: "./global-teardown.ts",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    navigationTimeout: 15_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
