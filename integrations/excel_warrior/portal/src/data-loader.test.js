import { test } from "node:test";
import assert from "node:assert";
import { loadData } from "./data-loader.js";

test("loadData fetches the 7 files and maps them to the data contract keys", async () => {
  const fixtures = {
    "data/projects.json": [{ id: "p1" }],
    "data/maisons.json": [{ id: "m1" }],
    "data/project_maison_status.json": [{ project_id: "p1", maison_id: "m1", status: "Run" }],
    "data/status_config.json": [{ status: "Run", category: "run" }],
    "data/maison_budget.json": [{ maison_id: "m1", notes: [] }],
    "data/status_legend.json": [{ status: "Run", description: "x" }],
    "data/images.json": [{ maison_id: "m1", images: [] }],
  };
  const seen = [];
  const seenOpts = [];
  globalThis.fetch = async (url, opts) => {
    seen.push(url);
    seenOpts.push(opts);
    return { ok: true, json: async () => fixtures[url] };
  };
  const data = await loadData("data");
  assert.deepStrictEqual(new Set(seen), new Set(Object.keys(fixtures)));
  // every data fetch must bypass the browser cache so a refresh shows fresh JSON
  assert.ok(seenOpts.length === 7 && seenOpts.every((o) => o && o.cache === "no-store"),
    "all loadData fetches must use { cache: 'no-store' }");
  assert.strictEqual(data.records.length, 1);
  assert.strictEqual(data.statusConfig[0].status, "Run");
  assert.strictEqual(data.budget[0].maison_id, "m1");
  assert.strictEqual(data.legend[0].status, "Run");
  assert.strictEqual(data.images[0].maison_id, "m1");
  assert.strictEqual(data.projects[0].id, "p1");
  assert.strictEqual(data.maisons[0].id, "m1");
});
