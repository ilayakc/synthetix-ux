export default async function globalTeardown() {
  const server = globalThis.__fixtureSiteServer;
  if (server) {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}
