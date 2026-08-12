import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  createPageAnalysisForDesignAsset,
  deleteDesignAsset,
  getDesignAsset,
  getDesignAssetPreviewUrl,
  getHealth,
  getPageAnalysis,
  patchWizardDraft,
  uploadDesignAsset,
} from "./client";

function jsonResponse(status: number, body: unknown, statusText = "Error") {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    statusText,
    json: async () => body,
  });
}

function notJsonResponse(status: number, statusText: string) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    statusText,
    json: async () => {
      throw new Error("Gecersiz JSON");
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("rawFetch hata mesaji cikarimi", () => {
  it("detail string ise dogrudan kullanir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(jsonResponse(400, { detail: "Gecersiz istek" })),
    );

    await expect(getHealth()).rejects.toMatchObject({
      message: "Geçersiz istek",
    } satisfies Partial<ApiError>);
  });

  it("detail FastAPI dogrulama dizisi ise anlamli kisa bir metin uretir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(
        jsonResponse(422, {
          detail: [
            {
              loc: ["body", "email"],
              msg: "gecerli bir e-posta adresi giriniz",
              type: "value_error",
            },
            { loc: ["body", "password"], msg: "en az 8 karakter olmalidir", type: "value_error" },
          ],
        }),
      ),
    );

    await expect(getHealth()).rejects.toMatchObject({
      message: "geçerli bir e-posta adresi giriniz; en az 8 karakter olmalıdır",
    } satisfies Partial<ApiError>);
  });

  it("detail bir nesne ise guvenli genel bir hata metni kullanir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(
        jsonResponse(500, {
          detail: { error: "internal", trace: "Traceback (most recent call last): ..." },
        }),
      ),
    );

    let error: ApiError | undefined;
    try {
      await getHealth();
    } catch (err) {
      error = err as ApiError;
    }
    expect(error).toBeInstanceOf(ApiError);
    expect(error!.message).not.toContain("[object Object]");
    expect(error!.message).not.toContain("Traceback");
    expect(error!.message).toBe("İstek işlenirken bir hata oluştu.");
  });

  it("JSON olmayan hata yanitlarinda HTTP durum mesajini kullanir", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(notJsonResponse(503, "Service Unavailable")));

    await expect(getHealth()).rejects.toMatchObject({
      message: "API isteği başarısız: 503 Service Unavailable",
    } satisfies Partial<ApiError>);
  });
});

describe("design-assets uc noktalari", () => {
  it("getDesignAsset metadata'yi apiFetch uzerinden dondurur", async () => {
    const body = {
      id: "asset-1",
      organization_id: "org-1",
      content_type: "image/png",
      byte_size: 1024,
      width: 100,
      height: 80,
      label: null,
      status: "active",
      has_image: true,
      expires_at: null,
      created_at: "2026-07-20T00:00:00Z",
      updated_at: "2026-07-20T00:00:00Z",
    };
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(jsonResponse(200, body)));

    await expect(getDesignAsset("asset-1")).resolves.toEqual(body);
  });

  it("deleteDesignAsset 204 yanitinda hicbir sey dondurmez", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockReturnValue(
          Promise.resolve({
            ok: true,
            status: 204,
            statusText: "No Content",
            json: async () => null,
          }),
        ),
    );

    await expect(deleteDesignAsset("asset-1")).resolves.toBeUndefined();
  });

  it("getDesignAssetPreviewUrl goreli yolu mutlak URL'ye cevirir", () => {
    expect(getDesignAssetPreviewUrl("asset-1")).toContain("/api/design-assets/asset-1/preview");
  });
});

describe("page-analyses uc noktalari (Paket 4C+4D)", () => {
  it("createPageAnalysisForDesignAsset design_asset_id govdesiyle POST atar", async () => {
    const body = {
      id: "analysis-1",
      organization_id: "org-1",
      source_kind: "design_asset",
      url: null,
      design_asset_id: "asset-1",
      design_asset_still_linked: true,
      status: "queued",
      attempt_count: 0,
      error: null,
      error_code: null,
      snapshot_version: null,
      analyzer_version: null,
      source: null,
      features: null,
      has_screenshot: false,
      image_width: null,
      image_height: null,
      screenshot_content_type: null,
      content_sha256: null,
      started_at: null,
      finished_at: null,
      created_at: "2026-07-23T00:00:00Z",
      updated_at: "2026-07-23T00:00:00Z",
    };
    const fetchMock = vi.fn().mockReturnValue(jsonResponse(201, body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(createPageAnalysisForDesignAsset("asset-1")).resolves.toEqual(body);
    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ design_asset_id: "asset-1" });
  });

  it("getPageAnalysis analiz durumunu apiFetch uzerinden dondurur", async () => {
    const body = { id: "analysis-1", status: "succeeded" };
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(jsonResponse(200, body)));

    await expect(getPageAnalysis("analysis-1")).resolves.toEqual(body);
  });
});

