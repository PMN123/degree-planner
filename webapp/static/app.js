/* Boiler Degree Planner — single-page app (vanilla ES modules, no build step). */

const STORAGE_KEY = "boiler-degree-planner.v1";
const RING_CIRC = 2 * Math.PI * 52;

const state = {
  allPrograms: [],
  selected: [],          // program ids
  completed: [],         // [{code,title,credits}]
  semesters: [],         // [{term, courses:[{code,title,credits}]}]
  gradTarget: "",
  theme: "dark",
  lastAudit: null,
};

// ---------- tiny helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return node;
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  return res.json();
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  requestAnimationFrame(() => t.classList.add("show"));
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.classList.remove("show"); setTimeout(() => (t.hidden = true), 200); }, 2600);
}

const fmtCr = (n) => (Number.isInteger(n) ? n : Number(n).toFixed(1));

// ---------- persistence ----------
function save() {
  const { allPrograms, lastAudit, ...persist } = state;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(persist));
}
function loadSaved() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) Object.assign(state, JSON.parse(raw));
  } catch { /* ignore */ }
}

// =====================================================================
// Rendering
// =====================================================================

function renderProgramPicker() {
  const wrap = $("#program-picker");
  wrap.innerHTML = "";
  for (const p of state.allPrograms) {
    const active = state.selected.includes(p.id);
    wrap.append(
      el("button", {
        class: "program-pill" + (active ? " active" : ""),
        type: "button",
        title: `${p.college || ""} · ${p.total_credits || "?"} cr`,
        onclick: () => toggleProgram(p.id),
      }, p.name, el("span", { class: "deg" }, p.degree || ""))
    );
  }
}

function courseChip(c, onRemove) {
  return el("div", { class: "course-chip" },
    el("span", { class: "cc-code" }, c.code),
    c.title ? el("span", { class: "cc-title hint" }, truncate(c.title, 22)) : null,
    el("span", { class: "cc-cr" }, `${fmtCr(c.credits)}cr`),
    el("button", { class: "cc-x", type: "button", "aria-label": "Remove", onclick: onRemove }, "✕"),
  );
}

const truncate = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");

function renderCompleted() {
  const list = $("#completed-list");
  list.innerHTML = "";
  state.completed.forEach((c, i) => {
    list.append(courseChip(c, () => { state.completed.splice(i, 1); commit(); }));
  });
}

function renderSemesters() {
  const wrap = $("#semesters");
  wrap.innerHTML = "";
  const loads = (state.lastAudit?.loads) || [];
  const prereqByCode = {};
  for (const chk of state.lastAudit?.prerequisites?.checks || []) {
    if (!chk.ok) prereqByCode[`${chk.term}|${chk.code}`] = chk;
  }
  state.semesters.forEach((sem, si) => {
    const credits = sem.courses.reduce((a, c) => a + (Number(c.credits) || 0), 0);
    const load = loads.find((l) => l.term === sem.term);
    const flag = load?.flag;
    const card = el("div", { class: "semester" });
    card.append(
      el("div", { class: "sem-head" },
        el("input", { class: "sem-term", value: sem.term, onchange: (e) => { sem.term = e.target.value; commit(); } }),
        el("span", { class: "sem-credits" + (flag ? " " + flag : "") }, `${fmtCr(credits)} cr`),
        el("button", { class: "cc-x sem-del", type: "button", title: "Delete semester", onclick: () => { state.semesters.splice(si, 1); commit(); } }, "✕"),
      )
    );
    const courses = el("div", { class: "sem-courses" });
    sem.courses.forEach((c, ci) => {
      const isPh = c.code.startsWith("PLACEHOLDER");
      const bad = prereqByCode[`${sem.term}|${c.code}`];
      const row = el("div", { class: "sem-course" + (bad ? " bad" : "") + (isPh ? " placeholder" : "") },
        el("span", { class: "sc-code" }, isPh ? "▦" : c.code),
        el("span", { class: "sc-title" }, isPh ? "Placeholder / elective" : (c.title || "")),
        bad ? el("span", { class: "sc-flag", title: bad.missing_best_alternative?.join(", ") || "prerequisite issue" }, "⚠") : null,
        el("span", { class: "sc-cr" }, `${fmtCr(c.credits)}`),
        el("button", { class: "cc-x", type: "button", onclick: () => { sem.courses.splice(ci, 1); commit(); } }, "✕"),
      );
      courses.append(row);
    });
    card.append(courses);
    card.append(makeSearchRow({ semIndex: si }));
    wrap.append(card);
  });
}

