import { test } from "node:test";
import assert from "node:assert";
import { renderDataHealth } from "./renderer-datahealth.js";

test("data health lists each metric and an overall verdict", () => {
  const rep = {
    valid_records: 216, missing_project_refs: 0, missing_maison_refs: 0, invalid_statuses: 0,
    duplicate_records: 0, empty_owner_projects: 0, empty_maison_names: 0, empty_statuses: 0,
    stale_records: 4, ok: true,
  };
  const html = renderDataHealth(rep);
  assert.ok(html.includes("216"));
  assert.ok(html.includes("Missing project references"));
  assert.ok(html.includes("Duplicate"));
  assert.ok(html.toLowerCase().includes("healthy") || html.toLowerCase().includes("ok"));
});

test("non-ok report flags problems", () => {
  const html = renderDataHealth({ valid_records: 1, missing_project_refs: 2, missing_maison_refs: 0,
    invalid_statuses: 0, duplicate_records: 0, empty_owner_projects: 0, empty_maison_names: 0,
    empty_statuses: 0, stale_records: 0, ok: false });
  assert.ok(html.includes("class=\"bad\""));
});
