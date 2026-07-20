import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // Vite 6, DNS-rebinding saldirilarina karsi taniinmayan `Host`
    // basliklariyla gelen istekleri varsayilan olarak reddeder.
    // "host.docker.internal", yalnizca izole E2E docker compose stack'i
    // icinde (bkz. compose.e2e.env, e2e/playwright.config.ts) Playwright'in
    // Docker Desktop'in host gateway'i uzerinden erisimi icin acikca izin
    // verilir; production build bu dev-server ayarini hic kullanmaz.
    allowedHosts: ["host.docker.internal"],
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/setupTests.ts",
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      exclude: [
        "**/*.test.tsx",
        "src/setupTests.ts",
        "src/main.tsx",
        "src/vite-env.d.ts",
        "src/test/**",
      ],
      // Baslangic esikleri, bu asamada eklenen testlerle FIILEN ulasilan
      // kapsamin biraz altina (guvenlik payi) ayarlanmistir; amac gercekci
      // bir regresyon kapisi olmasidir. Buyuk, henuz test edilmemis sayfalar
      // (PersonaPresets, ProjectDetail, Simulations, PersonaDistributionEditor)
      // sonraki asamalarda test kapsamina alindikca bu esikler kademeli
      // olarak yukseltilebilir.
      thresholds: {
        lines: 60,
        statements: 60,
        functions: 55,
        branches: 65,
      },
    },
  },
});