function makeSearchRow({ semIndex } = {}) {
  const input = el("input", { class: "course-input", type: "text", placeholder: "Add course…", autocomplete: "off" });
  const pop = el("div", { class: "search-pop", hidden: true });
  const row = el("div", { class: "add-course-row" }, el("div", { class: "course-search" }, input, pop));
  wireSearch(input, pop, (course) => addCourse(course, { semIndex }));
  return row;
}

// =====================================================================
// Course search autocomplete
// =====================================================================

function wireSearch(input, pop, onPick) {
  let items = [];
  let active = -1;

  const renderPop = (results, query) => {
    pop.innerHTML = "";
    items = results;
    active = -1;
    if (!results.length) {
      const code = normalizeMaybeCode(query);
      if (code) {
        pop.append(el("div", { class: "search-item", onmousedown: (e) => { e.preventDefault(); fetchAndPick(code, onPick, pop, input); } },
          el("span", { class: "si-code" }, "↟"),
          el("span", { class: "si-title" }, `Fetch “${code}” from the Purdue catalog`)));
      } else {
        pop.append(el("div", { class: "search-empty" }, "No matches"));
      }
      pop.hidden = false;
      return;
    }
    results.forEach((r, i) => {
      pop.append(el("div", { class: "search-item", "data-i": i, onmousedown: (e) => { e.preventDefault(); pick(r); } },
        el("span", { class: "si-code" }, r.code),
        el("span", { class: "si-title", title: r.title || "" }, r.title || ""),
        el("span", { class: "si-cr" }, r.credits != null ? `${fmtCr(r.credits)}cr` : "")));
    });
    pop.hidden = false;
  };

  const pick = (r) => {
    onPick({ code: r.code, title: r.title || "", credits: r.credits != null ? Number(r.credits) : 3 });
    input.value = "";
    pop.hidden = true;
  };

  const doSearch = debounce(async (q) => {
    if (!q.trim()) { pop.hidden = true; return; }
    const { results } = await api(`/api/courses/search?q=${encodeURIComponent(q)}`);
    renderPop(results, q);
  }, 160);

  input.addEventListener("input", () => doSearch(input.value));
  input.addEventListener("focus", () => { if (input.value.trim()) doSearch(input.value); });
  input.addEventListener("blur", () => setTimeout(() => (pop.hidden = true), 150));
  input.addEventListener("keydown", (e) => {
    const rows = $$(".search-item", pop);
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, rows.length - 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (active >= 0 && items[active]) pick(items[active]);
      else if (items[0]) pick(items[0]);
      else { const code = normalizeMaybeCode(input.value); if (code) fetchAndPick(code, onPick, pop, input); }
      return;
    } else if (e.key === "Escape") { pop.hidden = true; return; }
    rows.forEach((row, i) => row.classList.toggle("active", i === active));
  });
}

function normalizeMaybeCode(q) {
  const m = (q || "").trim().toUpperCase().replace(/-/g, " ").match(/^([A-Z]{2,5})\s*([0-9]{3,5})$/);
  if (!m) return null;
  let [, subj, num] = m;
  if (num.length < 5) num = num.padStart(5, "0");
  return `${subj} ${num}`;
}

async function fetchAndPick(code, onPick, pop, input) {
  pop.innerHTML = "";
  pop.append(el("div", { class: "search-empty" }, `Fetching ${code} from Purdue…`));
  const res = await api("/api/courses/ensure", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (res.ok && res.course) {
    onPick({ code: res.course.code, title: res.course.title || "", credits: res.course.credits != null ? Number(res.course.credits) : 3 });
    input.value = "";
    pop.hidden = true;
    toast(`Added ${res.course.code} from the live catalog`);
  } else {
    pop.innerHTML = "";
    pop.append(el("div", { class: "search-empty" }, res.error || `Couldn't fetch ${code}`));
  }
}

// =====================================================================
// Mutations
// =====================================================================

function toggleProgram(id) {
  const i = state.selected.indexOf(id);
  if (i >= 0) state.selected.splice(i, 1);
  else state.selected.push(id);
  commit();
}

function addCourse(course, { semIndex } = {}) {
  const target = semIndex != null ? state.semesters[semIndex].courses : state.completed;
  if (target.some((c) => c.code === course.code)) { toast(`${course.code} is already in your plan`); return; }
  target.push(course);
  commit();
}

function addSemester() {
  const last = state.semesters[state.semesters.length - 1];
  state.semesters.push({ term: nextTerm(last?.term), courses: [] });
  commit();
}

function nextTerm(prev) {
  if (!prev) return "Fall 2026";
  const m = prev.match(/(Fall|Spring|Summer)\s+(\d{4})/i);
  if (!m) return "New Term";
  const [, season, yr] = m;
  const y = Number(yr);
  if (/fall/i.test(season)) return `Spring ${y + 1}`;
  if (/spring/i.test(season)) return `Fall ${y}`;
  return `Fall ${y}`;
}

// commit = persist + re-render + re-audit
function commit() {
  save();
  renderProgramPicker();
  renderCompleted();
  renderSemesters();
  runAudit();
}

// =====================================================================
// Audit
// =====================================================================

const runAudit = debounce(async () => {
  const plan = {
    programs: state.selected,
    completed: state.completed,
    semesters: state.semesters,
  };
  const hasContent = state.selected.length || state.completed.length || state.semesters.some((s) => s.courses.length);
  if (!hasContent) { state.lastAudit = null; renderResults(null); return; }
  const result = await api("/api/audit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(plan),
  });
  state.lastAudit = result;
  renderResults(result);
  renderSemesters(); // refresh prereq flags on course rows
}, 200);

