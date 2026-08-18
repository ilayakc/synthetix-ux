import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { personaIntegrityStatus } from "../lib/personaIntegrity";
import Simulations from "./Simulations";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

function baseRun(overrides: Record<string, unknown> = {}) {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    organization_id: "org-1",
    test_variant_id: "variant-1",
    status: "failed",
    progress_percent: 0,
    progress_message: null,
    calibration_status: "uncalibrated",
    deterministic_seed: 1,
    model_version: "pending-engine",
    rules_version: null,
    fixture_version: null,
    error: "input_snapshot.url gereklidir (network_device_test)",
    result: null,
    retryable: false,
    failure_code: "network_device_test_requires_url",
    not_real_user_data_label: "Gerçek kullanıcı verisi değildir",
    methodology_reference: "docs/methodology.md",
    attempt_count: 1,
    persona_count: 1000,
    started_at: null,
    finished_at: "2026-07-24T10:01:52Z",
    created_at: "2026-07-24T10:01:00Z",
    updated_at: "2026-07-24T10:01:52Z",
    ...overrides,
  };
}

function stubRuns(runs: unknown[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/simulations/runs")) return jsonResponse(200, runs);
      throw new Error(`Beklenmeyen istek: ${url}`);
    }),
  );
}

function basePersona(overrides: Record<string, unknown> = {}) {
  return {
    id: "aaaaaaaa-0000-0000-0000-000000000001",
    simulation_run_id: "11111111-1111-1111-1111-111111111111",
    index: 0,
    label: "18-24 × Mobil × Orta",
    attributes: { age_range: "18_24", device_class: "mobile", digital_literacy: "medium" },
    population_weight: 300,
    created_at: "2026-07-30T10:00:00Z",
    ...overrides,
  };
}

const PERSONA_DIMENSIONS_RESPONSE = [
  { key: "age_range", label: "Yaş aralığı" },
  { key: "region", label: "Ülke / bölge" },
  { key: "device_class", label: "Cihaz sınıfı" },
  { key: "digital_literacy", label: "Dijital yatkınlık düzeyi" },
  { key: "accessibility_need", label: "Erişilebilirlik ihtiyacı" },
];

/**
 * `stubRuns` ile ayni "runs" listesini sunar, ek olarak
 * `/personas` ve `/api/personas/dimensions` uc noktalarini da mock'lar.
 * `personasResponder` bir fonksiyon olabilir (her cagride yeniden
 * degerlendirilir) - hata/retry senaryolarini (once basarisiz, sonra
 * basarili) test etmek icin kullanilir.
 */
