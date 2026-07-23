function daysBetween(a, b) {
  return Math.round((Date.parse(a) - Date.parse(b)) / 86400000);
}

export function validateData({ projects, maisons, records, statusConfig, today, staleDays = 90 }) {
  const pids = new Set(projects.map((p) => p.id));
  const mids = new Set(maisons.map((m) => m.id));
  const known = new Set(statusConfig.map((c) => c.status));
  const now = today || new Date().toISOString().slice(0, 10);

  const combo = new Map();
  for (const r of records) {
    const k = `${r.project_id}|${r.maison_id}`;
    combo.set(k, (combo.get(k) || 0) + 1);
  }

  const rep = {
    valid_records: records.length,
    missing_project_refs: records.filter((r) => !pids.has(r.project_id)).length,
    missing_maison_refs: records.filter((r) => !mids.has(r.maison_id)).length,
    invalid_statuses: records.filter((r) => !known.has(r.status)).length,
    duplicate_records: [...combo.values()].filter((n) => n > 1).length,
    empty_owner_projects: projects.filter((p) => !p.owner).length,
    empty_maison_names: maisons.filter((m) => !m.name).length,
    empty_statuses: records.filter((r) => !r.status).length,
    stale_records: records.filter((r) => r.updated_at && daysBetween(now, r.updated_at) > staleDays).length,
  };
  rep.ok = rep.missing_project_refs === 0 && rep.missing_maison_refs === 0 &&
    rep.invalid_statuses === 0 && rep.duplicate_records === 0 &&
    rep.empty_maison_names === 0 && rep.empty_statuses === 0;
  return rep;
}
