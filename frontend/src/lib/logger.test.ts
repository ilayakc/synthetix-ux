import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { formatDuration, logTiming, logger, maskSensitive, setLogLevel } from "./logger";

describe("formatDuration", () => {
  it("milisaniye altindaki sureleri ms olarak bicimler", () => {
    expect(formatDuration(2.34)).toBe("2.3 ms");
  });

  it("1 saniye ve ustundeki sureleri s olarak bicimler", () => {
    expect(formatDuration(1350)).toBe("1.35 s");
  });
});

describe("maskSensitive", () => {
  it("bilinen hassas alan adlarini maskeler", () => {
    const masked = maskSensitive({ password: "hunter2", email: "a@b.com" });
    expect(masked.password).toBe("***");
    expect(masked.email).toBe("a@b.com");
  });

  it("ic ice nesnelerde de (recursive) maskeler", () => {
    const masked = maskSensitive({
      Authorization: "Bearer xyz",
      nested: { api_key: "abc", note: "ok" },
    });
    expect(masked.Authorization).toBe("***");
    expect((masked.nested as Record<string, unknown>).api_key).toBe("***");
    expect((masked.nested as Record<string, unknown>).note).toBe("ok");
  });
});

describe("logger", () => {
  let infoSpy: ReturnType<typeof vi.spyOn>;
  let warnSpy: ReturnType<typeof vi.spyOn>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    infoSpy = vi.spyOn(console, "info").mockImplementation(() => {});
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    setLogLevel("DEBUG");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    setLogLevel("INFO");
  });

  it("info seviyesindeki mesajlari console.info ile, HH:MM:SS | SEVIYE | kategori formatinda yazar", () => {
    logger.info("auth", "verify token took 2.3 ms");

    expect(infoSpy).toHaveBeenCalledTimes(1);
    const [line] = infoSpy.mock.calls[0] as [string, ...unknown[]];
    // Hizalama uzunluklari (SEVIYE=8, kategori=10) formatlayicinin kendi
    // sabitleriyle AYNI mantikla (padEnd) uretilir - manuel bosluk sayimi
    // yerine ayni padEnd cagrisiyla dogrulanir.
    const expectedLevel = "INFO".padEnd(8);
    const expectedCategory = "auth".padEnd(10);
    expect(line.startsWith("%c")).toBe(true);
    expect(line).toContain(`| ${expectedLevel} | ${expectedCategory} | verify token took 2.3 ms`);
    expect(line.slice(2, 10)).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });

  it("warn seviyesindeki mesajlari console.warn ile yazar", () => {
    logger.warn("api", "POST /api/search -> 200 took 1.41 s (slow, >1.00 s)");
    expect(warnSpy).toHaveBeenCalledTimes(1);
  });

  it("error seviyesindeki mesajlari console.error ile yazar", () => {
    logger.error("api", "GET /api/items failed", new Error("boom"));
    expect(errorSpy).toHaveBeenCalledTimes(1);
  });

  it("yapilandirilan seviyenin altindaki loglari bastirir", () => {
    setLogLevel("ERROR");
    logger.info("api", "gorunmemeli");
    logger.warn("api", "bu da gorunmemeli");
    expect(infoSpy).not.toHaveBeenCalled();
    expect(warnSpy).not.toHaveBeenCalled();
  });
});

describe("logTiming", () => {
  beforeEach(() => {
    vi.spyOn(console, "info").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("basarili islemlerde INFO loglar ve sonucu dondurur", async () => {
    const infoSpy = vi.spyOn(console, "info");
    const result = await logTiming("api", "op", async () => 42, { slowThresholdMs: 10_000 });
    expect(result).toBe(42);
    expect(infoSpy).toHaveBeenCalledTimes(1);
  });

  it("esik asilirsa WARNING loglar", async () => {
    const warnSpy = vi.spyOn(console, "warn");
    await logTiming("api", "op", async () => "ok", { slowThresholdMs: -1 });
    expect(warnSpy).toHaveBeenCalledTimes(1);
  });

  it("hata olustugunda ERROR loglar ve hatayi yeniden firlatir", async () => {
    const errorSpy = vi.spyOn(console, "error");
    const boom = new Error("boom");
    await expect(
      logTiming("api", "op", async () => {
        throw boom;
      }),
    ).rejects.toBe(boom);
    expect(errorSpy).toHaveBeenCalledTimes(1);
  });
});
