import type { FullConfig } from "@playwright/test";
import { startFixtureSite } from "./fixtures/site/server.mjs";
import { FIXTURE_SITE_PORT } from "./fixtures/site/config";

// Playwright'in `globalSetup`/`globalTeardown`'i ayni Node surecinde
// calisir; sunucu referansi bu global uzerinden tasinir.
declare global {
  // eslint-disable-next-line no-var
  var __fixtureSiteServer: import("node:http").Server | undefined;
}

export default async function globalSetup(_config: FullConfig) {
  globalThis.__fixtureSiteServer = await startFixtureSite(FIXTURE_SITE_PORT);
}
