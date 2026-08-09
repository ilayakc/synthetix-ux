import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ProjectDetail from "./ProjectDetail";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("ProjectDetail", () => {
  it("başlatılmış testleri, benzersiz tamamlanan raporları ve taslakları birlikte gösterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/projects/project-1")) {
          return jsonResponse(200, {
            id: "project-1",
            organization_id: "org-1",
            name: "gigbi",
            description: "Test projesi",
            status: "active",
            test_count: 2,
            created_at: "2026-08-01T00:00:00Z",
            updated_at: "2026-08-01T00:00:00Z",
            archived_at: null,
          });
        }
        if (url.includes("/api/reports")) {
          return jsonResponse(200, [
            {
              id: "report-a",
              title: "Rapor A",
              simulation_run_id: "run-a",
              project_id: "project-1",
              project_name: "gigbi",
              test_definition_id: "definition-1",
              test_definition_name: "deneme2",
              variant_name: "A",
              variant_role: "variant_a",
              model_version: "v1",
              calibration_status: "uncalibrated",
              created_at: "2026-08-08T10:00:00Z",
            },
            {
              id: "report-b",
              title: "Rapor B",
              simulation_run_id: "run-b",
              project_id: "project-1",
              project_name: "gigbi",
              test_definition_id: "definition-1",
              test_definition_name: "deneme2",
              variant_name: "B",
              variant_role: "variant_b",
              model_version: "v1",
              calibration_status: "uncalibrated",
              created_at: "2026-08-08T10:01:00Z",
            },
          ]);
        }
        if (url.includes("/api/tests/drafts")) {
          return jsonResponse(200, [
            {
              id: "draft-1",
              organization_id: "org-1",
              status: "draft",
              current_step: 3,
              payload: { project_id: "project-1", name: "deneme1" },
              missing_fields: [],
              warnings: [],
              created_at: "2026-08-07T10:00:00Z",
              updated_at: "2026-08-07T10:00:00Z",
            },
          ]);
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    render(
      <MemoryRouter initialEntries={["/projeler/project-1"]}>
        <Routes>
          <Route path="/projeler/:projectId" element={<ProjectDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByRole("heading", { name: "gigbi" })).toBeInTheDocument());
    expect(screen.getByText("2 başlatılmış test · 1 taslak")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /deneme1/i })).toHaveAttribute(
      "href",
      "/tests/new?draft=draft-1",
    );
    expect(screen.getByRole("link", { name: /deneme2/i })).toHaveAttribute(
      "href",
      "/raporlar/report-a",
    );
    expect(screen.getByText(/1 test çalışıyor, başarısız oldu veya sonuç bekliyor/i)).toBeInTheDocument();
  });
});
