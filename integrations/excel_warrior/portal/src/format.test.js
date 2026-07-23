import { test } from "node:test";
import assert from "node:assert";
import { escapeHtml, categoryOf, categoryClass, statusBadge, CATEGORY } from "./format.js";

const cfg = [
  { status: "Ideation", category: "ideation" },
  { status: "Q3-Pilot", category: "pilot" },
  { status: "Run", category: "run" },
];

test("escapeHtml neutralizes markup", () => {
  assert.strictEqual(escapeHtml('<a>&"x'), "&lt;a&gt;&amp;&quot;x");
});

test("categoryOf maps status via config, null when unknown/empty", () => {
  assert.strictEqual(categoryOf("Q3-Pilot", cfg), "pilot");
  assert.strictEqual(categoryOf("Nope", cfg), null);
  assert.strictEqual(categoryOf("", cfg), null);
});

test("categoryClass returns cat-<category> or empty string", () => {
  assert.strictEqual(categoryClass("Run", cfg), "cat-run");
  assert.strictEqual(categoryClass("Nope", cfg), "");
});

test("statusBadge renders an escaped badge with category class; empty -> ''", () => {
  const html = statusBadge("Q3-Pilot", cfg);
  assert.ok(html.includes("cat-pilot"));
  assert.ok(html.includes("Q3-Pilot"));
  assert.strictEqual(statusBadge("", cfg), "");
});

test("CATEGORY lists the five categories in stage order", () => {
  assert.deepStrictEqual(CATEGORY.map(c => c.key),
    ["ideation", "pilot", "scale", "run", "done"]);
});