function stubRunsWithPersonas(
  runs: unknown[],
  personasResponder: unknown[] | (() => Promise<unknown>),
  dimensionsResponder: unknown[] | null = PERSONA_DIMENSIONS_RESPONSE,
) {
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (url.includes("/personas") && url.includes("/api/simulations/runs/")) {
      return typeof personasResponder === "function"
        ? personasResponder()
        : jsonResponse(200, personasResponder);
    }
    if (url.includes("/api/personas/dimensions")) {
      return dimensionsResponder === null
        ? Promise.reject(new Error("dimensions endpoint down"))
        : jsonResponse(200, dimensionsResponder);
    }
    if (url.includes("/api/simulations/runs")) return jsonResponse(200, runs);
    throw new Error(`Beklenmeyen istek: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderSimulations() {
  return render(
    <MemoryRouter initialEntries={["/simulasyonlar"]}>
      <Routes>
        <Route path="/simulasyonlar" element={<Simulations />} />
        <Route path="/tests/new" element={<p>Sihirbaz sayfası</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Simulations", () => {
  it("retryable=false olan basarisiz calistirmada 'Yeniden dene' gostermez", async () => {
    stubRuns([baseRun()]);
    renderSimulations();

    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    expect(screen.queryByText("Yeniden dene")).not.toBeInTheDocument();
  });

  it("retryable=false olan basarisiz calistirmada 'Yeni test oluştur' baglantisi gosterir", async () => {
    stubRuns([baseRun()]);
    renderSimulations();

    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Yeni test oluştur" })).toBeInTheDocument();
  });

  it("retryable=false olan calistirmada ham hata metnini birincil mesaj olarak gostermez", async () => {
    stubRuns([baseRun()]);
    renderSimulations();

    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    expect(screen.queryByText(/input_snapshot\.url gereklidir/)).not.toBeInTheDocument();
    expect(
      screen.getByText(/uyumsuz bir analiz modülü nedeniyle tamamlanamadı ve yeniden denenemez/),
    ).toBeInTheDocument();
  });

  it("bos sayfa snapshot hatasinda anlasilir ve spesifik yonlendirme gosterir", async () => {
    stubRuns([
      baseRun({
        failure_code: "page_analysis_empty_snapshot",
        error: "ic hata ayrintisi",
      }),
    ]);
    renderSimulations();

    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    expect(screen.getByText(/boş içerik döndürüyor olabilir/)).toBeInTheDocument();
    expect(screen.queryByText(/ic hata ayrintisi/)).not.toBeInTheDocument();
  });

  it("buyuk ana belge hatasinda yeniden denemek yerine ekran goruntusune yonlendirir", async () => {
    stubRuns([
      baseRun({
        failure_code: "page_analysis_response_too_large",
        error: "ic hata ayrintisi",
      }),
    ]);
    renderSimulations();

    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    expect(screen.queryByText("Yeniden dene")).not.toBeInTheDocument();
    expect(screen.getByText(/ana belgesi analiz sınırından büyük/)).toBeInTheDocument();
    expect(screen.queryByText(/ic hata ayrintisi/)).not.toBeInTheDocument();
  });

  it("retryable=true olan gecici hatada 'Yeniden dene' gosterir ve 'Yeni test oluştur' gostermez", async () => {
    stubRuns([
      baseRun({
        id: "22222222-2222-2222-2222-222222222222",
        retryable: true,
        failure_code: null,
        error: "analyzer'a ulasilamadi (network_device_test): connection error",
      }),
    ]);
    renderSimulations();

    await waitFor(() => expect(screen.getByText(/Çalıştırma 22222222/)).toBeInTheDocument());
    expect(screen.getByText("Yeniden dene")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Yeni test oluştur" })).not.toBeInTheDocument();
    expect(screen.getByText(/Hata: analyzer'a ulasilamadi/)).toBeInTheDocument();
  });

  it("ayni hata metnini ilerleme mesaji olarak tekrar etmez", async () => {
    const legacyError = "Bagli sayfa analizi basarisiz oldugu icin bu calistirma islenemedi";
    stubRuns([
      baseRun({
        retryable: true,
        progress_message: legacyError,
        error: legacyError,
      }),
    ]);
    renderSimulations();

    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    expect(screen.getAllByText(/Bağlı sayfa analizi başarısız olduğu için/)).toHaveLength(1);
  });

  it("basarili bir calistirmada ne 'Yeniden dene' ne de 'Yeni test oluştur' gosterir", async () => {
    stubRuns([
      baseRun({
        id: "33333333-3333-3333-3333-333333333333",
        status: "succeeded",
        retryable: true,
        failure_code: null,
        error: null,
        result: null,
      }),
    ]);
    renderSimulations();

    await waitFor(() => expect(screen.getByText(/Çalıştırma 33333333/)).toBeInTheDocument());
    expect(screen.queryByText("Yeniden dene")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Yeni test oluştur" })).not.toBeInTheDocument();
  });
});

describe("Simulations - 'Kimler simüle edildi?' persona paneli", () => {
  const runId = "11111111-1111-1111-1111-111111111111";

  function personasUrlCalls(fetchMock: ReturnType<typeof vi.fn>) {
    return fetchMock.mock.calls
      .map((call) => call[0] as string)
      .filter((url) => url.includes("/personas") && url.includes("/api/simulations/runs/"));
  }

  async function expandPanel() {
    const toggle = screen.getByRole("button", { name: "Kimler simüle edildi?" });
    await userEvent.click(toggle);
    return toggle;
  }

  // --- A. Lazy loading -------------------------------------------------------

  it("panel kapaliyken personas endpointini cagirmaz, acilinca tam olarak bir kez cagirir", async () => {
    const fetchMock = stubRunsWithPersonas(
      [baseRun({ id: runId, status: "succeeded" })],
      [basePersona()],
    );
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());

    expect(personasUrlCalls(fetchMock)).toHaveLength(0);

    await expandPanel();
    await waitFor(() => expect(personasUrlCalls(fetchMock)).toHaveLength(1));
  });

  it("kapatip tekrar acinca mevcut veriyi tekrar cekmez (cache)", async () => {
    const fetchMock = stubRunsWithPersonas(
      [baseRun({ id: runId, status: "succeeded" })],
      [basePersona()],
    );
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());

    const toggle = await expandPanel();
    await waitFor(() => expect(personasUrlCalls(fetchMock)).toHaveLength(1));

    await userEvent.click(toggle); // kapat
    await userEvent.click(toggle); // tekrar ac

    // Ikinci acilista fetch tekrar cagrilmaz - onceki sonuc (personas state'i) korunur.
    expect(personasUrlCalls(fetchMock)).toHaveLength(1);
  });

  // --- B. Basarili gorunum -----------------------------------------------------

  it("persona sayisini, toplam nufusu, yuzdeleri ve etiketleri dogru gosterir", async () => {
    const personas = [
      basePersona({
        id: "p-1",
        index: 1,
        label: "25-34 × Masaüstü × Yüksek",
        attributes: { age_range: "25_34", device_class: "desktop", digital_literacy: "high" },
        population_weight: 250,
      }),
      basePersona({ id: "p-0", index: 0, population_weight: 750 }),
    ];
    stubRunsWithPersonas([baseRun({ id: runId, status: "succeeded" })], personas);
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    await expandPanel();

    await waitFor(() => expect(screen.getByText("2 temsilî profil")).toBeInTheDocument());
    // toplam nufus: 750 + 250 = 1000 (baseRun.persona_count varsayilani ile ayni).
    expect(screen.getByText("1.000 sanal kullanıcı")).toBeInTheDocument();
    expect(screen.getByText(/Dağılım toplamı doğrulandı/)).toBeInTheDocument();

    const rows = screen.getAllByRole("row");
    // ilk veri satiri index 0 (population 750, %75.0) - backend sirasina (index ASC) gore ilk gorunmeli.
    const firstDataRow = rows[1];
    expect(within(firstDataRow).getByText("18-24 × Mobil × Orta")).toBeInTheDocument();
    expect(within(firstDataRow).getByText("750")).toBeInTheDocument();
    expect(within(firstDataRow).getByText("%75.0")).toBeInTheDocument();
    expect(within(firstDataRow).getByText("Mobile")).toBeInTheDocument();

    const secondDataRow = rows[2];
    expect(within(secondDataRow).getByText("25-34 × Masaüstü × Yüksek")).toBeInTheDocument();
    expect(within(secondDataRow).getByText("%25.0")).toBeInTheDocument();
  });

  it("backend sirasi karisik gelse bile index ASC olarak gosterir", async () => {
    const personas = [
      basePersona({ id: "p-2", index: 2, label: "Persona C" }),
      basePersona({ id: "p-0", index: 0, label: "Persona A" }),
      basePersona({ id: "p-1", index: 1, label: "Persona B" }),
    ];
    stubRunsWithPersonas([baseRun({ id: runId, status: "succeeded" })], personas);
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    await expandPanel();

    await waitFor(() => expect(screen.getByText("3 temsilî profil")).toBeInTheDocument());
    const rows = screen.getAllByRole("row").slice(1); // ilk satir thead basligi
    expect(within(rows[0]).getByText("Persona A")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Persona B")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Persona C")).toBeInTheDocument();
  });

  // --- C. Empty state -----------------------------------------------------------

  it("200 + [] durumunda hata degil, normal bos durum gosterir", async () => {
    stubRunsWithPersonas([baseRun({ id: runId, status: "succeeded" })], []);
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    await expandPanel();

    await waitFor(() =>
      expect(
        screen.getByText("Bu simülasyon için kalıcı persona kaydı bulunmuyor."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("Persona grupları yüklenemedi.")).not.toBeInTheDocument();
  });

  // --- D. Loading -----------------------------------------------------------

  it("istek surerken yukleniyor mesajini gosterir", async () => {
    let resolveFetch: (value: unknown) => void = () => {};
    const pending = new Promise((resolve) => {
      resolveFetch = resolve;
    });
    stubRunsWithPersonas(
      [baseRun({ id: runId, status: "succeeded" })],
      () => pending as Promise<unknown>,
    );
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    await expandPanel();

    await waitFor(() =>
      expect(screen.getByText("Persona grupları yükleniyor…")).toBeInTheDocument(),
    );

    resolveFetch(jsonResponse(200, [basePersona()]));
    await waitFor(() =>
      expect(screen.queryByText("Persona grupları yükleniyor…")).not.toBeInTheDocument(),
    );
  });

  // --- E. Hata ve tekrar dene ------------------------------------------------

  it("API hatasinda uygun mesaji gosterir ve 'Tekrar dene' yeniden istek atar", async () => {
    let callCount = 0;
    const fetchMock = stubRunsWithPersonas([baseRun({ id: runId, status: "succeeded" })], () => {
      callCount += 1;
      if (callCount === 1) return jsonResponse(500, { detail: "internal error" });
      return jsonResponse(200, [basePersona()]);
    });
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    await expandPanel();

    await waitFor(() =>
      expect(screen.getByText("Persona grupları yüklenemedi.")).toBeInTheDocument(),
    );
    // Backend hata detayi (ornegin "internal error") kullaniciya SIZMAZ.
    expect(screen.queryByText(/internal error/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Tekrar dene" }));

    await waitFor(() => expect(screen.getByText("1 temsilî profil")).toBeInTheDocument());
    expect(personasUrlCalls(fetchMock)).toHaveLength(2);
  });

  // --- F. Ilk 20 / tumunu goster ----------------------------------------------

  it("baslangicta ilk 20 profili gosterir, 'Tümünü göster' kalanlari acar, 'Daha az göster' daraltir", async () => {
    const personas = Array.from({ length: 25 }, (_, i) =>
      basePersona({ id: `p-${i}`, index: i, label: `Persona ${i}` }),
    );
    stubRunsWithPersonas([baseRun({ id: runId, status: "succeeded" })], personas);
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    await expandPanel();

    await waitFor(() => expect(screen.getByText("25 temsilî profil")).toBeInTheDocument());
    expect(screen.getByText("Persona 0")).toBeInTheDocument();
    expect(screen.getByText("Persona 19")).toBeInTheDocument();
    expect(screen.queryByText("Persona 20")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Tümünü göster (25)" }));
    expect(screen.getByText("Persona 24")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Daha az göster" }));
    expect(screen.queryByText("Persona 24")).not.toBeInTheDocument();
    expect(screen.getByText("Persona 19")).toBeInTheDocument();
  });

  // --- G. Bilinmeyen dimension --------------------------------------------------

  it("bilinmeyen/eksik attribute key'lerinde arayuz cokmez, guvenli fallback gosterir", async () => {
    const personas = [
      basePersona({
        id: "p-unknown",
        index: 0,
        attributes: { unexpected_dimension: "garip_deger" },
      }),
    ];
    // dimensions endpointi de basarisiz olsun - hem bilinmeyen attribute hem
    // de dimension etiketi yuklenemeden ham anahtar fallback'i test edilir.
    stubRunsWithPersonas([baseRun({ id: runId, status: "succeeded" })], personas, null);
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    await expandPanel();

    await waitFor(() => expect(screen.getByText("1 temsilî profil")).toBeInTheDocument());
    // Bilinen 5 boyuttan hicbiri attributes'ta yok -> hepsi "—" fallback'i gosterir.
    const rows = screen.getAllByRole("row");
    const dataRow = rows[1];
    const dashCells = within(dataRow).getAllByText("—");
    expect(dashCells.length).toBe(5);
    // dimensions endpointi basarisiz oldugu icin sutun basliklari ham
    // anahtardan humanize edilmis haliyle (orn. "Age Range") dusmeli.
    expect(screen.getByText("Age Range")).toBeInTheDocument();
  });

  // --- H. Butunluk kontrolu (saf fonksiyon) -------------------------------------

  describe("personaIntegrityStatus", () => {
    it("toplam agirlik persona_count'a esitse 'matched' doner", () => {
      expect(personaIntegrityStatus(1000, 1000)).toBe("matched");
    });

    it("toplam agirlik persona_count'tan farkliysa 'mismatched' doner", () => {
      expect(personaIntegrityStatus(950, 1000)).toBe("mismatched");
    });

    it("persona_count erisilemiyorsa (null) 'unknown' doner - yanlislikla 'matched' denmez", () => {
      expect(personaIntegrityStatus(1000, null)).toBe("unknown");
    });
  });

  // --- H (devam). run.persona_count -> panel butunluk gostergesi baglantisi ----

  it("run.persona_count agirlik toplamiyla esitse dogrulama gostergesi gosterir", async () => {
    stubRunsWithPersonas(
      [baseRun({ id: runId, status: "succeeded", persona_count: 1000 })],
      [
        basePersona({ population_weight: 400 }),
        basePersona({ id: "p-2", index: 1, population_weight: 600 }),
      ],
    );
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    await expandPanel();

    await waitFor(() => expect(screen.getByText("1.000 sanal kullanıcı")).toBeInTheDocument());
    expect(screen.getByText(/Dağılım toplamı doğrulandı/)).toBeInTheDocument();
    expect(screen.queryByText(/beklenen kullanıcı sayısıyla eşleşmiyor/)).not.toBeInTheDocument();
  });

  it("run.persona_count agirlik toplamindan farkliysa uyari gosterir", async () => {
    stubRunsWithPersonas(
      [baseRun({ id: runId, status: "succeeded", persona_count: 1000 })],
      [basePersona({ population_weight: 400 })],
    );
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    await expandPanel();

    await waitFor(() => expect(screen.getByText("400 sanal kullanıcı")).toBeInTheDocument());
    const warning = screen.getByText(
      /Persona dağılımı toplamı beklenen kullanıcı sayısıyla eşleşmiyor/,
    );
    expect(warning).toBeInTheDocument();
    expect(warning.closest('[role="status"]')).toBeInTheDocument();
    expect(screen.queryByText(/Dağılım toplamı doğrulandı/)).not.toBeInTheDocument();
  });

  it("persona_count null ise butunluk gostergesini gizler, toplam sanal kullanici sayisini yine gosterir", async () => {
    stubRunsWithPersonas(
      [baseRun({ id: runId, status: "succeeded", persona_count: null })],
      [basePersona({ population_weight: 400 })],
    );
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    await expandPanel();

    await waitFor(() => expect(screen.getByText("400 sanal kullanıcı")).toBeInTheDocument());
    expect(screen.queryByText(/Dağılım toplamı doğrulandı/)).not.toBeInTheDocument();
    expect(screen.queryByText(/beklenen kullanıcı sayısıyla eşleşmiyor/)).not.toBeInTheDocument();
  });

  it("persona listesi boşken (eski run) persona_count dolu olsa bile butunluk uyarisi gostermez", async () => {
    stubRunsWithPersonas([baseRun({ id: runId, status: "succeeded", persona_count: 1000 })], []);
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    await expandPanel();

    await waitFor(() =>
      expect(
        screen.getByText("Bu simülasyon için kalıcı persona kaydı bulunmuyor."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Dağılım toplamı doğrulandı/)).not.toBeInTheDocument();
    expect(screen.queryByText(/beklenen kullanıcı sayısıyla eşleşmiyor/)).not.toBeInTheDocument();
  });

  it("A/B run kartlari kendi persona_count degerini bagimsiz kullanir", async () => {
    const runIdA = "11111111-1111-1111-1111-111111111111";
    const runIdB = "22222222-2222-2222-2222-222222222222";
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.includes(`/api/simulations/runs/${runIdA}/personas`)) {
        return jsonResponse(200, [
          basePersona({ simulation_run_id: runIdA, population_weight: 500 }),
        ]);
      }
      if (url.includes(`/api/simulations/runs/${runIdB}/personas`)) {
        return jsonResponse(200, [
          basePersona({ id: "p-b", simulation_run_id: runIdB, population_weight: 300 }),
        ]);
      }
      if (url.includes("/api/personas/dimensions"))
        return jsonResponse(200, PERSONA_DIMENSIONS_RESPONSE);
      if (url.includes("/api/simulations/runs")) {
        return jsonResponse(200, [
          baseRun({ id: runIdA, status: "succeeded", persona_count: 500 }),
          baseRun({ id: runIdB, status: "succeeded", persona_count: 999 }),
        ]);
      }
      throw new Error(`Beklenmeyen istek: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/Çalıştırma 22222222/)).toBeInTheDocument());

    const toggles = screen.getAllByRole("button", { name: "Kimler simüle edildi?" });
    expect(toggles).toHaveLength(2);
    await userEvent.click(toggles[0]);
    await userEvent.click(toggles[1]);

    await waitFor(() => expect(screen.getAllByText(/Dağılım toplamı doğrulandı/)).toHaveLength(1));
    expect(
      screen.getByText(/Persona dağılımı toplamı beklenen kullanıcı sayısıyla eşleşmiyor/),
    ).toBeInTheDocument();
  });

  // --- I. Erisilebilirlik --------------------------------------------------------

  it("panel butonu aria-expanded/aria-controls ile klavye erisilebilirdir", async () => {
    stubRunsWithPersonas([baseRun({ id: runId, status: "succeeded" })], [basePersona()]);
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());

    const toggle = screen.getByRole("button", { name: "Kimler simüle edildi?" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle.tagName).toBe("BUTTON");

    toggle.focus();
    await userEvent.keyboard("{Enter}");

    await waitFor(() => expect(toggle).toHaveAttribute("aria-expanded", "true"));
    const controlsId = toggle.getAttribute("aria-controls");
    expect(controlsId).toBeTruthy();
    await waitFor(() => expect(document.getElementById(controlsId as string)).toBeInTheDocument());

    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.getByRole("columnheader", { name: "Persona" })).toBeInTheDocument();
  });

  // --- J. Regresyon: mevcut cancel/retry davranisi personas paneliyle bozulmaz --

  it("panel eklenmesi mevcut 'Yeniden dene' aksiyonunu etkilemez", async () => {
    stubRunsWithPersonas(
      [baseRun({ id: runId, status: "failed", retryable: true, error: "gecici hata" })],
      [basePersona()],
    );
    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());

    expect(screen.getByText("Yeniden dene")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kimler simüle edildi?" })).toBeInTheDocument();
  });
});