describe("patchWizardDraft ile CTA annotation gonderimi", () => {
  it("current_cta_annotation alanini payload icinde gonderir ve sunucunun cozumledigi yaniti dondurur", async () => {
    const resolved = {
      design_asset_id: "asset-1",
      box: { x: 0.1, y: 0.1, w: 0.2, h: 0.08 },
      selection_source: "manual_box",
      verified_content_sha256: "a".repeat(64),
    };
    const responseBody = {
      id: "draft-1",
      organization_id: "org-1",
      status: "draft",
      current_step: 2,
      payload: { current_cta_annotation: resolved },
      missing_fields: [],
      created_at: "2026-07-23T00:00:00Z",
      updated_at: "2026-07-23T00:00:00Z",
      warnings: [],
    };
    const fetchMock = vi.fn().mockReturnValue(jsonResponse(200, responseBody));
    vi.stubGlobal("fetch", fetchMock);

    const raw = {
      design_asset_id: "asset-1",
      box: { x: 0.1, y: 0.1, w: 0.2, h: 0.08 },
      selection_source: "manual_box" as const,
    };
    await expect(
      patchWizardDraft("draft-1", { current_cta_annotation: raw as never }),
    ).resolves.toEqual(responseBody);

    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ payload: { current_cta_annotation: raw } });
  });
});

