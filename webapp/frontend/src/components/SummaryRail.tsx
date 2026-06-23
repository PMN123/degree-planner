import { useStore } from "../store";

const RING = 2 * Math.PI * 52;

export function SummaryRail() {
  const audit = useStore((s) => s.audit);
  if (!audit) return <aside className="rail"><div className="card"><p className="hint">Auditing…</p></div></aside>;

  const c = audit.credits;
  const pct = Math.min(c.total / (c.degree_target || 120), 1);
  const failed = audit.prerequisites.checks.filter((x) => !x.ok);
  const unfilled = useStore.getState().semesters.flatMap((s) => s.courses).filter((x) => x.slot).length;

  return (
    <aside className="rail">
      <div className="card summary-card">
        <div className={`feasibility ${audit.feasible ? "ok" : "warn"}`}>
          <span className="status-dot" />
          <span>{audit.feasible ? "On track to graduate" : "Gaps remain — see below"}</span>
        </div>
        <div className="ring-wrap">
          <svg viewBox="0 0 120 120" className="credit-ring">
            <circle className="ring-track" cx="60" cy="60" r="52" />
            <circle className="ring-value" cx="60" cy="60" r="52"
              style={{ strokeDasharray: RING, strokeDashoffset: RING * (1 - pct) }} />
          </svg>
          <div className="ring-center"><strong>{c.total}</strong><span>/ {c.degree_target} cr</span></div>
        </div>
        <div className="credit-legend">
          <span>done <b>{c.completed}</b></span>
          <span>planned <b>{c.planned}</b></span>
          {unfilled > 0 && <span>open slots <b>{unfilled}</b></span>}
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h2>Degree coverage</h2></div>
        {audit.programs.length === 0 && <p className="empty-note">No requirement data for the selected programs.</p>}
        {audit.programs.map((p) => (
          <div key={p.id} className="coverage-row">
            <div className="cov-top">
              <span className="cov-name">{p.name} {p.degree && <span className="deg">{p.degree}</span>}</span>
              <span className="cov-pct">{p.progress.percent}%</span>
            </div>
            <div className="cov-bar"><span style={{ width: `${p.progress.percent}%` }} /></div>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-head"><h2>Issues</h2><span className="hint">{failed.length || "0"} prereq</span></div>
        {failed.length === 0 && unfilled === 0 && <div className="issue ok-issue">✓ No prerequisite or ordering problems.</div>}
        {failed.map((f, i) => (
          <div key={i} className="issue bad">
            <span className="i-dot">!</span>
            <div className="i-body">
              <div className="i-code">{f.code} · {f.term}</div>
              <div className="i-msg">
                {f.missing_best_alternative?.length ? `Needs ${f.missing_best_alternative.join(", ")} first` :
                  f.status === "missing_catalog" ? "Not in catalog — search to fetch prereqs" :
                  (f.warnings || []).join("; ") || "Prerequisite not satisfied in an earlier term."}
              </div>
            </div>
          </div>
        ))}
        {unfilled > 0 && <div className="issue warn-issue">▦ {unfilled} elective/selective slot(s) still to fill.</div>}
      </div>
    </aside>
  );
}
