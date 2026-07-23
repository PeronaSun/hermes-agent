import { getGlobalSummary } from "./stats.js";
import { CATEGORY, escapeHtml, statusBadge } from "./format.js";

function countBy(items, keyFn) {
  const m = new Map();
  for (const it of items) {
    const k = keyFn(it);
    if (!k) continue;
    m.set(k, (m.get(k) || 0) + 1);
  }
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function card(num, cap) {
  return `<div class="card"><div class="num">${num}</div><div class="cap">${escapeHtml(cap)}</div></div>`;
}

function kvPanel(title, pairs) {
  const rows = pairs.length
    ? pairs.map(([k, v]) => `<div class="kv"><span>${escapeHtml(k)}</span><b>${v}</b></div>`).join("")
    : `<div class="empty">None</div>`;
  return `<div class="panel"><h2>${escapeHtml(title)}</h2>${rows}</div>`;
}

function statusBar(sum) {
  const total = sum.total || 1;
  const segs = CATEGORY
    .filter((c) => sum[c.key] > 0)
    .map((c) => `<div class="sb-seg cat-${c.key}" style="flex:${sum[c.key]}" title="${escapeHtml(c.label)}: ${sum[c.key]}"></div>`)
    .join("");

  const labels = CATEGORY.map((c) =>
    `<div class="sb-label">
      <span class="badge cat-${c.key}">${escapeHtml(c.label)}</span>
      <b>${sum[c.key]}</b>
    </div>`
  ).join("");

  return `<div class="panel sb-wrap">
    <h2>Status Breakdown <span class="sb-total">${total} total</span></h2>
    <div class="sb-bar">${segs || `<div class="sb-seg" style="flex:1;background:var(--border)"></div>`}</div>
    <div class="sb-labels">${labels}</div>
  </div>`;
}

function legendPanel(statusConfig, legend) {
  const cfg = statusConfig || [];
  if (!cfg.length) return "";
  const desc = new Map((legend || []).map((l) => [l.status, l.description]));
  const items = cfg.map((c) =>
    `<div class="legend-item">
      ${statusBadge(c.status, cfg)}
      <span class="legend-desc">${escapeHtml(desc.get(c.status) || "")}</span>
    </div>`
  ).join("");
  return `<div class="panel"><h2>Status Legend</h2><div class="legend-row">${items}</div></div>`;
}

export function renderDashboard(data) {
  const { projects, maisons, records, statusConfig, legend } = data;
  const sum = getGlobalSummary(records, statusConfig);
  const latest = records.map((r) => r.updated_at).filter(Boolean).sort().pop() || "—";

  const topCards = [
    card(projects.length, "Projects"),
    card(maisons.length, "Maisons"),
    card(sum.total, "Active Records"),
  ].join("");

  const byDivision = kvPanel("Maisons by Division", countBy(maisons, (m) => m.division));
  const byOwner = kvPanel("Projects by Owner", countBy(projects, (p) => p.owner));
  const legendHtml = legendPanel(statusConfig, legend);

  return `
    <div class="db-hd">
      <h1>Dashboard</h1>
      <span class="cap">Last updated: ${escapeHtml(latest)}</span>
    </div>
    <div class="cards">${topCards}</div>
    <div style="margin-top:16px">${statusBar(sum)}</div>
    <div class="cols" style="margin-top:16px">${byDivision}${byOwner}</div>
    ${legendHtml ? `<div style="margin-top:16px">${legendHtml}</div>` : ""}
  `;
}
