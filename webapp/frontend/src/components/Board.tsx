import { DndContext, DragOverlay, PointerSensor, useSensor, useSensors, type DragEndEvent, type DragStartEvent } from "@dnd-kit/core";
import { useRef, useState } from "react";
import { useStore } from "../store";
import { TermColumn } from "./TermColumn";
import { DependencyLines } from "./DependencyLines";
import { SummaryRail } from "./SummaryRail";
import { TrackAddOn } from "./TrackAddOn";

export function Board() {
  const semesters = useStore((s) => s.semesters);
  const moveCourse = useStore((s) => s.moveCourse);
  const addSemester = useStore((s) => s.addSemester);
  const audit = useStore((s) => s.audit);
  const strict = useStore((s) => s.strict);
  const targetTerm = useStore((s) => s.constraints.target_term);
  const autoFix = useStore((s) => s.autoFix);
  const setToast = useStore((s) => s.setToast);
  const boardRef = useRef<HTMLDivElement>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(true);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  const activeCourse = semesters.flatMap((s) => s.courses).find((c) => c.uid === activeId);
  const populatedTerms = semesters.filter((s) => s.courses.length);
  const latestTerm = populatedTerms.length ? populatedTerms[populatedTerms.length - 1].term : undefined;
  const termKey = (term: string) => {
    const m = term.match(/(Spring|Summer|Fall)\s+(\d{4})/i);
    return m ? (+m[2] * 3) + ({ spring: 0, summer: 1, fall: 2 } as Record<string, number>)[m[1].toLowerCase()] : 0;
  };
  const targetOnTrack = !targetTerm || !latestTerm || termKey(latestTerm) <= termKey(targetTerm);

  const wouldViolate = (uid: string, targetTerm: string): boolean => {
    if (!audit) return false;
    const course = semesters.flatMap((s) => s.courses).find((c) => c.uid === uid);
    if (!course?.code) return false;
    const targetIdx = semesters.findIndex((s) => s.term === targetTerm);
    const prereqs = audit.edges.filter((e) => e.to === course.code && e.type === "prereq").map((e) => e.from);
    return prereqs.some((p) => {
      const idx = semesters.findIndex((s) => s.courses.some((c) => c.code === p));
      return idx >= 0 && idx >= targetIdx;
    });
  };

  const onDragStart = (e: DragStartEvent) => setActiveId(String(e.active.id));
  const onDragEnd = (e: DragEndEvent) => {
    setActiveId(null);
    const overId = e.over?.id ? String(e.over.id) : null;
    const uid = String(e.active.id);
    if (!overId) return;
    const overCourse = semesters.flatMap((s) => s.courses).find((c) => c.uid === overId);
    const overTerm = overCourse
      ? semesters.find((s) => s.courses.some((c) => c.uid === overId))?.term
      : overId.startsWith("term:") ? overId.slice(5) : null;
    if (!overTerm) return;
    const from = semesters.find((s) => s.courses.some((c) => c.uid === uid))?.term;
    if (strict && wouldViolate(uid, overTerm)) {
      setToast("Strict mode: that move puts a course before its prerequisite — snapped back.");
      return;
    }
    const target = semesters.find((s) => s.term === overTerm);
    const toIndex = overCourse && target ? target.courses.findIndex((c) => c.uid === overId) : (target?.courses.length || 0);
    moveCourse(uid, overTerm, toIndex);
  };

  return (
    <DndContext sensors={sensors} onDragStart={onDragStart} onDragEnd={onDragEnd}>
      <main className="board-layout">
        <div className="board-main">
          <div className="board-controls">
            {targetTerm && <div className={`target-pill${targetOnTrack ? " ok" : " late"}`}>
              Target {targetTerm} · {targetOnTrack ? "within horizon" : `currently ends ${latestTerm}`}
            </div>}
            <TrackAddOn />
            <label className="line-toggle">
              <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show all dependency lines
            </label>
            <div className="legend">
              <span className="lg prereq">— prereq</span>
              <span className="lg coreq">— coreq</span>
              <span className="lg violated">— violated</span>
            </div>
            <button className="ghost-btn" onClick={() => autoFix()} title="Move any course that sits before its prerequisites to the earliest term that works">⚖ Auto-arrange</button>
            <button className="ghost-btn" onClick={addSemester}>+ Add term</button>
          </div>
          <div className="board" ref={boardRef}>
            <DependencyLines containerRef={boardRef} showAll={showAll} />
            <div className="term-row">
              {semesters.map((sem) => <TermColumn key={sem.term} sem={sem} />)}
            </div>
          </div>
        </div>
        <SummaryRail />
      </main>
      <DragOverlay dropAnimation={null}>
        {activeCourse ? (
          <div className="course-card dragging">
            <div className="cc-main">
              <span className="cc-code">{activeCourse.code || activeCourse.label}</span>
              <span className="cc-cr">{activeCourse.credits}</span>
            </div>
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}
