import { useStore } from "../store";
import type { Course } from "../types";
import { CourseSearch } from "./CourseSearch";

export function SlotChooser({ course, onClose }: { course: Course; onClose: () => void }) {
  const fillSlot = useStore((s) => s.fillSlot);
  const hasOptions = (course.options?.length || 0) > 0;
  return (
    <div className="slot-pop" onPointerDown={(e) => e.stopPropagation()}>
      <div className="slot-pop-head">
        <strong>{course.label || "Pick a course"}</strong>
        <button className="cc-btn x" onClick={onClose}>✕</button>
      </div>
      <p className="hint">{hasOptions ? `Approved options for this requirement (${course.options!.length}):` : "Search any Purdue course:"}</p>
      {course.note && <p className="slot-note">{course.note}</p>}
      <CourseSearch
        placeholder={hasOptions ? "Filter options…" : "Search courses…"}
        filterCodes={hasOptions ? course.options : undefined}
        onPick={(c) => { fillSlot(course.uid, c.code, c.title || "", c.credits ?? course.credits); onClose(); }}
      />
    </div>
  );
}
