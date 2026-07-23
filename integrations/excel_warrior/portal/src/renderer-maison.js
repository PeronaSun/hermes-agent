import { escapeHtml, statusBadge, CATEGORY } from "./format.js";
import { getMaisonSummary } from "./stats.js";
import { indexById, recordsForMaison } from "./selectors.js";

function statsBar(sum) {
  const cells = [["Total", sum.total], ...CATEGORY.map((c) => [c.label, sum[c.key]])];
  return `<div class="stats">${cells
    .map(([label, n]) => `<div class="stat"><b>${n}</b><span>${label}</span></div>`)
    .join("")}</div>`;
}

export function renderMaisonDetail(maisonId, data) {
  const { projects, maisons, records, statusConfig, budget, images } = data;
  const M = indexById(maisons).get(maisonId);
  if (!M) return `<h1>Maison</h1><p class="empty">Maison not found: ${escapeHtml(maisonId)}</p>`;
  const P = indexById(projects);
  const rows = recordsForMaison(maisonId, records);
  const sum = getMaisonSummary(maisonId, records, statusConfig);

  const projectRows = rows.length
    ? rows.map((r) => {
        const p = P.get(r.project_id) || {};
        return `<tr><td>${escapeHtml(p.name || r.project_id)}</td><td>${escapeHtml(p.owner || "")}</td>
          <td>${statusBadge(r.status, statusConfig)}</td><td class="rmk">${escapeHtml(r.remark || "")}</td></tr>`;
      }).join("")
    : `<tr><td colspan="4" class="empty">No projects</td></tr>`;

  const gallery = (images.find((i) => i.maison_id === maisonId)?.images || []);
  const galleryHtml = gallery.length
    ? `<div class="gallery">${gallery.map((src) => `<img class="gallery-img" src="${escapeHtml(src)}" loading="lazy" alt="">`).join("")}</div>`
    : `<div class="empty">No images</div>`;

  const notes = (budget.find((b) => b.maison_id === maisonId)?.notes || []);
  const b26Html = notes.length
    ? `<dl class="b26">${notes.map((n) => `<dt>${escapeHtml(n.key)}</dt><dd>${escapeHtml(n.value)}</dd>`).join("")}</dl>`
    : `<div class="empty">No budget notes</div>`;

  return `
    <div class="detail-hd">
      <h1>${escapeHtml(M.name)}</h1>
      <div class="meta">${escapeHtml(M.division || "")} · Key Account: ${escapeHtml(M.key_account || "—")} · AI Champion: ${escapeHtml(M.ai_champion || "—")}</div>
    </div>
    <div class="detail-body">
      ${statsBar(sum)}
      <div class="tabs" data-tabs>
        <button class="active" data-tab="projects">Projects</button>
        <button data-tab="gallery">Gallery</button>
        <button data-tab="b26">B26</button>
      </div>
      <div class="tab-panel active" data-panel="projects">
        <table class="list"><thead><tr><th>Project</th><th>Owner</th><th>Status</th><th>Remark</th></tr></thead>
        <tbody>${projectRows}</tbody></table>
      </div>
      <div class="tab-panel" data-panel="gallery">${galleryHtml}</div>
      <div class="tab-panel" data-panel="b26">${b26Html}</div>
    </div>
  `;
}
