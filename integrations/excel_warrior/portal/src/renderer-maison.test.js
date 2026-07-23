import { test } from "node:test";
import assert from "node:assert";
import { renderMaisonDetail } from "./renderer-maison.js";

const data = {
  projects: [{ id: "p1", name: "Proj One", owner: "Ann" }],
  maisons: [{ id: "m1", name: "Tiffany", division: "W&J", key_account: "Candy", ai_champion: "Josh" }],
  records: [{ project_id: "p1", maison_id: "m1", status: "Run", remark: "wait", updated_at: "2026-06-16" }],
  statusConfig: [{ status: "Run", category: "run" }],
  budget: [{ maison_id: "m1", notes: [{ key: "AI budget", value: "tbc" }] }],
  images: [{ maison_id: "m1", images: ["images/16_tiffany_01.png"] }],
};

test("maison detail shows header, stats, project row with remark, gallery, B26", () => {
  const html = renderMaisonDetail("m1", data);
  assert.ok(html.includes("Tiffany"));
  assert.ok(html.includes("W&amp;J") || html.includes("W&J"));
  assert.ok(html.includes("Candy") && html.includes("Josh"));
  assert.ok(html.includes("Proj One") && html.includes("Ann"));
  assert.ok(html.includes("wait"));
  assert.ok(html.includes("images/16_tiffany_01.png"));
  assert.ok(html.includes("AI budget") && html.includes("tbc"));
  assert.ok(html.includes("Projects") && html.includes("Gallery") && html.includes("B26"));
});

test("unknown maison id renders a not-found message", () => {
  assert.ok(renderMaisonDetail("nope", data).toLowerCase().includes("not found"));
});
