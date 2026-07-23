async function getJson(url) {
  // no-store: always re-fetch the live JSON from the server, never serve a
  // stale copy from the browser cache (so a normal refresh shows agent edits).
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load ${url}: ${res.status}`);
  return res.json();
}

export async function loadData(base = "data") {
  const [projects, maisons, records, statusConfig, budget, legend, images] = await Promise.all([
    getJson(`${base}/projects.json`),
    getJson(`${base}/maisons.json`),
    getJson(`${base}/project_maison_status.json`),
    getJson(`${base}/status_config.json`),
    getJson(`${base}/maison_budget.json`),
    getJson(`${base}/status_legend.json`),
    getJson(`${base}/images.json`),
  ]);
  return { projects, maisons, records, statusConfig, budget, legend, images };
}
