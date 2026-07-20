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

  it("altta 'Yeni Test Başlat' baglantisi bulunur", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Sidebar isOpen={false} onClose={() => {}} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Yeni Test Başlat" })).toHaveAttribute(
      "href",
      "/tests/new",
    );
  });
});
