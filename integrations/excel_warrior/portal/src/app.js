import { loadData } from "./data-loader.js";
import { parseRoute } from "./router.js";
import { filterOptions, applyFilters } from "./selectors.js";
import { validateData } from "./validate.js";
import { renderDashboard } from "./renderer-dashboard.js";
import { renderMatrix } from "./renderer-matrix.js";
import { renderMaisonDetail } from "./renderer-maison.js";
import { renderProjectDetail } from "./renderer-project.js";
import { renderDataHealth } from "./renderer-datahealth.js";
import { renderSubnav } from "./subnav.js";
import { escapeHtml } from "./format.js";

const state = { data: null, filters: {} };

function renderSidebar() {
  const o = filterOptions(state.data.projects, state.data.maisons, state.data.statusConfig);
  const sel = (key, label, values) => `
    <div class="fgroup"><label class="label">${label}</label>
      <select data-filter="${key}">
        <option value="">All</option>
        ${values.map((v) => `<option value="${escapeHtml(v)}"${state.filters[key] === v ? " selected" : ""}>${escapeHtml(v)}</option>`).join("")}
      </select>
    </div>`;
  const maisonSel = `
    <div class="fgroup"><label class="label">Maison</label>
      <select data-filter="maison">
        <option value="">All</option>
        ${o.maisons.map((m) => `<option value="${escapeHtml(m.id)}"${state.filters.maison === m.id ? " selected" : ""}>${escapeHtml(m.name)}</option>`).join("")}
      </select>
    </div>`;
  document.getElementById("sidebar").innerHTML =
    sel("division", "Division", o.divisions) + maisonSel +
    sel("owner", "Owner", o.owners) + sel("status", "Status", o.statuses) +
    sel("priority", "Priority", o.priorities) + sel("domain", "Domain", o.domains) +
    `<div class="reset" id="reset">Reset filters</div>`;
}

function renderMain() {
  const { view, id } = parseRoute(location.hash);
  const d = state.data;
  const filtered = applyFilters(d.records, { projects: d.projects, maisons: d.maisons, filters: state.filters });
  let html;
  if (view === "matrix") html = renderMatrix(d, filtered);
  else if (view === "maison") html = id ? renderMaisonDetail(id, d) : pickHint("maison");
  else if (view === "project") html = id ? renderProjectDetail(id, d) : pickHint("project");
  else if (view === "health") html = renderDataHealth(validateData({ ...d, today: new Date().toISOString().slice(0, 10) }));
  else html = renderDashboard(d);
  document.getElementById("main").innerHTML = html;
  document.getElementById("subnav").innerHTML = renderSubnav(view, d, id);

  const showSidebar = view === "matrix";
  document.getElementById("sidebar").style.display = showSidebar ? "" : "none";

  document.querySelectorAll("#nav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.view === view));
  wireTabs();
}

function pickHint(kind) {
  const label = kind === "maison" ? "Maison" : "Project";
  return `<h1>${label}</h1><p class="empty">Select a ${kind} from the bar above.</p>`;
}

function wireTabs() {
  const tabs = document.querySelector("[data-tabs]");
  if (!tabs) return;
  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-tab]");
    if (!btn) return;
    tabs.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll("[data-panel]").forEach((p) =>
      p.classList.toggle("active", p.dataset.panel === btn.dataset.tab));
  });
}

function wireTooltip() {
  const tip = document.createElement("div");
  tip.id = "tip";
  document.body.appendChild(tip);

  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest("[data-tip]");
    if (!el) return;
    tip.textContent = el.dataset.tip;
    tip.classList.remove("show");
    tip.style.display = "block";

    const r = el.getBoundingClientRect();
    const tr = tip.getBoundingClientRect();
    const gap = 8;

    const top = r.top >= tr.height + gap
      ? r.top - tr.height - gap          // above
      : r.bottom + gap;                  // flip below

    const left = Math.max(8, Math.min(
      r.right - tr.width,
      window.innerWidth - tr.width - 8
    ));

    tip.style.top = `${top}px`;
    tip.style.left = `${left}px`;
    tip.classList.add("show");
  });

  document.addEventListener("mouseout", (e) => {
    if (e.target.closest("[data-tip]")) { tip.classList.remove("show"); tip.style.display = ""; }
  });
}

function wireLightbox() {
  const box = document.getElementById("lightbox");
  const big = document.createElement("img");
  box.appendChild(big);
  document.addEventListener("click", (e) => {
    const im = e.target.closest && e.target.closest(".gallery-img");
    if (im) { big.src = im.src; box.classList.add("open"); return; }
    if (box.classList.contains("open")) box.classList.remove("open");
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") box.classList.remove("open"); });
}

function wireSidebar() {
  document.getElementById("sidebar").addEventListener("change", (e) => {
    const f = e.target.dataset.filter;
    if (!f) return;
    state.filters[f] = e.target.value;
    renderMain();
  });
  document.getElementById("sidebar").addEventListener("click", (e) => {
    if (e.target.id === "reset") { state.filters = {}; renderSidebar(); renderMain(); }
  });
}

async function init() {
  try {
    state.data = await loadData("data");
  } catch (err) {
    document.getElementById("main").innerHTML =
      `<p class="bad">Failed to load data: ${escapeHtml(err.message)}. Serve this page over http (e.g. <code>python3 -m http.server</code>), not file://.</p>`;
    return;
  }
  renderSidebar();
  wireSidebar();
  wireTooltip();
  wireLightbox();
  window.addEventListener("hashchange", renderMain);
  renderMain();
}

init();
