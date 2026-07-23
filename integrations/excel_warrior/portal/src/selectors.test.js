import { test } from "node:test";
import assert from "node:assert";
import { indexById, recordsForMaison, recordsForProject, applyFilters, filterOptions } from "./selectors.js";

const projects = [
  { id: "p1", name: "P1", owner: "Ann", priority: "QW", domain: "Client development" },
  { id: "p2", name: "P2", owner: "Bob", priority: "R1", domain: "Operations" },
];
const maisons = [
  { id: "m1", name: "M1", division: "FLG" },
  { id: "m2", name: "M2", division: "W&J" },
];
const records = [
  { project_id: "p1", maison_id: "m1", status: "Run" },
  { project_id: "p1", maison_id: "m2", status: "Ideation" },
  { project_id: "p2", maison_id: "m1", status: "Done" },
];

test("indexById builds an id->object map", () => {
  const m = indexById(maisons);
  assert.strictEqual(m.get("m2").name, "M2");
});

test("recordsForMaison / recordsForProject filter", () => {
  assert.strictEqual(recordsForMaison("m1", records).length, 2);
  assert.strictEqual(recordsForProject("p1", records).length, 2);
});

test("applyFilters narrows by division/owner/status/priority/domain/maison", () => {
  assert.strictEqual(applyFilters(records, { projects, maisons, filters: { division: "W&J" } }).length, 1);
  assert.strictEqual(applyFilters(records, { projects, maisons, filters: { owner: "Ann" } }).length, 2);
  assert.strictEqual(applyFilters(records, { projects, maisons, filters: { status: "Done" } }).length, 1);
  assert.strictEqual(applyFilters(records, { projects, maisons, filters: { priority: "R1" } }).length, 1);
  assert.strictEqual(applyFilters(records, { projects, maisons, filters: { maison: "m1" } }).length, 2);
  assert.strictEqual(applyFilters(records, { projects, maisons, filters: {} }).length, 3);
});

test("filterOptions returns sorted distinct values + statuses from config", () => {
  const statusConfig = [{ status: "Ideation" }, { status: "Run" }];
  const o = filterOptions(projects, maisons, statusConfig);
  assert.deepStrictEqual(o.divisions, ["FLG", "W&J"]);
  assert.deepStrictEqual(o.owners, ["Ann", "Bob"]);
  assert.deepStrictEqual(o.priorities, ["QW", "R1"]);
  assert.deepStrictEqual(o.domains, ["Client development", "Operations"]);
  assert.deepStrictEqual(o.statuses, ["Ideation", "Run"]);
  assert.deepStrictEqual(o.maisons.map((m) => m.id), ["m1", "m2"]);
});
