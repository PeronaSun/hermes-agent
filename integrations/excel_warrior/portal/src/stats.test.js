import { test } from "node:test";
import assert from "node:assert";
import { countByStatusCategory, getGlobalSummary, getProjectSummary, getMaisonSummary } from "./stats.js";

const cfg = [
  { status: "Ideation", category: "ideation" },
  { status: "Q3-Pilot", category: "pilot" },
  { status: "Q1-Scale", category: "scale" },
  { status: "Run", category: "run" },
  { status: "Done", category: "done" },
];
const recs = [
  { project_id: "p1", maison_id: "m1", status: "Ideation" },
  { project_id: "p1", maison_id: "m2", status: "Run" },
  { project_id: "p2", maison_id: "m1", status: "Q3-Pilot" },
  { project_id: "p2", maison_id: "m1", status: "Bogus" },
];

test("countByStatusCategory totals + per-category", () => {
  assert.deepStrictEqual(countByStatusCategory(recs, cfg),
    { total: 4, ideation: 1, pilot: 1, scale: 0, run: 1, done: 0 });
});

test("global summary equals counting all", () => {
  assert.deepStrictEqual(getGlobalSummary(recs, cfg), countByStatusCategory(recs, cfg));
});

test("project summary filters by project_id", () => {
  assert.deepStrictEqual(getProjectSummary("p1", recs, cfg),
    { total: 2, ideation: 1, pilot: 0, scale: 0, run: 1, done: 0 });
});

test("maison summary filters by maison_id", () => {
  assert.deepStrictEqual(getMaisonSummary("m1", recs, cfg),
    { total: 3, ideation: 1, pilot: 1, scale: 0, run: 0, done: 0 });
});
