import { test } from "node:test";
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { renderDashboard } from "./renderer-dashboard.js";
import { renderMatrix } from "./renderer-matrix.js";
import { renderMaisonDetail } from "./renderer-maison.js";
import { renderProjectDetail } from "./renderer-project.js";
import { renderDataHealth } from "./renderer-datahealth.js";
import { validateData } from "./validate.js";
import { applyFilters } from "./selectors.js";

const DATA = join(dirname(fileURLToPath(import.meta.url)), "..", "data");
const read = (f) => JSON.parse(readFileSync(join(DATA, f), "utf8"));
const data = {
  projects: read("projects.json"),
  maisons: read("maisons.json"),
  records: read("project_maison_status.json"),
  statusConfig: read("status_config.json"),
  budget: read("maison_budget.json"),
  legend: read("status_legend.json"),
  images: read("images.json"),
};

test("real data: dashboard renders with real totals", () => {
  const html = renderDashboard(data);
  assert.ok(html.includes(">Projects<"));
  assert.ok(html.includes(String(data.projects.length)));
});

test("real data: matrix renders known maison columns + colored cells, no throw", () => {
  const html = renderMatrix(data, data.records);
  assert.ok(html.includes("Louis Vuitton"));
  assert.ok(html.includes("cat-"));
});

test("real data: every maison detail renders without throwing", () => {
  for (const m of data.maisons) {
    const html = renderMaisonDetail(m.id, data);
    assert.ok(html.length > 0 && html.includes(m.name));
  }
});

test("real data: every project detail renders without throwing", () => {
  for (const p of data.projects) {
    const html = renderProjectDetail(p.id, data);
    assert.ok(html.length > 0);
  }
});

test("real data: validateData reports a clean dataset and health renders", () => {
  const rep = validateData({ ...data, today: "2026-06-16" });
  assert.strictEqual(rep.missing_project_refs, 0);
  assert.strictEqual(rep.missing_maison_refs, 0);
  assert.strictEqual(rep.invalid_statuses, 0);
  assert.strictEqual(rep.duplicate_records, 0);
  assert.strictEqual(rep.ok, true);
  assert.ok(renderDataHealth(rep).includes("Data Health"));
});

test("real data: a division filter narrows records", () => {
  const div = data.maisons[0].division;
  const filtered = applyFilters(data.records, { projects: data.projects, maisons: data.maisons, filters: { division: div } });
  assert.ok(filtered.length > 0 && filtered.length <= data.records.length);
});
