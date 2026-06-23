import type { Audit, Course, ProgramMeta, Semester } from "./types";

async function jget<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}
async function jpost<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

export const api = {
  meta: () => jget<{ catalog_courses: number; catalog_term: string; programs: number }>("/api/meta"),

  programs: (type?: string, q?: string, limit = 0) => {
    const p = new URLSearchParams();
    if (type) p.set("type", type);
    if (q) p.set("q", q);
    if (limit) p.set("limit", String(limit));
    return jget<{ programs: ProgramMeta[] }>(`/api/programs?${p}`).then((r) => r.programs);
  },

  program: (id: string) => jget<any>(`/api/programs/${encodeURIComponent(id)}`),

  courseSearch: (q: string) =>
    jget<{ results: { code: string; title?: string; credits?: number }[] }>(
      `/api/courses/search?q=${encodeURIComponent(q)}`
    ).then((r) => r.results),

  scaffold: (programs: string[], completed: Course[], constraints: unknown) =>
    jpost<{ semesters: Semester[]; source: string; notes: string[] }>("/api/plan/scaffold", {
      programs,
      completed,
      constraints,
    }),

  audit: (programs: string[], completed: Course[], semesters: Semester[]) =>
    jpost<Audit>("/api/audit", { programs, completed, semesters }),

  ensureBatch: (codes: string[]) =>
    jpost<{ added: string[]; failed: string[]; already_cached: number }>("/api/courses/ensure-batch", { codes }),
};