describe("uploadDesignAsset (XMLHttpRequest tabanli multipart yukleme)", () => {
  class FakeXHR {
    static instances: FakeXHR[] = [];

    method = "";
    url = "";
    withCredentials = false;
    status = 0;
    statusText = "";
    responseText = "";
    upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    requestHeaders: Record<string, string> = {};
    sentBody: unknown = null;

    open(method: string, url: string) {
      this.method = method;
      this.url = url;
      FakeXHR.instances.push(this);
    }

    setRequestHeader(name: string, value: string) {
      this.requestHeaders[name] = value;
    }

    send(body: unknown) {
      this.sentBody = body;
    }
  }

  afterEach(() => {
    FakeXHR.instances = [];
  });

  it("basarili yuklemede ayristirilmis JSON govdeyi dondurur", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR as unknown as typeof XMLHttpRequest);

    const file = new File(["fake-png-bytes"], "design.png", { type: "image/png" });
    const promise = uploadDesignAsset(file, { label: "Tasarim A" });

    const xhr = FakeXHR.instances[0];
    expect(xhr.method).toBe("POST");
    expect(xhr.url).toContain("/api/design-assets");
    expect(xhr.withCredentials).toBe(true);

    xhr.status = 201;
    xhr.responseText = JSON.stringify({ id: "asset-1", content_type: "image/png" });
    xhr.onload?.();

    await expect(promise).resolves.toMatchObject({ id: "asset-1", content_type: "image/png" });
  });

  it("basarisiz yuklemede backend detail mesajiyla ApiError firlatir", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR as unknown as typeof XMLHttpRequest);

    const file = new File(["not-an-image"], "design.png", { type: "image/png" });
    const promise = uploadDesignAsset(file);

    const xhr = FakeXHR.instances[0];
    xhr.status = 422;
    xhr.statusText = "Unprocessable Entity";
    xhr.responseText = JSON.stringify({ detail: "Desteklenmeyen gorsel formati" });
    xhr.onload?.();

    await expect(promise).rejects.toMatchObject({
      status: 422,
      message: "Desteklenmeyen gorsel formati",
    } satisfies Partial<ApiError>);
  });

  it("ag hatasinda ApiError ile reddeder", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR as unknown as typeof XMLHttpRequest);

    const file = new File(["bytes"], "design.png", { type: "image/png" });
    const promise = uploadDesignAsset(file);

    const xhr = FakeXHR.instances[0];
    xhr.onerror?.();

    await expect(promise).rejects.toBeInstanceOf(ApiError);
  });

  it("ilerleme geri cagirisini (onProgress) tetikler", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR as unknown as typeof XMLHttpRequest);

    const onProgress = vi.fn();
    const file = new File(["bytes"], "design.png", { type: "image/png" });
    const promise = uploadDesignAsset(file, { onProgress });

    const xhr = FakeXHR.instances[0];
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 } as ProgressEvent);
    expect(onProgress).toHaveBeenCalledWith(50, 100);

    xhr.status = 201;
    xhr.responseText = JSON.stringify({ id: "asset-1" });
    xhr.onload?.();
    await promise;
  });

  function flushMicrotasks(): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, 0));
  }

  it("401 sonrasi basarili yenilemeyle yuklemeyi TAM OLARAK BIR KEZ tekrarlar", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR as unknown as typeof XMLHttpRequest);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(
        jsonResponse(200, {
          user_id: "u1",
          email: "u@example.com",
          display_name: null,
          organization_id: "org-1",
          organization_name: "Org",
          role: "owner",
          is_platform_admin: false,
        }),
      ),
    );

    const onProgress = vi.fn();
    const file = new File(["bytes"], "design.png", { type: "image/png" });
    const promise = uploadDesignAsset(file, { onProgress });

    expect(FakeXHR.instances).toHaveLength(1);
    const firstAttempt = FakeXHR.instances[0];
    firstAttempt.status = 401;
    firstAttempt.responseText = JSON.stringify({ detail: "Oturum gecersiz veya suresi dolmus" });
    firstAttempt.onload?.();

    await flushMicrotasks();

    // Yenileme (refresh) tam olarak bir kez cagrilmis olmali.
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetch).mock.calls[0][0]).toContain("/api/auth/refresh");

    // Tekrar deneme icin YENI bir XHR olusturulmus olmali (ikinci ornek).
    expect(FakeXHR.instances).toHaveLength(2);
    const retryAttempt = FakeXHR.instances[1];
    retryAttempt.upload.onprogress?.({
      lengthComputable: true,
      loaded: 10,
      total: 10,
    } as ProgressEvent);
    retryAttempt.status = 201;
    retryAttempt.responseText = JSON.stringify({ id: "asset-after-refresh" });
    retryAttempt.onload?.();

    await expect(promise).resolves.toMatchObject({ id: "asset-after-refresh" });
    // Ilerleme callback'i tekrar deneme sirasinda da calismaya devam eder.
    expect(onProgress).toHaveBeenCalledWith(10, 10);
    // Sonsuz donguye girmedigini kanitlamak icin: toplam iki deneme, iki XHR.
    expect(FakeXHR.instances).toHaveLength(2);
  });

  it("401 sonrasi yenileme basarisiz olursa oturum suresi doldu bildirimiyle reddeder", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR as unknown as typeof XMLHttpRequest);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(jsonResponse(401, { detail: "Oturum gecersiz veya suresi dolmus" })),
    );
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");

    const file = new File(["bytes"], "design.png", { type: "image/png" });
    const promise = uploadDesignAsset(file);

    const firstAttempt = FakeXHR.instances[0];
    firstAttempt.status = 401;
    firstAttempt.responseText = JSON.stringify({ detail: "Oturum gecersiz veya suresi dolmus" });
    firstAttempt.onload?.();

    await expect(promise).rejects.toMatchObject({ status: 401 } satisfies Partial<ApiError>);

    // Yenileme basarisiz oldugu icin IKINCI bir yukleme denemesi YAPILMAMALI.
    expect(FakeXHR.instances).toHaveLength(1);
    expect(dispatchSpy).toHaveBeenCalled();

    dispatchSpy.mockRestore();
  });

  it("tekrar denemede guncel CSRF cookie'sini yeniden okur", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXHR as unknown as typeof XMLHttpRequest);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(
        jsonResponse(200, {
          user_id: "u1",
          email: "u@example.com",
          display_name: null,
          organization_id: "org-1",
          organization_name: "Org",
          role: "owner",
          is_platform_admin: false,
        }),
      ),
    );

    document.cookie = "csrf_token=eski-token; path=/";
    const file = new File(["bytes"], "design.png", { type: "image/png" });
    const promise = uploadDesignAsset(file);

    const firstAttempt = FakeXHR.instances[0];
    expect(firstAttempt.requestHeaders["X-CSRF-Token"]).toBe("eski-token");
    firstAttempt.status = 401;
    firstAttempt.onload?.();

    // Yenileme (refresh) CSRF cookie'sini rotate etmis olabilir; bu deger
    // ikinci XHR ACILMADAN once degistirilir, boylece tekrar denemenin
    // GERCEKTEN taze bir okuma yaptigi (eski degeri onbelleklemedigi)
    // dogrulanabilir.
    document.cookie = "csrf_token=yeni-token; path=/";
    await flushMicrotasks();

    const retryAttempt = FakeXHR.instances[1];
    expect(retryAttempt.requestHeaders["X-CSRF-Token"]).toBe("yeni-token");

    retryAttempt.status = 201;
    retryAttempt.responseText = JSON.stringify({ id: "asset-1" });
    retryAttempt.onload?.();
    await promise;
  });
});