describe("Simulations - retry aksiyonu devam ederken", () => {
  const runId = "44444444-4444-4444-4444-444444444444";

  it("retry sürerken butonu devre disi birakir ve yukleme etiketi gosterir (cift tiklama korumasi)", async () => {
    let resolveRetry: (value: unknown) => void = () => {};
    const retryPending = new Promise((resolve) => {
      resolveRetry = resolve;
    });
    let retryCalls = 0;
    const failedRun = baseRun({
      id: runId,
      status: "failed",
      retryable: true,
      failure_code: null,
      error: "gecici hata",
    });
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes(`/api/simulations/runs/${runId}/retry`) && method === "POST") {
        retryCalls += 1;
        return retryPending;
      }
      if (url.includes("/api/simulations/runs")) return jsonResponse(200, [failedRun]);
      throw new Error(`Beklenmeyen istek: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderSimulations();
    await waitFor(() => expect(screen.getByText(/Çalıştırma 44444444/)).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "Yeniden dene" }));

    // Istek surerken buton devre disi + yukleme etiketi gosterir; devre disi
    // buton yapisal olarak ikinci bir POST /retry gonderilmesini engeller.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Yeniden deneniyor…" })).toBeDisabled(),
    );
    expect(retryCalls).toBe(1);

    // Cozulunce buton normale doner (liste tazelenir; run hala failed doner).
    resolveRetry(jsonResponse(200, { ...failedRun, status: "queued", error: null }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Yeniden dene" })).toBeEnabled());
  });
});
