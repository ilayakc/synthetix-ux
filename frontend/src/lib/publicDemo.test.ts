import { describe, expect, it } from "vitest";

import { selectPublicDemoItems } from "./publicDemo";

describe("selectPublicDemoItems", () => {
  it("shows only the newest unified example by default", () => {
    const items = [
      { id: "old", created_at: "2026-01-01T00:00:00Z" },
      { id: "new", created_at: "2026-03-01T00:00:00Z" },
      { id: "middle", created_at: "2026-02-01T00:00:00Z" },
    ];

    expect(selectPublicDemoItems(items)).toEqual([items[1]]);
  });

  it("does not mutate the API response ordering", () => {
    const items = [
      { id: "old", created_at: "2026-01-01T00:00:00Z" },
      { id: "new", created_at: "2026-03-01T00:00:00Z" },
    ];

    selectPublicDemoItems(items);

    expect(items.map((item) => item.id)).toEqual(["old", "new"]);
  });
});
