import { test } from "node:test";
import assert from "node:assert";
import { renderSubnav } from "./subnav.js";

const data = { maisons: [{ id: "m2", name: "Bravo" }, { id: "m1", name: "Alpha" }], projects: [{ id: "p1", name: "Proj" }] };

test("subnav lists maisons sorted, active highlighted, links to detail", () => {
  const html = renderSubnav("maison", data, "m1");
  assert.ok(html.includes('class="subnav-lead">Maison'));                  // section label
  assert.ok(html.indexOf("Alpha") < html.indexOf("Bravo"));               // sorted by name
  assert.ok(/class="chip active"\s+href="#\/maison\/m1"/.test(html));      // active chip
  assert.ok(html.includes('href="#/maison/m2"'));
});

test("subnav for project view links to projects with its own lead label", () => {
  const html = renderSubnav("project", data, null);
  assert.ok(html.includes('class="subnav-lead">Project'));
  assert.ok(html.includes('href="#/project/p1"'));
});

test("subnav is empty string for non maison/project views", () => {
  assert.strictEqual(renderSubnav("dashboard", data, null), "");
  assert.strictEqual(renderSubnav("matrix", data, null), "");
});
