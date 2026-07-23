export function indexById(list) {
  return new Map(list.map((x) => [x.id, x]));
}

export function recordsForMaison(maisonId, records) {
  return records.filter((r) => r.maison_id === maisonId);
}

export function recordsForProject(projectId, records) {
  return records.filter((r) => r.project_id === projectId);
}

export function applyFilters(records, { projects, maisons, filters }) {
  const P = indexById(projects);
  const M = indexById(maisons);
  return records.filter((r) => {
    const p = P.get(r.project_id);
    const m = M.get(r.maison_id);
    if (filters.division && (!m || m.division !== filters.division)) return false;
    if (filters.maison && r.maison_id !== filters.maison) return false;
    if (filters.owner && (!p || p.owner !== filters.owner)) return false;
    if (filters.status && r.status !== filters.status) return false;
    if (filters.priority && (!p || p.priority !== filters.priority)) return false;
    if (filters.domain && (!p || p.domain !== filters.domain)) return false;
    return true;
  });
}

function distinct(values) {
  return [...new Set(values.filter((v) => v))].sort();
}

export function filterOptions(projects, maisons, statusConfig = []) {
  return {
    divisions: distinct(maisons.map((m) => m.division)),
    owners: distinct(projects.map((p) => p.owner)),
    priorities: distinct(projects.map((p) => p.priority)),
    domains: distinct(projects.map((p) => p.domain)),
    statuses: statusConfig.map((c) => c.status),
    maisons: [...maisons].sort((a, b) => a.name.localeCompare(b.name)),
  };
}
