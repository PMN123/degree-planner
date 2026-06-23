import { useEffect } from "react";
import { useStore } from "./store";
import { Wizard } from "./components/Wizard";
import { Board } from "./components/Board";
import { Toast } from "./components/Toast";

export function App() {
  const ready = useStore((s) => s.ready);
  const step = useStore((s) => s.step);
  const init = useStore((s) => s.init);
  const theme = useStore((s) => s.theme);
  const toggleTheme = useStore((s) => s.toggleTheme);

  useEffect(() => { init(); }, [init]);
  if (!ready) return <div className="boot">Loading…</div>;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">P</div>
          <div className="brand-text">
            <h1>BoilerPlanner</h1>
            <p className="tagline">Auto-scaffolded, drag-and-drop plan for <span className="accent">any</span> Purdue major + minor</p>
          </div>
        </div>
        <div className="topbar-actions">
          {step === "board" && <BoardActions />}
          <button className="icon-btn" onClick={toggleTheme} aria-label="Toggle theme">{theme === "dark" ? "◑" : "◐"}</button>
        </div>
      </header>
      {step === "wizard" ? <Wizard /> : <Board />}
      <Toast />
    </div>
  );
}

function BoardActions() {
  const editPlan = useStore((s) => s.editPlan);
  const strict = useStore((s) => s.strict);
  const toggleStrict = useStore((s) => s.toggleStrict);
  return (
    <>
      <button className="icon-btn back-btn" onClick={editPlan} aria-label="Back to degree editing" title="Back to degree editing">←</button>
      <label className="strict-toggle" title="Snap illegal drops back instead of just flagging them">
        <input type="checkbox" checked={strict} onChange={toggleStrict} /> Strict
      </label>
      <button className="ghost-btn" onClick={editPlan}>Edit degrees</button>
    </>
  );
}
