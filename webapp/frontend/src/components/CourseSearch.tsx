import { useEffect, useRef, useState } from "react";
import { api } from "../api";

interface Result { code: string; title?: string; credits?: number }
export interface Picked { code: string; title: string; credits: number }

export function CourseSearch({
  placeholder, onPick, filterCodes,
}: {
  placeholder: string;
  onPick: (c: Picked) => void;
  filterCodes?: string[]; // if given, restrict suggestions to these codes (slot options)
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Result[]>([]);
  const [open, setOpen] = useState(false);
  const timer = useRef<number>();

  useEffect(() => {
    if (filterCodes) {
      const ql = q.trim().toLowerCase();
      setResults(filterCodes.filter((c) => !ql || c.toLowerCase().includes(ql)).map((code) => ({ code })));
      return;
    }
    window.clearTimeout(timer.current);
    if (!q.trim()) { setResults([]); return; }
    timer.current = window.setTimeout(async () => {
      try { setResults(await api.courseSearch(q)); } catch { setResults([]); }
    }, 160);
  }, [q, filterCodes]);

  const pick = (r: Result) => {
    onPick({ code: r.code, title: r.title || "", credits: r.credits ?? 3 });
    setQ(""); setOpen(false); setResults([]);
  };

  return (
    <div className="course-search">
      <input
        className="course-input"
        value={q}
        placeholder={placeholder}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && results.length > 0 && (
        <div className="search-pop">
          {results.slice(0, 30).map((r) => (
            <div key={r.code} className="search-item" onMouseDown={(e) => { e.preventDefault(); pick(r); }}>
              <span className="si-code">{r.code}</span>
              <span className="si-title">{r.title || ""}</span>
              {r.credits != null && <span className="si-cr">{r.credits}cr</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
