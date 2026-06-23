import { useDroppable } from "@dnd-kit/core";
import { useStore } from "../store";
import type { Semester } from "../types";
import { CourseCard } from "./CourseCard";

function loadClass(credits: number): string {
  if (credits > 19) return "heavy";
  if (credits > 0 && credits < 12) return "light";
  return "ok";
}

export function TermColumn({ sem }: { sem: Semester }) {
  const { setNodeRef, isOver } = useDroppable({ id: sem.term });
  const removeSemester = useStore((s) => s.removeSemester);
  const max = useStore((s) => s.constraints.max_credits);
  const credits = sem.courses.reduce((a, c) => a + (Number(c.credits) || 0), 0);
  const cls = loadClass(credits);
  const pct = Math.min(100, (credits / Math.max(max + 3, 18)) * 100);

  return (
    <div ref={setNodeRef} className={`term-col${isOver ? " over" : ""}`}>
      <div className="term-head">
        <span className="term-name">{sem.term}</span>
        <span className={`term-cr ${cls}`}>{credits} cr</span>
        {!sem.courses.length && (
          <button className="cc-btn x" title="Remove empty term" onClick={() => removeSemester(sem.term)}>✕</button>
        )}
      </div>
      <div className={`load-meter ${cls}`}><span style={{ width: `${pct}%` }} /></div>
      <div className="term-body">
        {sem.courses.map((c) => (
          <CourseCard key={c.uid} course={c} term={sem.term} />
        ))}
        {!sem.courses.length && <div className="term-empty">drop here</div>}
      </div>
    </div>
  );
}
