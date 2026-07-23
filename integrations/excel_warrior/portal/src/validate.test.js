import { test } from "node:test";
import assert from "node:assert";
import { validateData } from "./validate.js";

const projects = [{ id: "p1", name: "P1", owner: "Ann" }, { id: "p2", name: "P2", owner: "" }];
const maisons = [{ id: "m1", name: "M1" }];
const statusConfig = [{ status: "Run", category: "run" }];
const records = [
  { project_id: "p1", maison_id: "m1", status: "Run", updated_at: "2026-06-16" },
  { project_id: "p1", maison_id: "m1", status: "Run", updated_at: "2026-06-16" },
  { project_id: "ghost", maison_id: "m1", status: "Run", updated_at: "2026-06-16" },
  { project_id: "p1", maison_id: "mX", status: "Nope", updated_at: "2000-01-01" },
];

test("validateData reports each rule and overall validity", () => {
  const rep = validateData({ projects, maisons, records, statusConfig, today: "2026-06-16", staleDays: 90 });
  assert.strictEqual(rep.valid_records, 4);
  assert.strictEqual(rep.missing_project_refs, 1);
  assert.strictEqual(rep.missing_maison_refs, 1);
  assert.strictEqual(rep.invalid_statuses, 1);
  assert.strictEqual(rep.duplicate_records, 1);
  assert.strictEqual(rep.empty_owner_projects, 1);
  assert.strictEqual(rep.stale_records, 1);
  assert.strictEqual(rep.ok, false);
});

test("clean dataset -> ok true", () => {
  const rep = validateData({
    projects: [{ id: "p1", name: "P1", owner: "Ann" }],
    maisons: [{ id: "m1", name: "M1" }],
    records: [{ project_id: "p1", maison_id: "m1", status: "Run", updated_at: "2026-06-16" }],
    statusConfig, today: "2026-06-16", staleDays: 90,
  });
  assert.strictEqual(rep.ok, true);
});
