import { useDraggable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { useMemo, useState } from "react";
import { useStore } from "../store";
import type { Course } from "../types";
import { SlotChooser } from "./SlotChooser";

const PALETTE = ["a", "b", "c", "d", "e"];
function colorFor(satisfies: string[] | undefined, programOrder: string[]) {
  if (!satisfies?.length) return "x";
  const idx = programOrder.indexOf(satisfies[0]);
  return PALETTE[(idx < 0 ? 0 : idx) % PALETTE.length];
}

export function CourseCard({ course, term }: { course: Course; term: string }) {
  const audit = useStore((s) => s.audit);
  const remove = useStore((s) => s.removeCourse);
  const fix = useStore((s) => s.fixCourse);
  const pickAlt = useStore((s) => s.pickAlternative);
  const pickTrack = useStore((s) => s.pickTrack);
  const setHover = useStore((s) => s.setHover);
  const majors = useStore((s) => s.majors);
  const minors = useStore((s) => s.minors);
  const programOrder = useMemo(() => [...majors, ...minors], [majors, minors]);
  const [chooser, setChooser] = useState(false);

  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: course.uid });

  const badCheck = audit?.prerequisites.checks.find((c) => c.code === course.code && c.term === term && !c.ok);
  const violatedEdge = audit?.edges.some((e) => e.to === course.code && !e.satisfied);
  const bad = Boolean(badCheck || violatedEdge);

  const style: React.CSSProperties = {
    transform: CSS.Translate.toString(transform),
    opacity: isDragging ? 0.4 : undefined,
  };

  if (course.slot && course.slotKind === "track") {
    return (
      <div
        ref={setNodeRef} style={style} {...listeners} {...attributes}
        className="course-card slot track-slot" data-uid={course.uid}
      >
        <div className="cc-main">
          <span className="cc-icon">◈</span>
          <div className="cc-text">
            <div className="cc-label">{course.label || "Concentration"}</div>
            <div className="cc-sub">pick a track{course.track_credits ? ` · ≈${course.track_credits} cr` : ""}</div>
          </div>
        </div>
        <div className="track-options" onPointerDown={(e) => e.stopPropagation()}>
          {course.tracks?.map((t) => (
            <button key={t.id} className="track-pick" onClick={() => pickTrack(course.uid, t.id)} title={`${t.items.length} requirement(s)`}>
              {t.name} <span className="track-arrow">▸</span>
            </button>
          ))}
        </div>
        <button className="cc-btn x corner" onPointerDown={(e) => e.stopPropagation()} onClick={() => remove(course.uid)} title="Remove">✕</button>
      </div>
    );
  }

  if (course.slot) {
    const constrained = (course.options?.length || 0) > 0;
    return (
      <div
        ref={setNodeRef} style={style} {...listeners} {...attributes}
        className={`course-card slot${constrained ? " constrained" : ""}`} data-uid={course.uid}
      >
        <div className="cc-main">
          <span className="cc-icon">▦</span>
          <div className="cc-text">
            <div className="cc-label">{course.label || "Selective / elective"}</div>
            <div className="cc-sub">{constrained ? `choose 1 of ${course.options!.length} approved` : "choose a course"}</div>
          </div>
          <span className="cc-cr">{course.credits}</span>
        </div>
        {course.note && <div className="cc-note" onPointerDown={(e) => e.stopPropagation()}>{course.note}</div>}
        <div className="cc-actions">
          <button className="cc-btn" onPointerDown={(e) => e.stopPropagation()} onClick={() => setChooser(true)}>Choose</button>
          <button className="cc-btn x" onPointerDown={(e) => e.stopPropagation()} onClick={() => remove(course.uid)}>✕</button>
        </div>
        {chooser && <SlotChooser course={course} onClose={() => setChooser(false)} />}
      </div>
    );
  }

  return (
    <div
      ref={setNodeRef} style={style} {...listeners} {...attributes}
      className={`course-card cat-${colorFor(course.satisfies, programOrder)}${bad ? " bad" : ""}${course.locked ? " locked" : ""}`}
      data-uid={course.uid} data-code={course.code}
      onMouseEnter={() => setHover(course.uid)} onMouseLeave={() => setHover(null)}
    >
      <div className="cc-main">
        <span className="cc-code">{course.code}{course.locked && <span className="lock" title="Required core">🔒</span>}</span>
        <span className="cc-cr">{course.credits}</span>
      </div>
      {course.title && <div className="cc-title">{course.title}</div>}
      {course.alternatives?.length ? (
        <div className="cc-alts" onPointerDown={(e) => e.stopPropagation()}>
          <span className="alt-label">or</span>
          {course.alternatives.map((a) => (
            <button key={a} className="alt-pill" onClick={() => pickAlt(course.uid, a)}>{a}</button>
          ))}
        </div>
      ) : null}
      {bad && (
        <div className="cc-flag" onPointerDown={(e) => e.stopPropagation()}>
          <span className="flag-msg">⚠ {badCheck?.missing_best_alternative?.length ? `needs ${badCheck.missing_best_alternative.join(", ")}` : "prereq not met before this term"}</span>
          <button className="cc-btn fix" onClick={() => fix(course.uid)}>Fix ▸</button>
        </div>
      )}
      <button className="cc-btn x corner" onPointerDown={(e) => e.stopPropagation()} onClick={() => remove(course.uid)}
        disabled={course.locked} title={course.locked ? "Required — can't remove (drag to move)" : "Remove"}>✕</button>
    </div>
  );
}
