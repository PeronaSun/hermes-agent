import { test } from "node:test";
import assert from "node:assert";
import { renderProjectDetail } from "./renderer-project.js";

const data = {
  projects: [{ id: "p1", name: "Proj One", owner: "Ann", domain: "Client development", priority: "QW", description: "desc here" }],
  maisons: [{ id: "m1", name: "Tiffany", division: "W&J" }],
  records: [{ project_id: "p1", maison_id: "m1", status: "Run", remark: "go", updated_at: "2026-06-16" }],
  statusConfig: [{ status: "Run", category: "run" }],
};

test("project detail shows header, summary, maison coverage with status + remark", () => {
  const html = renderProjectDetail("p1", data);
  assert.ok(html.includes("Proj One"));
  assert.ok(html.includes("Client development") && html.includes("QW") && html.includes("Ann"));
  assert.ok(html.includes("desc here"));
  assert.ok(html.includes("Tiffany") && html.includes("W&amp;J"));
  assert.ok(html.includes("go"));
  assert.ok(html.includes("cat-run") || html.includes("Run"));
});

test("unknown project id renders not found", () => {
  assert.ok(renderProjectDetail("nope", data).toLowerCase().includes("not found"));
});
