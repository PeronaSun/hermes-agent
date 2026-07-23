import { categoryOf } from "./format.js";

export function countByStatusCategory(records, statusConfig) {
  const out = { total: 0, ideation: 0, pilot: 0, scale: 0, run: 0, done: 0 };
  for (const r of records) {
    out.total += 1;
    const cat = categoryOf(r.status, statusConfig);
    if (cat && cat in out) out[cat] += 1;
  }
  return out;
}

export function getGlobalSummary(records, statusConfig) {
  return countByStatusCategory(records, statusConfig);
}

export function getProjectSummary(projectId, records, statusConfig) {
  return countByStatusCategory(records.filter((r) => r.project_id === projectId), statusConfig);
}

export function getMaisonSummary(maisonId, records, statusConfig) {
  return countByStatusCategory(records.filter((r) => r.maison_id === maisonId), statusConfig);
}
