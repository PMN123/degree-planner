import { useEffect, useRef, useState } from "react";
import { api } from "../api";

interface Result { code: string; title?: string; credits?: number }
export interface Picked { code: string; title: string; credits: number }

// "ma 262" / "ma262" -> "MA 26200"; Purdue numbers are 5 digits (zero-padded).
function normalizeCode(raw: string): string | null {
  const m = raw.trim().toUpperCase().replace(/-/g, " ").match(/^([A-Z]+)\s*([0-9][0-9A-Z]{1,5})$/);
  if (!m) return null;
  let [, subj, num] = m;
  if (/^\d+$/.test(num) && num.length < 5) num = num.padStart(5, "0");
  return `${subj} ${num}`;
}

export function CourseSearch({
  placeholder, onPick, filterCodes, allowFreeform, defaultCredits = 3,
}: {
  placeholder: string;
  onPick: (c: Picked) => void;
  filterCodes?: string[]; // if given, restrict suggestions to these codes (slot options)
  allowFreeform?: boolean; // let the user add a typed course code that isn't in the catalog
  defaultCredits?: number; // credits to use for a freeform add (catalog hits keep their own)
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

  // When freeform is allowed and the typed text is a valid-looking code we don't already list,
  // offer to add it anyway (transfer / AP / IB credit for a course not in the scraped catalog).
  const freeformCode = allowFreeform ? normalizeCode(q) : null;
  const showFreeform = !!freeformCode && !results.some((r) => r.code === freeformCode);

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
      {open && (results.length > 0 || showFreeform) && (
        <div className="search-pop">
          {results.slice(0, 30).map((r) => (
            <div key={r.code} className="search-item" onMouseDown={(e) => { e.preventDefault(); pick(r); }}>
              <span className="si-code">{r.code}</span>
              <span className="si-title">{r.title || ""}</span>
              {r.credits != null && <span className="si-cr">{r.credits}cr</span>}
            </div>
          ))}
          {showFreeform && (
            <div className="search-item freeform" onMouseDown={(e) => { e.preventDefault(); onPick({ code: freeformCode!, title: "", credits: defaultCredits }); setQ(""); setOpen(false); setResults([]); }}>
              <span className="si-code">➕ {freeformCode}</span>
              <span className="si-title">add — not in catalog (transfer / AP / IB)</span>
              <span className="si-cr">{defaultCredits}cr</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
