import { test } from "node:test";
import assert from "node:assert";
import { renderMatrix } from "./renderer-matrix.js";

const data = {
  projects: [{ id: "p1", name: "Proj One" }, { id: "p2", name: "Proj Two" }],
  maisons: [{ id: "m1", name: "M1" }, { id: "m2", name: "M2" }],
  statusConfig: [{ status: "Run", category: "run" }, { status: "Ideation", category: "ideation" }],
};
const records = [
  { project_id: "p1", maison_id: "m1", status: "Run" },
  { project_id: "p1", maison_id: "m2", status: "Ideation" },
  { project_id: "p2", maison_id: "m1", status: "Run" },
];

test("matrix has a column per maison and a row per project with category-colored cells", () => {
  const html = renderMatrix(data, records);
  assert.ok(html.includes("Proj One") && html.includes("Proj Two"));
  assert.ok(html.includes(">M1<") && html.includes(">M2<"));
  assert.ok(html.includes("cat-run") && html.includes("cat-ideation"));
  assert.ok(html.includes("Stats") || html.includes("sum"));
});

test("empty project/maison combo renders an empty cell (no badge)", () => {
  const html = renderMatrix(data, records);
  const badges = html.match(/badge cat-/g) || [];
  assert.strictEqual(badges.length, 3);
});

test("filtered records collapse visible rows and columns", () => {
  const subset = [{ project_id: "p1", maison_id: "m1", status: "Run" }];
  const html = renderMatrix(data, subset);
  assert.ok(html.includes("Proj One"), "visible project shown");
  assert.ok(!html.includes("Proj Two"), "hidden project absent");
  assert.ok(html.includes(">M1<"), "visible maison shown");
  assert.ok(!html.includes(">M2<"), "hidden maison absent");
});

test("no records renders empty state", () => {
  const html = renderMatrix(data, []);
  assert.ok(html.includes("empty") || html.includes("No records"));
});

// --- Descriptor columns (domain_category / domain / priority / owner) ---

const descData = {
  projects: [
    { id: "p1", name: "Proj One", domain_category: "DATA & AI", domain: "Client development", priority: "QW", owner: "Mars YU" },
    { id: "p2", name: "Proj Two", domain_category: "DATA & AI", domain: "Client development", priority: "TT", owner: "Chen WANG" },
    { id: "p3", name: "Proj Three", domain_category: "DATA & AI", domain: "", priority: "TT", owner: "" },
    { id: "p4", name: "Proj Four", domain_category: "OMNI. RETAIL", domain: "Omni retail", priority: "BB", owner: "Wendy CHEN" },
  ],
  maisons: [{ id: "m1", name: "M1" }],
  statusConfig: [{ status: "Run", category: "run" }],
};
const descRecords = [
  { project_id: "p1", maison_id: "m1", status: "Run" },
  { project_id: "p2", maison_id: "m1", status: "Run" },
  { project_id: "p3", maison_id: "m1", status: "Run" },
  { project_id: "p4", maison_id: "m1", status: "Run" },
];

test("matrix renders descriptor column headers", () => {
  const html = renderMatrix(descData, descRecords);
  assert.ok(html.includes(">Domain category</div>"), "Domain category header");
  assert.ok(html.includes(">Domain</div>"), "Domain header");
  assert.ok(html.includes(">Priority</div>"), "Priority header");
  assert.ok(html.includes(">Owner</div>"), "Owner header");
});

test("priority and owner render as plain text per row", () => {
  const html = renderMatrix(descData, descRecords);
  assert.ok(html.includes("QW") && html.includes("TT") && html.includes("BB"), "priorities");
  assert.ok(html.includes("Mars YU") && html.includes("Wendy CHEN"), "owners");
});

test("domain_category merges consecutive rows via rowspan", () => {
  const html = renderMatrix(descData, descRecords);
  // DATA & AI spans p1,p2,p3
  assert.ok(html.includes('rowspan="3"'), "category rowspan 3");
  const catCells = html.match(/>DATA &amp; AI<\/div>/g) || [];
  assert.strictEqual(catCells.length, 1, "category rendered once");
});

test("domain merges consecutive rows within a category", () => {
  const html = renderMatrix(descData, descRecords);
  // Client development spans p1,p2 → rowspan 2
  assert.ok(html.includes('rowspan="2"'), "domain rowspan 2");
  const domCells = html.match(/>Client development<\/div>/g) || [];
  assert.strictEqual(domCells.length, 1, "domain rendered once");
});

test("empty domain/owner render blank and do not merge", () => {
  const html = renderMatrix(descData, descRecords);
  // domain cells: header + Client development(rowspan2) + blank(p3) + Omni retail(p4) = 4
  assert.strictEqual((html.match(/class="d-dom"/g) || []).length, 4, "domain cell count");
  // owner is never grouped: header + 4 rows = 5
  assert.strictEqual((html.match(/class="d-own"/g) || []).length, 5, "owner cell count");
});

const boundaryData = {
  projects: [
    { id: "a", name: "A", domain_category: "CAT1", domain: "Shared", priority: "QW", owner: "X" },
    { id: "b", name: "B", domain_category: "CAT2", domain: "Shared", priority: "QW", owner: "Y" },
  ],
  maisons: [{ id: "m1", name: "M1" }],
  statusConfig: [{ status: "Run", category: "run" }],
};
const boundaryRecords = [
  { project_id: "a", maison_id: "m1", status: "Run" },
  { project_id: "b", maison_id: "m1", status: "Run" },
];

test("domain does not merge across a category boundary", () => {
  const html = renderMatrix(boundaryData, boundaryRecords);
  const domCells = html.match(/>Shared<\/div>/g) || [];
  assert.strictEqual(domCells.length, 2, "Shared rendered once per category");
});

test("rowspans recompute when a filter hides part of a group", () => {
  const subset = descRecords.filter((r) => r.project_id !== "p2");
  const html = renderMatrix(descData, subset);
  assert.ok(!html.includes("Proj Two"), "hidden project absent");
  assert.ok(html.includes('rowspan="2"'), "category spans 2 visible rows");
  const catCells = html.match(/>DATA &amp; AI<\/div>/g) || [];
  assert.strictEqual(catCells.length, 1, "category rendered once over visible set");
});