function renderResults(audit) {
  renderSummary(audit);
  renderCoverage(audit);
  renderPrereqs(audit);
  renderLoads(audit);
  if (audit && state.pendingOpen) {
    const p = audit.programs.find((x) => x.id === state.pendingOpen);
    state.pendingOpen = null;
    if (p) openModal(p);
  }
}

function renderSummary(audit) {
  const feas = $("#feasibility");
  const txt = $(".status-text", feas);
  feas.className = "feasibility";
  if (!audit) {
    txt.textContent = "Build a plan to audit";
    setRing(0, 120);
    $("#credit-legend").innerHTML = "";
    return;
  }
  const c = audit.credits;
  if (audit.feasible) { feas.classList.add("ok"); txt.textContent = "On track to graduate"; }
  else { feas.classList.add("warn"); txt.textContent = "Gaps remain — see below"; }
  setRing(c.total, c.degree_target);
  $("#credit-legend").innerHTML = "";
  $("#credit-legend").append(
    el("span", {}, "done ", el("b", {}, fmtCr(c.completed))),
    el("span", {}, "planned ", el("b", {}, fmtCr(c.planned))),
    el("span", {}, "target ", el("b", {}, fmtCr(c.degree_target))),
  );
}

function setRing(total, target) {
  const pct = Math.min(total / (target || 120), 1);
  $(".ring-value").style.strokeDashoffset = String(RING_CIRC * (1 - pct));
  $("#ring-credits").textContent = fmtCr(total);
  $("#ring-target").textContent = `/ ${fmtCr(target)} cr`;
}

function renderCoverage(audit) {
  const wrap = $("#program-coverage");
  wrap.innerHTML = "";
  if (!audit || !audit.programs.length) {
    wrap.append(el("div", { class: "empty-note" }, "Select a major to see degree coverage."));
    return;
  }
  for (const p of audit.programs) {
    const pct = p.progress.percent;
    wrap.append(
      el("div", { class: "coverage-row", onclick: () => openModal(p) },
        el("div", { class: "cov-top" },
          el("div", { class: "cov-name" }, p.name, " ", el("span", { class: "deg" }, p.degree || p.type)),
          el("div", { class: "cov-pct" }, `${pct}%`)),
        el("div", { class: "cov-bar" }, el("span", { style: `width:${pct}%` })),
        el("div", { class: "cov-meta" },
          el("span", {}, p.satisfied ? el("span", { class: "cov-check" }, "✓ all requirements met") : `${p.progress.have} / ${p.progress.need} requirements`),
          el("span", {}, "view details →")),
      )
    );
  }
  // overlap note
  const ov = Object.keys(audit.overlaps || {});
  if (ov.length) wrap.append(el("div", { class: "empty-note" }, `${ov.length} course${ov.length > 1 ? "s" : ""} shared across degrees (double-counted).`));
}

