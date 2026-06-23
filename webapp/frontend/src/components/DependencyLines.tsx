import { useEffect, useLayoutEffect, useState } from "react";
import { useStore } from "../store";
import type { Edge } from "../types";

interface Path { d: string; cls: string; key: string }
const EMPTY: Edge[] = []; // stable reference so the layout effect doesn't loop when there's no audit yet

export function DependencyLines({ containerRef, showAll }: { containerRef: React.RefObject<HTMLElement>; showAll: boolean }) {
  const edges = useStore((s) => s.audit?.edges) ?? EMPTY;
  const semesters = useStore((s) => s.semesters);
  const hoverUid = useStore((s) => s.hoverUid);
  const [paths, setPaths] = useState<Path[]>([]);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [tick, setTick] = useState(0);

  // hovered card's code (for focus mode)
  const hoverCode = semesters.flatMap((s) => s.courses).find((c) => c.uid === hoverUid)?.code;

  useLayoutEffect(() => {
    const cont = containerRef.current;
    if (!cont) return;
    const crect = cont.getBoundingClientRect();
    const center = (el: Element, side: "l" | "r" | "c") => {
      const r = el.getBoundingClientRect();
      const y = r.top - crect.top + cont.scrollTop + r.height / 2;
      const x = (side === "l" ? r.left : side === "r" ? r.right : r.left + r.width / 2) - crect.left + cont.scrollLeft;
      return { x, y };
    };
    const find = (code: string) => cont.querySelector(`[data-code="${CSS.escape(code)}"]`);
    const out: Path[] = [];
    edges.forEach((e: Edge, i) => {
      const a = find(e.from), b = find(e.to);
      if (!a || !b) return;
      const focus = hoverCode && (e.from === hoverCode || e.to === hoverCode);
      if (!showAll && !focus) return;
      const p1 = center(a, "r"), p2 = center(b, "l");
      const dx = Math.max(40, Math.abs(p2.x - p1.x) * 0.5);
      const d = `M ${p1.x} ${p1.y} C ${p1.x + dx} ${p1.y}, ${p2.x - dx} ${p2.y}, ${p2.x} ${p2.y}`;
      const cls = [
        "edge", e.type, e.satisfied ? "" : "violated",
        hoverCode ? (focus ? "focus" : "dim") : "",
      ].filter(Boolean).join(" ");
      out.push({ d, cls, key: `${e.from}|${e.to}|${e.type}|${i}` });
    });
    setSize({ w: cont.scrollWidth, h: cont.scrollHeight });
    setPaths(out);
  }, [edges, semesters, hoverCode, showAll, tick, containerRef]);

  useEffect(() => {
    const cont = containerRef.current;
    if (!cont) return;
    const ro = new ResizeObserver(() => setTick((t) => t + 1));
    ro.observe(cont);
    const onResize = () => setTick((t) => t + 1);
    window.addEventListener("resize", onResize);
    return () => { ro.disconnect(); window.removeEventListener("resize", onResize); };
  }, [containerRef]);

  return (
    <svg className="dep-svg" width={size.w} height={size.h} aria-hidden="true">
      <defs>
        <marker id="arrow" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0,0 L8,4 L0,8 z" className="arrowhead" />
        </marker>
        <marker id="arrow-bad" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0,0 L8,4 L0,8 z" className="arrowhead-bad" />
        </marker>
      </defs>
      {paths.map((p) => (
        <path key={p.key} d={p.d} className={p.cls} fill="none"
          markerEnd={p.cls.includes("violated") ? "url(#arrow-bad)" : "url(#arrow)"} />
      ))}
    </svg>
  );
}
