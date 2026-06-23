import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import type { ProgramMeta } from "../types";
import { CourseSearch } from "./CourseSearch";

function ProgramPicker({ kind, label, hint }: { kind: "majors" | "minors"; label: string; hint: string }) {
  const selected = useStore((s) => s[kind]);
  const toggle = useStore((s) => s.toggleProgram);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<ProgramMeta[]>([]);
  const ptype = kind === "majors" ? "major" : "minor";

  useEffect(() => {
    let live = true;
    api.programs(ptype, q, 40).then((r) => { if (live) setResults(r); }).catch(() => {});
    return () => { live = false; };
  }, [q, ptype]);

  const selectedMeta = results.filter((p) => selected.includes(p.id));
  const known = new Set(results.map((p) => p.id));

  return (
    <div className="card picker-card">
      <div className="card-head">
        <h2>{label}</h2>
        <span className="hint">{hint}</span>
      </div>
      <input className="course-input wide" placeholder={`Search ${ptype}s…`} value={q} onChange={(e) => setQ(e.target.value)} />
      {selected.length > 0 && (
        <div className="chip-row">
          {selected.map((id) => {
            const m = results.find((p) => p.id === id);
            return (
              <span key={id} className="prog-chip active" onClick={() => toggle(kind, id)}>
                {m?.name || id} ✕
              </span>
            );
          })}
        </div>
      )}
      <div className="prog-list">
        {results.filter((p) => !selected.includes(p.id)).slice(0, 24).map((p) => (
          <button key={p.id} className="prog-row" onClick={() => toggle(kind, p.id)}>
            <span className="prog-name">{p.name}</span>
            <span className="prog-tags">
              {p.degree && <span className="deg">{p.degree}</span>}
              {!p.verified && p.has_requirements && <span className="badge draft" title="Auto-scraped, unverified">draft</span>}
              {p.verified && <span className="badge ok" title="Hand-verified">✓</span>}
              {!p.has_requirements && <span className="badge none" title="Requirements not scraped yet">no data</span>}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function Wizard() {
  const completed = useStore((s) => s.completed);
  const addCompleted = useStore((s) => s.addCompleted);
  const removeCompleted = useStore((s) => s.removeCompleted);
  const constraints = useStore((s) => s.constraints);
  const setConstraint = useStore((s) => s.setConstraint);
  const generate = useStore((s) => s.generate);
  const busy = useStore((s) => s.busy);
  const majors = useStore((s) => s.majors);
  const minors = useStore((s) => s.minors);

  return (
    <main className="wizard">
      <div className="wizard-grid">
        <ProgramPicker kind="majors" label="Majors" hint="Search & pick one or more — overlap is allowed" />
        <ProgramPicker kind="minors" label="Minors" hint="Separate from majors" />
      </div>

      <div className="card">
        <div className="card-head"><h2>Where you are</h2><span className="hint">Start, pace & prior credit</span></div>
        <div className="constraint-row">
          <label className="field">
            <span>Start term</span>
            <input value={constraints.start_term} onChange={(e) => setConstraint("start_term", e.target.value)} placeholder="Fall 2026" />
          </label>
          <label className="field">
            <span>Max credits / term</span>
            <input type="number" min={3} max={24} value={constraints.max_credits} onChange={(e) => setConstraint("max_credits", Number(e.target.value))} />
          </label>
          <label className="field checkbox">
            <input type="checkbox" checked={constraints.use_summers} onChange={(e) => setConstraint("use_summers", e.target.checked)} />
            <span>Use summers</span>
          </label>
        </div>
        <div className="completed-block">
          <span className="hint">Completed / AP / transfer credit</span>
          <div className="chip-row">
            {completed.map((c) => (
              <span key={c.uid} className="course-chip" onClick={() => removeCompleted(c.uid)}>
                {c.code} <b>{c.credits}cr</b> ✕
              </span>
            ))}
          </div>
          <CourseSearch placeholder="Add a completed course (e.g. CS 18000)" onPick={(c) => addCompleted(c)} />
        </div>
      </div>

      <div className="wizard-cta">
        <span className="hint">{majors.length} major(s) · {minors.length} minor(s) selected</span>
        <button className="primary-btn" disabled={busy || (!majors.length && !minors.length)} onClick={generate}>
          {busy ? "Generating…" : "Generate my 4-year plan →"}
        </button>
      </div>
      <p className="disclaimer">Starter data — much of it auto-scraped & <strong>unverified</strong>. Always confirm with myPurduePlan / your advisor before registering.</p>
    </main>
  );
}
