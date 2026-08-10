import { create } from "zustand";
import { api } from "./api";
import type { Audit, Constraints, Course, SavedPlan, Semester } from "./types";

let _uid = 0;
const uid = () => `c${_uid++}`;
const withUids = (sems: Semester[]): Semester[] =>
  sems.map((s) => ({ term: s.term, courses: s.courses.map((c) => ({ ...c, uid: uid() })) }));

const STORAGE_KEY = "boilerplanner.v3";
const LEGACY_STORAGE_KEY = "boilerplanner.v2";
const COMBINATION_MAJORS = ["computer-science-bs", "mathematics-bs", "statistics-math-emphasis-bs"];
const COMBINATION_MINORS = ["finance-minor"];

interface State {
  ready: boolean;
  step: "wizard" | "board";
  theme: "dark" | "light";
  strict: boolean;
  activePlanId: string;
  planName: string;
  plans: SavedPlan[];
  availableTracks: import("./types").TrackOption[];
  pickedTracks: string[];
  majors: string[];
  minors: string[];
  completed: Course[];
  constraints: Constraints;
  semesters: Semester[];
  audit: Audit | null;
  targetStatus: { term: string; last_term: string; on_track: boolean } | null;
  busy: boolean;
  hoverUid: string | null;
  toast: string | null;

  init: () => Promise<void>;
  toggleProgram: (kind: "majors" | "minors", id: string) => void;
  switchPlan: (id: string) => void;
  renameActivePlan: (name: string) => void;
  createTargetWorkspaces: () => void;
  setConstraint: <K extends keyof Constraints>(k: K, v: Constraints[K]) => void;
  addCompleted: (c: Omit<Course, "uid">) => void;
  removeCompleted: (uid: string) => void;
  generate: () => Promise<void>;
  editPlan: () => void;
  runAudit: () => void;
  moveCourse: (uid: string, toTerm: string, toIndex: number) => void;
  removeCourse: (uid: string) => void;
  fillSlot: (uid: string, code: string, title: string, credits: number) => void;
  pickAlternative: (uid: string, code: string) => void;
  pickTrack: (uid: string, trackId: string) => void;
  addExtraTrack: (trackId: string) => void;
  fixCourse: (uid: string) => void;
  autoFix: () => Promise<void>;
  addCourseToTerm: (term: string, c: Omit<Course, "uid">) => void;
  addSemester: () => void;
  removeSemester: (term: string) => void;
  setHover: (uid: string | null) => void;
  toggleStrict: () => void;
  toggleTheme: () => void;
  setToast: (m: string | null) => void;
}

function planSnapshot(s: State): SavedPlan {
  return {
    id: s.activePlanId,
    name: s.planName.trim() || "Untitled plan",
    majors: s.majors,
    minors: s.minors,
    completed: s.completed,
    constraints: s.constraints,
    semesters: s.semesters,
    step: s.step,
    availableTracks: s.availableTracks,
    pickedTracks: s.pickedTracks,
  };
}

function persist(s: State) {
  const current = planSnapshot(s);
  const plans = s.plans.some((p) => p.id === current.id)
    ? s.plans.map((p) => p.id === current.id ? current : p)
    : [...s.plans, current];
  // Keep the in-memory collection current too. The active plan's ordinary state drives the
  // screen, while this collection lets a later tab switch restore the latest autosaved copy.
  s.plans = plans;
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ activePlanId: s.activePlanId, plans, theme: s.theme, strict: s.strict }));
}

function nextTermAfter(term: string, useSummers: boolean): string {
  const m = term.match(/(Fall|Spring|Summer)\s+(\d{4})/i);
  if (!m) return "Fall 2026";
  const season = m[1], year = +m[2];
  if (/fall/i.test(season)) return `Spring ${year + 1}`;
  if (/spring/i.test(season)) return useSummers ? `Summer ${year}` : `Fall ${year}`;
  return `Fall ${year}`;
}

const SUMMER_MAX_CREDITS = 9;
// Per-term credit ceiling — summer is short, so cap it lower than the user's normal max.
function termCap(term: string, max: number): number {
  return /summer/i.test(term) ? Math.min(max, SUMMER_MAX_CREDITS) : max;
}

let auditTimer: number | undefined;

