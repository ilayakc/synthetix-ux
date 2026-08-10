import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import Help from "./Help";

function renderHelp() {
  return render(
    <MemoryRouter initialEntries={["/yardim"]}>
      <Help />
    </MemoryRouter>,
  );
}

describe("Help", () => {
  it("bos placeholder yerine rehberleri ve calisan hizli baglantilari gosterir", () => {
    renderHelp();

    expect(
      screen.getByRole("heading", { name: "Nereden başlayacağınızı birlikte bulalım" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Beş adımda yeni test" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Hangi analizi ne zaman kullanmalısınız?" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Teste başlayın/ })).toHaveAttribute(
      "href",
      "/tests/new",
    );
    expect(screen.getByRole("link", { name: /Modülleri inceleyin/ })).toHaveAttribute(
      "href",
      "/analiz-modulleri",
    );
    expect(screen.queryByText("Bu bölüm henüz uygulanmadı.")).not.toBeInTheDocument();
  });

  it("sik sorulan sorulari arama ifadesine gore filtreler ve temizler", async () => {
    const user = userEvent.setup();
    renderHelp();

    const search = screen.getByRole("searchbox", { name: "Sık sorulan sorularda ara" });
    await user.type(search, "ısı haritası");

    expect(screen.getByText("1 sonuç")).toBeInTheDocument();
    expect(screen.getByText("Isı haritasındaki renkler neyi gösterir?")).toBeInTheDocument();
    expect(
      screen.queryByText("Yarım bıraktığım teste nasıl devam ederim?"),
    ).not.toBeInTheDocument();

    await user.clear(search);
    await user.type(search, "eşleşmeyen ifade");
    expect(screen.getByText("Bu ifadeyle eşleşen bir yanıt bulunamadı.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Aramayı temizle" }));
    expect(search).toHaveValue("");
  });
});
