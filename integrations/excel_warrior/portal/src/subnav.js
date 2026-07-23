import { escapeHtml } from "./format.js";

export function renderSubnav(view, { maisons = [], projects = [] }, activeId = null) {
  let items, prefix;
  if (view === "maison") { items = [...maisons].sort((a, b) => a.name.localeCompare(b.name)); prefix = "maison"; }
  else if (view === "project") { items = [...projects].sort((a, b) => a.name.localeCompare(b.name)); prefix = "project"; }
  else return "";
  const chips = items.map((it) =>
    `<a class="chip${it.id === activeId ? " active" : ""}" href="#/${prefix}/${encodeURIComponent(it.id)}">${escapeHtml(it.name)}</a>`
  ).join("");
  const lead = prefix === "maison" ? "Maison" : "Project";
  return `<div class="subnav-inner"><span class="subnav-lead">${lead}</span>${chips}</div>`;
}
