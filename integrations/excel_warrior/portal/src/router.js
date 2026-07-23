const VIEWS = new Set(["dashboard", "matrix", "maison", "project", "health"]);

export function parseRoute(hash) {
  const clean = (hash || "").replace(/^#\/?/, "");
  const [view, id] = clean.split("/");
  if (!view || !VIEWS.has(view)) return { view: "dashboard", id: null };
  return { view, id: id || null };
}
