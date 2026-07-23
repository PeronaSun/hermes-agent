const ROWS = [
  ["valid_records", "Valid records", false],
  ["missing_project_refs", "Missing project references", true],
  ["missing_maison_refs", "Missing maison references", true],
  ["invalid_statuses", "Invalid statuses", true],
  ["duplicate_records", "Duplicate records", true],
  ["empty_owner_projects", "Projects with empty owner", true],
  ["empty_maison_names", "Maisons with empty name", true],
  ["empty_statuses", "Records with empty status", true],
  ["stale_records", "Stale records (>90d)", false],
];

export function renderDataHealth(rep) {
  const items = ROWS.map(([key, label, isError]) => {
    const n = rep[key] ?? 0;
    const cls = isError && n > 0 ? "bad" : "ok";
    return `<li><span class="${cls}">${n}</span> — ${label}</li>`;
  }).join("");
  const verdict = rep.ok
    ? `<p class="ok"><b>Data is healthy.</b></p>`
    : `<p class="bad"><b>Data has issues — review above.</b></p>`;
  return `<h1>Data Health</h1>${verdict}<ul class="health">${items}</ul>`;
}
