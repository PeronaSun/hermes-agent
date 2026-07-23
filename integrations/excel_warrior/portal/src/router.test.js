import { test } from "node:test";
import assert from "node:assert";
import { parseRoute } from "./router.js";

test("parseRoute defaults to dashboard", () => {
  assert.deepStrictEqual(parseRoute(""), { view: "dashboard", id: null });
  assert.deepStrictEqual(parseRoute("#/"), { view: "dashboard", id: null });
});

test("parseRoute reads view and optional id", () => {
  assert.deepStrictEqual(parseRoute("#/matrix"), { view: "matrix", id: null });
  assert.deepStrictEqual(parseRoute("#/maison/tiffany"), { view: "maison", id: "tiffany" });
  assert.deepStrictEqual(parseRoute("#/project/gen_ai"), { view: "project", id: "gen_ai" });
  assert.deepStrictEqual(parseRoute("#/health"), { view: "health", id: null });
});

test("parseRoute falls back to dashboard for unknown view", () => {
  assert.deepStrictEqual(parseRoute("#/bogus"), { view: "dashboard", id: null });
});
