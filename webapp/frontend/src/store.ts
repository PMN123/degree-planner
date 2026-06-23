import { create } from "zustand";
import { api } from "./api";
import type { Audit, Constraints, Course, Semester } from "./types";

let _uid = 0;
const uid = () => `c${_uid++}`;
const withUids = (sems: Semester[]): Semester[] =>
  sems.map((s) => ({ term: s.term, courses: s.courses.map((c) => ({ ...c, uid: uid() })) }));

const STORAGE_KEY = "boiler-degree-planner.v2";

interface State {
  ready: boolean;
  step: "wizard" | "board";
  theme: "dark" | "light";
  strict: boolean;
  majors: string[];
  minors: string[];
  completed: Course[];
  constraints: Constraints;
  semesters: Semester[];
  audit: Audit | null;
  busy: boolean;
  hoverUid: string | null;
  toast: string | null;

  init: () => Promise<void>;
  toggleProgram: (kind: "majors" | "minors", id: string) => void;
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
  fixCourse: (uid: string) => void;
  autoFix: () => Promise<void>;
  deferLeaves: () => void;
  addCourseToTerm: (term: string, c: Omit<Course, "uid">) => void;
  addSemester: () => void;
  removeSemester: (term: string) => void;
  setHover: (uid: string | null) => void;
  toggleStrict: () => void;
  toggleTheme: () => void;
  setToast: (m: string | null) => void;
}

function persist(s: State) {
  const { majors, minors, completed, constraints, semesters, step, theme, strict } = s;
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ majors, minors, completed, constraints, semesters, step, theme, strict }));
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
const termCredits = (s: Semester) => s.courses.reduce((a, c) => a + (c.credits || 0), 0);

let auditTimer: number | undefined;