function renderPrereqs(audit) {
  const list = $("#prereq-list");
  const count = $("#prereq-count");
  list.innerHTML = "";
  if (!audit) { count.textContent = ""; list.append(el("div", { class: "empty-note" }, "—")); return; }
  const checks = audit.prerequisites.checks || [];
  const failed = checks.filter((c) => !c.ok);
  count.textContent = `${checks.length - failed.length}/${checks.length} ok`;
  if (!failed.length) { list.append(el("div", { class: "issue ok-issue" }, el("span", { class: "i-dot" }, "✓"), el("div", { class: "i-body" }, el("div", { class: "i-msg" }, "All planned courses have their prerequisites satisfied in earlier terms.")))); return; }
  for (const f of failed) {
    let msg;
    if (f.status === "missing_catalog") msg = "Not in the scraped catalog — search it to fetch prereqs.";
    else if (f.missing_best_alternative?.length) msg = el("span", {}, "Needs ", ...f.missing_best_alternative.flatMap((c, i) => [i ? ", " : "", el("code", {}, c)]));
    else msg = (f.warnings || []).join("; ") || "Prerequisite not satisfied.";
    list.append(el("div", { class: "issue bad" },
      el("span", { class: "i-dot" }, "!"),
      el("div", { class: "i-body" },
        el("div", { class: "i-code" }, `${f.code} · ${f.term}`),
        el("div", { class: "i-msg" }, msg))));
  }
}

function renderLoads(audit) {
  const list = $("#loads-list");
  list.innerHTML = "";
  if (!audit || !audit.loads.length) { list.append(el("div", { class: "empty-note" }, "Add semesters to see term loads.")); return; }
  const max = Math.max(18, ...audit.loads.map((l) => l.credits));
  for (const l of audit.loads) {
    list.append(el("div", { class: "load-row" },
      el("span", { class: "load-term" }, l.term),
      el("div", { class: "load-bar" + (l.flag ? " " + l.flag : "") }, el("span", { style: `width:${Math.min(100, (l.credits / max) * 100)}%` })),
      el("span", { class: "load-cr" }, `${fmtCr(l.credits)} cr`)));
  }
}

// =====================================================================
// Requirement modal
// =====================================================================

function openModal(program) {
  $("#modal-title").textContent = `${program.name}${program.degree ? " " + program.degree : ""}`;
  $("#modal-sub").textContent = `${program.college || ""}${program.total_credits ? " · " + program.total_credits + " cr" : ""} · ${program.progress.percent}% complete`;
  const body = $("#modal-body");
  body.innerHTML = "";
  for (const req of program.requirements) body.append(renderReq(req));
  if (program.notes?.length || program.source_url) {
    const notes = el("div", { class: "modal-notes" });
    (program.notes || []).forEach((n) => notes.append(el("div", {}, n)));
    if (program.source_url) notes.append(el("div", {}, "Catalog: ", el("a", { href: program.source_url, target: "_blank", rel: "noopener" }, program.source_url)));
    body.append(notes);
  }
  $("#modal").hidden = false;
}

function reqBadge(node) {
  if (node.satisfied) return el("span", { class: "req-badge ok" }, "✓");
  const have = node.progress?.have || 0;
  return have > 0 ? el("span", { class: "req-badge partial" }, "◐") : el("span", { class: "req-badge no" }, "");
}

function progLabel(node) {
  const p = node.progress;
  if (!p) return "";
  if (p.unit === "credits") return `${p.have} / ${p.need} cr`;
  if (p.unit === "option") return node.satisfied ? "met" : "needed";
  return `${p.have} / ${p.need}`;
}

function courseOpt(c) {
  return el("span", { class: "opt" + (c.satisfied ? " have" : "") }, c.code);
}

function renderReq(node, depth = 0) {
  const wrap = el("div", { class: "req" });
  wrap.append(el("div", { class: "req-head" }, reqBadge(node),
    el("span", { class: "req-name" }, node.name || node.kind),
    el("span", { class: "req-prog" }, progLabel(node))));
  if (node.note) wrap.append(el("div", { class: "req-note" }, node.note));

  const kind = node.kind;
  if (kind === "track_select") {
    for (const tr of node.tracks || []) {
      const sub = el("div", { class: "req-sub" });
      sub.append(renderReq(tr, depth + 1));
      wrap.append(sub);
    }
    return wrap;
  }

  const children = node.children || [];
  const courseKids = children.filter((c) => c.type === "course");
  const nodeKids = children.filter((c) => c.type !== "course");

  if (courseKids.length) {
    const chips = el("div", { class: "req-courses" });
    courseKids.forEach((c) => chips.append(courseOpt(c)));
    wrap.append(chips);
  }
  if (nodeKids.length) {
    const sub = el("div", { class: "req-sub" });
    nodeKids.forEach((c) => sub.append(renderReq(c, depth + 1)));
    wrap.append(sub);
  }
  if (kind === "credits" && node.placeholder) {
    wrap.append(el("div", { class: "req-note" }, "Placeholder bucket — fill with approved courses for this category."));
  }
  return wrap;
}

