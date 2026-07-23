import { escapeHtml, statusBadge, CATEGORY } from "./format.js";
import { getProjectSummary } from "./stats.js";
import { indexById, recordsForProject } from "./selectors.js";

function statsBar(sum) {
  const cells = [["Total", sum.total], ...CATEGORY.map((c) => [c.label, sum[c.key]])];
  return `<div class="stats">${cells
    .map(([label, n]) => `<div class="stat"><b>${n}</b><span>${label}</span></div>`)
    .join("")}</div>`;
}

export function renderProjectDetail(projectId, data) {
  const { projects, maisons, records, statusConfig } = data;
  const Pj = indexById(projects).get(projectId);
  if (!Pj) return `<h1>Project</h1><p class="empty">Project not found: ${escapeHtml(projectId)}</p>`;
  const M = indexById(maisons);
  const rows = recordsForProject(projectId, records);
  const sum = getProjectSummary(projectId, records, statusConfig);

  const coverage = rows.length
    ? rows.map((r) => {
        const m = M.get(r.maison_id) || {};
        return `<tr><td>${escapeHtml(m.name || r.maison_id)}</td><td>${escapeHtml(m.division || "")}</td>
          <td>${statusBadge(r.status, statusConfig)}</td><td class="rmk">${escapeHtml(r.remark || "")}</td>
          <td>${escapeHtml(r.updated_at || "")}</td></tr>`;
      }).join("")
    : `<tr><td colspan="5" class="empty">No maison coverage</td></tr>`;

  return `
    <div class="detail-hd">
      <h1>${escapeHtml(Pj.name)}</h1>
      <div class="meta">${escapeHtml(Pj.domain || "")} · ${escapeHtml(Pj.priority || "")} · Owner: ${escapeHtml(Pj.owner || "—")}</div>
    </div>
    <div class="detail-body">
      ${Pj.description ? `<p>${escapeHtml(Pj.description)}</p>` : ""}
      ${statsBar(sum)}
      <h2>Maison Coverage</h2>
      <table class="list"><thead><tr><th>Maison</th><th>Division</th><th>Status</th><th>Remark</th><th>Updated</th></tr></thead>
      <tbody>${coverage}</tbody></table>
    </div>
  `;
}
