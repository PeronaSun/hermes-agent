export const CATEGORY = [
  { key: "ideation", label: "Ideation" },
  { key: "pilot", label: "Pilot" },
  { key: "scale", label: "Scale" },
  { key: "run", label: "Run" },
  { key: "done", label: "Done" },
];

export function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function categoryOf(status, statusConfig) {
  if (!status) return null;
  const hit = statusConfig.find((c) => c.status === status);
  return hit ? hit.category : null;
}

export function categoryClass(status, statusConfig) {
  const cat = categoryOf(status, statusConfig);
  return cat ? `cat-${cat}` : "";
}

export function statusBadge(status, statusConfig, legendMap) {
  if (!status) return "";
  const desc = legendMap?.get(status);
  const title = desc ? ` title="${escapeHtml(desc)}"` : "";
  return `<span class="badge ${categoryClass(status, statusConfig)}"${title}>${escapeHtml(status)}</span>`;
}