function closeModal() { $("#modal").hidden = true; }

// =====================================================================
// Theme + top bar
// =====================================================================

function applyTheme() {
  document.documentElement.setAttribute("data-theme", state.theme);
  $(".theme-icon").textContent = state.theme === "dark" ? "◑" : "◐";
}
function toggleTheme() { state.theme = state.theme === "dark" ? "light" : "dark"; applyTheme(); save(); }

// =====================================================================
// Example plan + reset
// =====================================================================

const EXAMPLE = {
  selected: ["computer-science-bs", "mathematics-bs"],
  gradTarget: "Fall 2028",
  completed: [
    { code: "CS 18000", title: "Problem Solving & OOP", credits: 4 },
    { code: "MA 16100", title: "Plane Analytic Geometry & Calculus I", credits: 5 },
    { code: "MA 16200", title: "Plane Analytic Geometry & Calculus II", credits: 5 },
    { code: "MA 26100", title: "Multivariate Calculus", credits: 4 },
    { code: "MA 26500", title: "Linear Algebra", credits: 3 },
    { code: "MA 35301", title: "Linear Algebra II", credits: 3 },
  ],
  semesters: [
    { term: "Fall 2026", courses: [
      { code: "CS 18200", title: "Foundations of Computer Science", credits: 3 },
      { code: "CS 24000", title: "Programming in C", credits: 3 },
      { code: "MA 36600", title: "Ordinary Differential Equations", credits: 4 },
    ]},
    { term: "Spring 2027", courses: [
      { code: "CS 25000", title: "Computer Architecture", credits: 4 },
      { code: "CS 25100", title: "Data Structures & Algorithms", credits: 3 },
      { code: "MA 34100", title: "Foundations of Analysis", credits: 3 },
    ]},
    { term: "Fall 2027", courses: [
      { code: "CS 25200", title: "Systems Programming", credits: 4 },
      { code: "CS 38100", title: "Analysis of Algorithms", credits: 3 },
      { code: "MA 42500", title: "Elements of Complex Analysis", credits: 3 },
    ]},
    { term: "Spring 2028", courses: [
      { code: "CS 37300", title: "Data Mining & Machine Learning", credits: 3 },
      { code: "CS 47100", title: "Introduction to AI", credits: 3 },
      { code: "MA 45300", title: "Elements of Algebra I", credits: 3 },
    ]},
  ],
};

function loadExample() {
  state.selected = [...EXAMPLE.selected];
  state.completed = EXAMPLE.completed.map((c) => ({ ...c }));
  state.semesters = EXAMPLE.semesters.map((s) => ({ term: s.term, courses: s.courses.map((c) => ({ ...c })) }));
  state.gradTarget = EXAMPLE.gradTarget;
  $("#grad-target").value = state.gradTarget;
  commit();
  toast("Loaded an example CS + Math plan");
}

function resetPlan() {
  state.selected = [];
  state.completed = [];
  state.semesters = [{ term: "Fall 2026", courses: [] }];
  state.gradTarget = "";
  $("#grad-target").value = "";
  commit();
}

// =====================================================================
// Init
// =====================================================================

async function init() {
  loadSaved();
  const params = new URLSearchParams(location.search);
  if (params.get("theme") === "light" || params.get("theme") === "dark") state.theme = params.get("theme");
  applyTheme();

  const meta = await api("/api/meta");
  $("#catalog-meta").textContent = `${meta.catalog_courses} courses · term ${meta.catalog_term}`;
  const { programs } = await api("/api/programs");
  state.allPrograms = programs;

  if (!state.semesters.length) state.semesters = [{ term: "Fall 2026", courses: [] }];
  $("#grad-target").value = state.gradTarget || "";

  // shared completed-course search row
  wireSearch($(".completed-card .course-input"), $(".completed-card .search-pop"), (course) => addCourse(course, {}));

  // top bar
  $("#theme-toggle").addEventListener("click", toggleTheme);
  $("#load-example").addEventListener("click", loadExample);
  $("#reset-plan").addEventListener("click", resetPlan);
  $("#add-semester").addEventListener("click", addSemester);
  $("#grad-target").addEventListener("change", (e) => { state.gradTarget = e.target.value; save(); });
  $("#modal-close").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  if (params.get("open")) state.pendingOpen = params.get("open");
  if (params.get("demo") === "1" && !state.selected.length && !state.completed.length) loadExample();
  else commit();
}

init();