export const useStore = create<State>((set, get) => ({
  ready: false,
  step: "wizard",
  theme: "dark",
  strict: false,
  majors: [],
  minors: [],
  completed: [],
  constraints: { start_term: "Fall 2026", target_term: "", max_credits: 16, use_summers: false },
  semesters: [],
  audit: null,
  busy: false,
  hoverUid: null,
  toast: null,

  init: async () => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const saved = JSON.parse(raw);
        set({ ...saved });
        if (saved.semesters?.length) {
          set({ semesters: withUids(saved.semesters) });
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
      set({ semesters: withUids(res.semesters), step: "board", busy: false, toast: res.notes?.[0] || "Plan generated" });
      persist(get());
      get().runAudit();
      // Warm the catalog so prereq/coreq data exists, then auto-arrange anything the
      // scaffolder placed out of prerequisite order (so the user doesn't have to click "Fix").
      const codes = res.semesters.flatMap((s) => s.courses.map((c) => c.code).filter(Boolean) as string[]);
      try { await api.ensureBatch(codes); } catch { /* offline catalog is fine */ }
      await get().autoFix();
      // Push electives / non-prerequisite "leaf" courses toward the end so the prerequisite
      // spine (math, core sequences) takes the early terms — feedback: PHYS 272 was landing
      // far too early because the packer filled the first open slot it found.
      get().deferLeaves();
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
    for (const s of semesters) {
      const i = s.courses.findIndex((c) => c.uid === u);
      if (i >= 0) { moving = s.courses.splice(i, 1)[0]; break; }
    }
    if (!moving) return;
    const target = semesters.find((s) => s.term === toTerm);
    if (!target) return;
    target.courses.splice(Math.min(toIndex, target.courses.length), 0, moving);
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

    for (let si = 0; si < semesters.length; si++) {
      const i = semesters[si].courses.findIndex((c) => c.uid === u);
      if (i < 0) continue;
      const slot = semesters[si].courses[i];
      const track = slot.tracks?.find((t) => t.id === trackId);
      if (!track) return;
      trackName = track.name;
      semesters[si].courses.splice(i, 1); // drop the chooser
      const cards: Course[] = track.items.map((it) => ({ ...it, uid: uid(), satisfies: it.satisfies ?? slot.satisfies }));
      newCodes = cards.map((c) => c.code).filter(Boolean) as string[];
      for (const card of cards) {
        let placed = false;
        for (let j = si; j < semesters.length; j++) {
          if (credits(semesters[j]) + (card.credits || 0) <= max) { semesters[j].courses.push(card); placed = true; break; }
        }
        if (!placed) {
          const last = semesters[semesters.length - 1].term;
          semesters.push({ term: nextTermAfter(last, constraints.use_summers), courses: [card] });
        }
      }
      break;
    }
    set({ semesters, toast: trackName ? `Concentration: ${trackName}` : null });
    persist(get());
    get().runAudit();
    // warm prereq data for the new courses, then tidy prerequisite order automatically
    (async () => {
      if (newCodes.length) { try { await api.ensureBatch(newCodes); } catch { /* ok */ } }
      await get().autoFix();
    })();
  },

  fixCourse: (u) => {
    const { semesters, audit } = get();
    const course = semesters.flatMap((s) => s.courses).find((c) => c.uid === u);
    if (!course?.code || !audit) return;
    const prereqs = audit.edges.filter((e) => e.to === course.code && e.type === "prereq").map((e) => e.from);
    const termIndex = (term: string) => semesters.findIndex((s) => s.term === term);
    let earliest = 0;
    for (const p of prereqs) {
      const idx = semesters.findIndex((s) => s.courses.some((c) => c.code === p));
      if (idx >= 0) earliest = Math.max(earliest, idx + 1);
    }
    const curIdx = semesters.findIndex((s) => s.courses.some((c) => c.uid === u));
    if (earliest >= semesters.length) { get().addSemester(); }
    const targetTerm = get().semesters[Math.min(earliest, get().semesters.length - 1)].term;
    if (earliest !== curIdx) get().moveCourse(u, targetTerm, 99);
    set({ toast: `Moved ${course.code} to ${targetTerm}` });
  },

  // What clicking "Fix" on every flagged course would do, run automatically until stable:
  // re-audit, push each prereq-violating course to the earliest term its prerequisites allow,
  // repeat. Bounded passes guard against prereq-data cycles.
  autoFix: async () => {
    const MAX_PASSES = 6;
    let totalMoved = 0;
    for (let pass = 0; pass < MAX_PASSES; pass++) {
      const { majors, minors, completed } = get();
      const semesters = get().semesters;
      let audit: Audit;
      try { audit = await api.audit([...majors, ...minors], completed, semesters); }
      catch { break; }
      set({ audit });

      const moves: { uid: string; toIdx: number }[] = [];
      for (let si = 0; si < semesters.length; si++) {
        for (const c of semesters[si].courses) {
          if (!c.code) continue;
          const failed = audit.prerequisites.checks.some((ck) => ck.code === c.code && ck.term === semesters[si].term && !ck.ok);
          const violated = audit.edges.some((e) => e.to === c.code && !e.satisfied);
          if (!failed && !violated) continue;
          const prereqs = audit.edges.filter((e) => e.to === c.code && e.type === "prereq").map((e) => e.from);
          let earliest = 0;
          for (const p of prereqs) {
            const idx = semesters.findIndex((s) => s.courses.some((x) => x.code === p));
            if (idx >= 0) earliest = Math.max(earliest, idx + 1);
          }
          if (earliest > si) moves.push({ uid: c.uid, toIdx: earliest });
        }
      }
      if (!moves.length) break;

      const next = get().semesters.map((s) => ({ ...s, courses: [...s.courses] }));
      for (const mv of moves) {
        while (mv.toIdx >= next.length) {
          const last = next[next.length - 1].term;
          next.push({ term: nextTermAfter(last, get().constraints.use_summers), courses: [] });
        }
        for (const s of next) {
          const i = s.courses.findIndex((c) => c.uid === mv.uid);
          if (i >= 0) { const [card] = s.courses.splice(i, 1); next[mv.toIdx].courses.push(card); break; }
        }
      }
      set({ semesters: next });
      totalMoved += moves.length;
    }
    persist(get());
    if (totalMoved) set({ toast: `Auto-arranged ${totalMoved} course${totalMoved > 1 ? "s" : ""} to satisfy prerequisites` });
    get().runAudit();
  },

  // Move every "leaf" card (an elective/open slot, or a course nothing else depends on) as
  // late as it can go without breaking its own prerequisites or overflowing a term. Leaves the
  // prerequisite spine untouched. Runs once after generation, not on every edit.
  deferLeaves: () => {
    const { audit, constraints } = get();
    if (!audit) return;
    const max = constraints.max_credits || 16;
    const isPrereqForSomething = (code?: string) =>
      !!code && audit.edges.some((e) => e.from === code && e.type === "prereq");

    const semesters = get().semesters.map((s) => ({ ...s, courses: [...s.courses] }));
    const earliestAllowed = (course: Course): number => {
      // a leaf course must still sit after its own prerequisites
      if (!course.code) return 0;
      const prereqs = audit.edges.filter((e) => e.to === course.code && e.type === "prereq").map((e) => e.from);
      let earliest = 0;
      for (const p of prereqs) {
        const idx = semesters.findIndex((s) => s.courses.some((c) => c.code === p));
        if (idx >= 0) earliest = Math.max(earliest, idx + 1);
      }
      return earliest;
    };

    let moved = 0;
    // walk terms front-to-back; for each leaf, try to slide it to the latest term with room
    for (let si = 0; si < semesters.length; si++) {
      for (const card of [...semesters[si].courses]) {
        const leaf = card.slot || !isPrereqForSomething(card.code);
        if (!leaf || card.locked) continue;
        const lo = Math.max(si + 1, earliestAllowed(card));
        let dest = -1;
        for (let tj = semesters.length - 1; tj >= lo; tj--) {
          if (termCredits(semesters[tj]) + (card.credits || 0) <= termCap(semesters[tj].term, max)) { dest = tj; break; }
        }
        if (dest > si) {
          const i = semesters[si].courses.findIndex((c) => c.uid === card.uid);
          if (i >= 0) { semesters[si].courses.splice(i, 1); semesters[dest].courses.push(card); moved++; }
        }
      }
    }
    if (moved) { set({ semesters }); persist(get()); get().runAudit(); }
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