export const useStore = create<State>((set, get) => ({
  ready: false,
  step: "wizard",
  theme: "dark",
  strict: false,
  activePlanId: "plan-1",
  planName: "My plan",
  plans: [],
  availableTracks: [],
  pickedTracks: [],
  majors: [],
  minors: [],
  completed: [],
  constraints: { start_term: "Fall 2026", target_term: "", max_credits: 16, use_summers: false },
  semesters: [],
  audit: null,
  targetStatus: null,
  busy: false,
  hoverUid: null,
  toast: null,

  init: async () => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const saved = JSON.parse(raw);
        const active = saved.plans?.find((p: SavedPlan) => p.id === saved.activePlanId) as SavedPlan | undefined;
        if (active) {
          set({
            activePlanId: active.id, planName: active.name, majors: active.majors || [], minors: active.minors || [],
            completed: withUids([{ term: "", courses: active.completed || [] }])[0].courses,
            constraints: active.constraints || get().constraints, semesters: withUids(active.semesters || []),
            step: active.step || "wizard", plans: saved.plans, availableTracks: active.availableTracks || [],
            pickedTracks: active.pickedTracks || [], theme: saved.theme || "dark", strict: Boolean(saved.strict),
          });
        }
      } else {
        // Bring the original single-plan local storage forward without losing anyone's draft.
        const legacy = localStorage.getItem(LEGACY_STORAGE_KEY);
        if (legacy) {
          const old = JSON.parse(legacy);
          set({
            majors: old.majors || [], minors: old.minors || [],
            completed: withUids([{ term: "", courses: old.completed || [] }])[0].courses,
            constraints: old.constraints || get().constraints, semesters: withUids(old.semesters || []),
            step: old.step || "wizard", theme: old.theme || "dark", strict: Boolean(old.strict),
          });
          persist(get());
        }
      }
    } catch { /* ignore */ }
    document.documentElement.setAttribute("data-theme", get().theme);
    set({ ready: true });
    if (get().step === "board" && get().semesters.length) get().runAudit();
  },

  toggleProgram: (kind, id) => {
    const cur = get()[kind];
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    set({ [kind]: next } as any);
    persist(get());
  },

  switchPlan: (id) => {
    if (id === get().activePlanId) return;
    persist(get());
    const next = get().plans.find((p) => p.id === id);
    if (!next) return;
    set({
      activePlanId: next.id, planName: next.name, majors: next.majors || [], minors: next.minors || [],
      completed: withUids([{ term: "", courses: next.completed || [] }])[0].courses,
      constraints: next.constraints || get().constraints, semesters: withUids(next.semesters || []),
      step: next.step || "wizard", availableTracks: next.availableTracks || [], pickedTracks: next.pickedTracks || [],
      audit: null, toast: `Opened ${next.name}`,
    });
    persist(get());
    if (get().step === "board" && get().semesters.length) get().runAudit();
  },

  renameActivePlan: (name) => {
    set({ planName: name || "Untitled plan" });
    persist(get());
  },

  createTargetWorkspaces: () => {
    const current = get();
    const sharedCompleted = current.completed;
    const startTerm = current.constraints.start_term || "Fall 2026";
    const targets = ["Spring 2028", "Fall 2028", "Spring 2029"];
    const plans: SavedPlan[] = targets.map((target, index) => ({
      id: `target-${target.replace(/\s/g, "-").toLowerCase()}-${Date.now()}-${index}`,
      name: `${target} target`, majors: [...COMBINATION_MAJORS], minors: [...COMBINATION_MINORS],
      completed: sharedCompleted, constraints: { ...current.constraints, start_term: startTerm, target_term: target },
      semesters: [], step: "wizard",
    }));
    const first = plans[0];
    set({
      activePlanId: first.id, planName: first.name, plans, majors: first.majors, minors: first.minors,
      completed: first.completed, constraints: first.constraints, semesters: [], audit: null, step: "wizard",
      availableTracks: [], pickedTracks: [],
      toast: "Created three local target workspaces. Add your completed credit once, then generate each plan.",
    });
    persist(get());
  },

  setConstraint: (k, v) => { set({ constraints: { ...get().constraints, [k]: v } }); persist(get()); },

  addCompleted: (c) => {
    if (get().completed.some((x) => x.code === c.code)) return;
    set({ completed: [...get().completed, { ...c, uid: uid() }] });
    persist(get());
  },
  removeCompleted: (u) => { set({ completed: get().completed.filter((c) => c.uid !== u) }); persist(get()); },

  generate: async () => {
    const { majors, minors, completed, constraints } = get();
    if (!majors.length && !minors.length) { set({ toast: "Pick at least one major or minor." }); return; }
    set({ busy: true });
    try {
      const res = await api.scaffold([...majors, ...minors], completed, constraints);
      set({
        semesters: withUids(res.semesters), step: "board", busy: false, targetStatus: res.target || null,
        toast: res.target && !res.target.on_track ? `Target check: draft ends ${res.target.last_term}, after ${res.target.term}.` : (res.notes?.[0] || "Plan generated"),
      });
      persist(get());
      get().runAudit();
      // Warm the catalog so prereq/coreq data exists, then auto-arrange anything the
      // scaffolder placed out of prerequisite order (so the user doesn't have to click "Fix").
      const codes = res.semesters.flatMap((s) => s.courses.map((c) => c.code).filter(Boolean) as string[]);
      try { await api.ensureBatch(codes); } catch { /* offline catalog is fine */ }
      await get().autoFix();
    } catch (e) {
      set({ busy: false, toast: "Could not generate a plan." });
    }
  },

  editPlan: () => { set({ step: "wizard" }); persist(get()); },

  runAudit: () => {
    window.clearTimeout(auditTimer);
    auditTimer = window.setTimeout(async () => {
      const { majors, minors, completed, semesters } = get();
      try {
        const audit = await api.audit([...majors, ...minors], completed, semesters);
        set({ audit });
      } catch { /* ignore */ }
    }, 180);
  },

  moveCourse: (u, toTerm, toIndex) => {
    const semesters = get().semesters.map((s) => ({ ...s, courses: [...s.courses] }));
    let moving: Course | undefined;
    let sourceTerm = "";
    let sourceIndex = -1;
    for (const s of semesters) {
      const i = s.courses.findIndex((c) => c.uid === u);
      if (i >= 0) { moving = s.courses.splice(i, 1)[0]; sourceTerm = s.term; sourceIndex = i; break; }
    }
    if (!moving) return;
    const target = semesters.find((s) => s.term === toTerm);
    if (!target) return;
    const adjustedIndex = sourceTerm === toTerm && sourceIndex < toIndex ? toIndex - 1 : toIndex;
    target.courses.splice(Math.max(0, Math.min(adjustedIndex, target.courses.length)), 0, moving);
    set({ semesters });
    persist(get());
    get().runAudit();
  },

  removeCourse: (u) => {
    const semesters = get().semesters.map((s) => ({ ...s, courses: s.courses.filter((c) => c.uid !== u || c.locked) }));
    set({ semesters });
    persist(get());
    get().runAudit();
  },

  fillSlot: (u, code, title, credits) => {
    const semesters = get().semesters.map((s) => ({
      ...s,
      courses: s.courses.map((c) =>
        c.uid === u ? { ...c, code, title, credits, slot: false } : c
      ),
    }));
    set({ semesters, toast: `Filled ${code}` });
    persist(get());
    get().runAudit();
    api.ensureBatch([code]).then(() => get().runAudit()).catch(() => {});
  },

  pickAlternative: (u, code) => {
    const semesters = get().semesters.map((s) => ({
      ...s,
      courses: s.courses.map((c) => (c.uid === u ? { ...c, code } : c)),
    }));
    set({ semesters });
    persist(get());
    get().runAudit();
  },

  // Replace a concentration "pick a track" chooser with the chosen track's courses +
  // constrained selective slots, distributed across the term it sat in and later ones.
  pickTrack: (u, trackId) => {
    const { constraints } = get();
    const max = constraints.max_credits || 16;
    const semesters = get().semesters.map((s) => ({ ...s, courses: [...s.courses] }));
    const credits = (sem: Semester) => sem.courses.reduce((a, c) => a + (c.credits || 0), 0);
    let trackName = "";
    let newCodes: string[] = [];
    let originalTracks: import("./types").TrackOption[] | undefined;

    for (let si = 0; si < semesters.length; si++) {
      const i = semesters[si].courses.findIndex((c) => c.uid === u);
      if (i < 0) continue;
      const slot = semesters[si].courses[i];
      const track = slot.tracks?.find((t) => t.id === trackId);
      if (!track) return;
      originalTracks = slot.tracks;
      trackName = track.name;
      semesters[si].courses.splice(i, 1); // drop the chooser
      const cards: Course[] = track.items.map((it) => ({ ...it, uid: uid(), satisfies: it.satisfies ?? slot.satisfies }));
      newCodes = cards.map((c) => c.code).filter(Boolean) as string[];
      for (const card of cards) {
        let placed = false;
        for (let j = si; j < semesters.length; j++) {
          if (credits(semesters[j]) + (card.credits || 0) <= termCap(semesters[j].term, max)) { semesters[j].courses.push(card); placed = true; break; }
        }
        if (!placed) {
          const last = semesters[semesters.length - 1].term;
          semesters.push({ term: nextTermAfter(last, constraints.use_summers), courses: [card] });
        }
      }
      break;
    }
    // Retain the chooser's original list for optional second-track planning after the
    // selected concentration card itself has been removed.
    set({ semesters, availableTracks: originalTracks || get().availableTracks, pickedTracks: [trackId], toast: trackName ? `Concentration: ${trackName}` : null });
    persist(get());
    get().runAudit();
    // warm prereq data for the new courses, then tidy prerequisite order automatically
    (async () => {
      if (newCodes.length) { try { await api.ensureBatch(newCodes); } catch { /* ok */ } }
      await get().autoFix();
    })();
  },

  // A second CS track is course-planning context, not an additional degree requirement. It
  // preserves shared courses, adds only the missing track cards, and leaves the official
  // one-concentration degree audit honest.
  addExtraTrack: (trackId) => {
    const { availableTracks, pickedTracks, constraints } = get();
    if (pickedTracks.includes(trackId)) return;
    const track = availableTracks.find((t) => t.id === trackId);
    if (!track) return;
    const semesters = get().semesters.map((s) => ({ ...s, courses: [...s.courses] }));
    const existing = new Set(semesters.flatMap((s) => s.courses.map((c) => c.code).filter(Boolean)));
    const cards = track.items
      .filter((item) => !item.code || !existing.has(item.code))
      .map((item) => ({ ...item, uid: uid() }));
    const credits = (sem: Semester) => sem.courses.reduce((a, c) => a + (c.credits || 0), 0);
    for (const card of cards) {
      let placed = false;
      for (const sem of semesters) {
        if (credits(sem) + (card.credits || 0) <= termCap(sem.term, constraints.max_credits || 16)) {
          sem.courses.push(card); placed = true; break;
        }
      }
      if (!placed) {
        const lastTerm = semesters.length ? semesters[semesters.length - 1].term : constraints.start_term;
        semesters.push({ term: nextTermAfter(lastTerm, constraints.use_summers), courses: [card] });
      }
    }
    set({ semesters, pickedTracks: [...pickedTracks, trackId], toast: `Added ${track.name} as an extra CS focus` });
    persist(get());
    get().runAudit();
    const codes = cards.map((c) => c.code).filter(Boolean) as string[];
    (async () => {
      if (codes.length) { try { await api.ensureBatch(codes); } catch { /* cached/offline is fine */ } }
      await get().autoFix();
    })();
  },

  fixCourse: (u) => {
    const { semesters, audit, constraints } = get();
    const course = semesters.flatMap((s) => s.courses).find((c) => c.uid === u);
    if (!course?.code || !audit) return;
    const max = constraints.max_credits || 16;
    const prereqs = audit.edges.filter((e) => e.to === course.code && e.type === "prereq").map((e) => e.from);
    let floor = 0;
    for (const p of prereqs) {
      const idx = semesters.findIndex((s) => s.courses.some((c) => c.code === p));
      if (idx >= 0) floor = Math.max(floor, idx + 1);
    }
    // earliest term at/after the prereq floor that still has room for this course
    const load = (s: Semester) => s.courses.reduce((a, c) => a + (c.credits || 0), 0);
    let targetIdx = -1;
    for (let i = floor; i < semesters.length; i++) {
      if (load(semesters[i]) + (course.credits || 0) <= termCap(semesters[i].term, max)) { targetIdx = i; break; }
    }
    if (targetIdx < 0) { get().addSemester(); targetIdx = get().semesters.length - 1; }
    const targetTerm = get().semesters[targetIdx].term;
    get().moveCourse(u, targetTerm, 99);
    set({ toast: `Moved ${course.code} to ${targetTerm}` });
  },

  // Re-pack the whole plan so it respects BOTH prerequisites and per-term credit caps (16,
  // or 9 in summer), keeping required courses early and electives/slots in the leftover room.
  // Replaces the old "nudge violators later" pass, which ignored capacity and produced
  // 23-credit terms next to half-empty ones. Used after generation and by the ⚖ button.
  autoFix: async () => {
    const { majors, minors, completed, constraints } = get();
    const max = constraints.max_credits || 16;
    const useSummers = constraints.use_summers;
    let audit: Audit;
    try { audit = await api.audit([...majors, ...minors], completed, get().semesters); }
    catch { return; }
    set({ audit });

    // If the plan already respects caps and prerequisites, leave it as-is — repacking a clean
    // official sample plan would only churn Purdue's curated ordering.
    const overCap = get().semesters.some((s) => s.courses.reduce((a, c) => a + (c.credits || 0), 0) > termCap(s.term, max) + 0.01);
    const prereqBad = audit.edges.some((e) => e.type === "prereq" && !e.satisfied)
      || audit.prerequisites.checks.some((c) => !c.ok);
    if (!overCap && !prereqBad) { get().runAudit(); return; }

    // prereq adjacency among courses actually present in the plan
    const prereqOf = new Map<string, string[]>();
    for (const e of audit.edges) {
      if (e.type !== "prereq") continue;
      const arr = prereqOf.get(e.to) ?? [];
      arr.push(e.from);
      prereqOf.set(e.to, arr);
    }
    const cards = get().semesters.flatMap((s) => s.courses);
    const codePresent = new Set(cards.map((c) => c.code).filter(Boolean) as string[]);

    // depth = longest in-plan prerequisite chain ending at this course (topological rank)
    const depthMemo = new Map<string, number>();
    const depthOf = (code: string, seen: Set<string> = new Set()): number => {
      if (depthMemo.has(code)) return depthMemo.get(code)!;
      if (seen.has(code)) return 0;
      seen.add(code);
      let d = 0;
      for (const p of prereqOf.get(code) ?? []) if (codePresent.has(p)) d = Math.max(d, depthOf(p, seen) + 1);
      depthMemo.set(code, d);
      return d;
    };

    // Place the prerequisite spine first (courses, by depth), then fill leftover room with
    // slots/electives — so required courses never get crowded into overflowing terms.
    const order = [...cards].sort((a, b) => {
      const sa = a.slot ? 1 : 0, sb = b.slot ? 1 : 0;
      if (sa !== sb) return sa - sb;
      const da = a.code ? depthOf(a.code) : 0, db = b.code ? depthOf(b.code) : 0;
      if (da !== db) return da - db;
      return (b.credits || 0) - (a.credits || 0);
    });

    const terms: Semester[] = get().semesters.map((s) => ({ term: s.term, courses: [] }));
    const load = (i: number) => terms[i].courses.reduce((s, c) => s + (c.credits || 0), 0);
    const placedAt = new Map<string, number>();
    for (const card of order) {
      let floor = 0;
      if (card.code) for (const p of prereqOf.get(card.code) ?? []) {
        const pi = placedAt.get(p);
        if (pi != null) floor = Math.max(floor, pi + 1);
      }
      let dest = -1;
      for (let i = Math.max(0, floor); i < terms.length; i++) {
        if (load(i) + (card.credits || 0) <= termCap(terms[i].term, max)) { dest = i; break; }
      }
      while (dest < 0) {
        terms.push({ term: nextTermAfter(terms[terms.length - 1].term, useSummers), courses: [] });
        const i = terms.length - 1;
        // a fresh empty term always accepts the card (even an oversized one that fits nowhere),
        // which also guarantees this loop terminates
        if (i >= floor && (terms[i].courses.length === 0 || load(i) + (card.credits || 0) <= termCap(terms[i].term, max))) dest = i;
      }
      terms[dest].courses.push(card);
      if (card.code && !placedAt.has(card.code)) placedAt.set(card.code, dest);
    }
    while (terms.length > 1 && !terms[terms.length - 1].courses.length) terms.pop();

    set({ semesters: terms, toast: "Auto-arranged to satisfy prerequisites & credit caps" });
    persist(get());
    get().runAudit();
  },

  // Manually drop a course into a term after the plan exists. It is audited like any other
  // card, so a missing prerequisite shows the same ⚠ flag + Fix button (feedback request).
  addCourseToTerm: (term, c) => {
    const semesters = get().semesters.map((s) =>
      s.term === term ? { ...s, courses: [...s.courses, { ...c, uid: uid() }] } : s
    );
    set({ semesters, toast: `Added ${c.code} to ${term}` });
    persist(get());
    if (c.code) api.ensureBatch([c.code]).then(() => get().runAudit()).catch(() => {});
    get().runAudit();
  },

  addSemester: () => {
    const { semesters, constraints } = get();
    const last = semesters[semesters.length - 1]?.term || constraints.start_term;
    set({ semesters: [...semesters, { term: nextTermAfter(last, constraints.use_summers), courses: [] }] });
    persist(get());
  },
  removeSemester: (term) => {
    set({ semesters: get().semesters.filter((s) => s.term !== term || s.courses.length) });
    persist(get());
    get().runAudit();
  },

  setHover: (u) => set({ hoverUid: u }),
  toggleStrict: () => { set({ strict: !get().strict }); persist(get()); },
  toggleTheme: () => {
    const theme = get().theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", theme);
    set({ theme }); persist(get());
  },
  setToast: (m) => set({ toast: m }),
}));
