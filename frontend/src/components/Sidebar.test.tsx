import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Sidebar from "./Sidebar";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Sidebar", () => {
  it("aktif rotayi aria-current='page' ile isaretler", () => {
    render(
      <MemoryRouter initialEntries={["/projeler"]}>
        <Sidebar isOpen={false} onClose={() => {}} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: /Projeler/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /Genel Bakış/ })).not.toHaveAttribute("aria-current");
  });

  it("Escape tusuyla kapanir", () => {
    const onClose = vi.fn();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Sidebar isOpen onClose={onClose} />
      </MemoryRouter>,
    );

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("acikken klavye odagi menu iceriginde kalir (ilk baglantiya odaklanir)", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Sidebar isOpen onClose={() => {}} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: /Genel Bakış/ })).toHaveFocus();
  });

  it("Ayarlar grubunun yaninda 'Yeni Test' CTA'si gostermez", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Sidebar isOpen={false} onClose={() => {}} />
      </MemoryRouter>,
    );

    expect(screen.queryByRole("link", { name: /Yeni Test/ })).not.toBeInTheDocument();
  });

  it("ana islemler dogru sirada listelenir ve Cip Cuzdani'na yonlendirir", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Sidebar isOpen={false} onClose={() => {}} />
      </MemoryRouter>,
    );

    const primaryLabels = ["Genel Bakış", "Projeler", "Simülasyonlar", "Raporlar", "Çip Cüzdanı"];
    for (const label of primaryLabels) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: "Çip Cüzdanı" })).toHaveAttribute(
      "href",
      "/kullanim-ve-chip",
    );
    expect(screen.queryByRole("link", { name: "Kullanım ve Chip" })).not.toBeInTheDocument();
  });

  it("Personalar ve Analiz Modulleri 'Araçlar' grubunda yer alir", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Sidebar isOpen={false} onClose={() => {}} />
      </MemoryRouter>,
    );

    const toolsGroup = screen.getByRole("list", { name: "Araçlar" });
    expect(toolsGroup).toContainElement(screen.getByRole("link", { name: "Personalar" }));
    expect(toolsGroup).toContainElement(screen.getByRole("link", { name: "Analiz Modülleri" }));
  });

  it("Ayarlar ve Yardım menu altinda bulunur", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Sidebar isOpen={false} onClose={() => {}} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Ayarlar" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Yardım" })).toBeInTheDocument();
  });
});
