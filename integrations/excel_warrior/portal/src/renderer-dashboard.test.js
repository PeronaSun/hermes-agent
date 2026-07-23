import { test } from "node:test";
import assert from "node:assert";
import { renderDashboard } from "./renderer-dashboard.js";

const data = {
  projects: [{ id: "p1", owner: "Ann" }, { id: "p2", owner: "Ann" }, { id: "p3", owner: "Bob" }],
  maisons: [{ id: "m1", division: "FLG" }, { id: "m2", division: "W&J" }],
  records: [
    { project_id: "p1", maison_id: "m1", status: "Run", updated_at: "2026-06-10" },
    { project_id: "p2", maison_id: "m2", status: "Ideation", updated_at: "2026-06-16" },
  ],
  statusConfig: [{ status: "Run", category: "run" }, { status: "Ideation", category: "ideation" }],
};

test("dashboard shows totals, category breakdown, by-division, by-owner, latest update", () => {
  const html = renderDashboard(data);
  assert.ok(html.includes(">3<") || html.includes("3</"));
  assert.ok(html.includes(">Projects<"));
  assert.ok(html.includes(">Maisons<"));
  assert.ok(html.includes("Run") && html.includes("Ideation"));
  assert.ok(html.includes("Ann"));
  assert.ok(html.includes("FLG"));
  assert.ok(html.includes("2026-06-16"));
});

test("dashboard shows a status legend with badge + description when provided", () => {
  const d = {
    projects: [], maisons: [], records: [],
    statusConfig: [{ status: "Ideation", category: "ideation" }, { status: "Run", category: "run" }],
    legend: [{ status: "Ideation", description: "Identified, not started" }, { status: "Run", description: "Business as usual" }],
  };
  const html = renderDashboard(d);
  assert.ok(html.includes("Legend") || html.includes("legend"));
  assert.ok(html.includes("Identified, not started"));
  assert.ok(html.includes("Business as usual"));
  assert.ok(html.includes("cat-ideation") && html.includes("cat-run")); // badges shown
});

test("dashboard does not crash when legend/statusConfig are missing", () => {
  const html = renderDashboard({ projects: [{ id: "p1", owner: "A" }], maisons: [], records: [], statusConfig: [] });
  assert.ok(html.includes("Dashboard"));
});
