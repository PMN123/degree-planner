import { useState } from "react";
import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { useStore } from "../store";
import type { Semester } from "../types";
import { CourseCard } from "./CourseCard";
import { CourseSearch } from "./CourseSearch";

function loadClass(credits: number, isSummer: boolean): string {
  if (isSummer) return credits > 9 ? "heavy" : "ok"; // summer is short — 9-credit ceiling
  if (credits > 19) return "heavy";
  if (credits > 0 && credits < 12) return "light";
  return "ok";
}

export function TermColumn({ sem }: { sem: Semester }) {
  const { setNodeRef, isOver } = useDroppable({ id: `term:${sem.term}` });
  const removeSemester = useStore((s) => s.removeSemester);
  const addCourseToTerm = useStore((s) => s.addCourseToTerm);
  const max = useStore((s) => s.constraints.max_credits);
  const [adding, setAdding] = useState(false);
  const isSummer = /summer/i.test(sem.term);
  const credits = sem.courses.reduce((a, c) => a + (Number(c.credits) || 0), 0);
  const cls = loadClass(credits, isSummer);
  const cap = isSummer ? Math.min(max, 9) : max;
  const pct = Math.min(100, (credits / Math.max(cap + 3, isSummer ? 9 : 18)) * 100);

  return (
    <div ref={setNodeRef} className={`term-col${isOver ? " over" : ""}`}>
      <div className="term-head">
        <span className="term-name">{sem.term}{isSummer && <span className="summer-tag" title="Summer terms cap at ~9 credits"> · ≤9</span>}</span>
        <span className={`term-cr ${cls}`}>{credits} cr</span>
        {!sem.courses.length && (
          <button className="cc-btn x" title="Remove empty term" onClick={() => removeSemester(sem.term)}>✕</button>
        )}
      </div>
      <div className={`load-meter ${cls}`}><span style={{ width: `${pct}%` }} /></div>
      <div className="term-body">
        <SortableContext items={sem.courses.map((c) => c.uid)} strategy={verticalListSortingStrategy}>
          {sem.courses.map((c) => (
            <CourseCard key={c.uid} course={c} term={sem.term} />
          ))}
        </SortableContext>
        {!sem.courses.length && <div className="term-empty">drop here</div>}
        {adding ? (
          <div className="term-add" onPointerDown={(e) => e.stopPropagation()}>
            <CourseSearch
              placeholder="Add a course to this term…"
              allowFreeform
              onPick={(c) => { addCourseToTerm(sem.term, { code: c.code, title: c.title, credits: c.credits, satisfies: [] }); setAdding(false); }}
            />
            <button className="cc-btn x" onClick={() => setAdding(false)}>✕</button>
          </div>
        ) : (
          <button className="term-add-btn" onClick={() => setAdding(true)}>+ add course</button>
        )}
      </div>
    </div>
  );
}
