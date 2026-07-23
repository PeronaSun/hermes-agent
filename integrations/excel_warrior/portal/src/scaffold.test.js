import { test } from "node:test";
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const PORTAL = join(HERE, "..");

test("index.html has the five nav views and loads app.js as a module", () => {
  const html = readFileSync(join(PORTAL, "index.html"), "utf8");
  for (const label of ["Dashboard", "Matrix", "Maison", "Project", "Data Health"]) {
    assert.ok(html.includes(label), `nav missing ${label}`);
  }
  assert.ok(/<script[^>]+type="module"[^>]+src="src\/app\.js"/.test(html), "app.js module not loaded");
  assert.ok(html.includes('id="main"'), "missing #main container");
  assert.ok(html.includes('id="lightbox"'), "missing lightbox");
});

test("package.json declares ESM", () => {
  const pkg = JSON.parse(readFileSync(join(PORTAL, "package.json"), "utf8"));
  assert.strictEqual(pkg.type, "module");
});
