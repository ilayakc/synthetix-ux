// Bagimliliksiz, kucuk bir statik dosya sunucusu (yalnizca Node'un yerlesik
// `http`/`fs` modulleri). E2E testlerinde sihirbazin URL alanina yazilan
// deger bu yerel adrese isaret eder; boylece testler HICBIR gercek dis
// internet adresine bagimli olmaz. Yalnizca Playwright globalSetup
// tarafindan baslatilir/durdurulur; production kodunun hic bir parcasi
// degildir.

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const indexPath = path.join(__dirname, "index.html");

export function startFixtureSite(port) {
  return new Promise((resolve, reject) => {
    const server = createServer(async (_req, res) => {
      try {
        const body = await readFile(indexPath, "utf-8");
        res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        res.end(body);
      } catch (error) {
        res.writeHead(500);
        res.end(String(error));
      }
    });

    server.on("error", reject);
    server.listen(port, "127.0.0.1", () => resolve(server));
  });
}

// Dogrudan `node server.mjs` ile calistirildiginda da ayakta kalir
// (ManuelWebServer / hata ayiklama icin).
if (import.meta.url === `file://${process.argv[1]}`) {
  const port = Number(process.env.FIXTURE_SITE_PORT ?? 4173);
  await startFixtureSite(port);
  console.log(`Fixture site listening on http://127.0.0.1:${port}`);
}
