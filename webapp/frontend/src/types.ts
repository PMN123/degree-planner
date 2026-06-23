export interface ProgramMeta {
  id: string;
  name: string;
  type: "major" | "minor" | "certificate" | "graduate" | "other";
  degree?: string;
  college?: string;
  total_credits?: number;
  verified: boolean;
  has_requirements: boolean;
  source_url?: string;
}

export interface CourseMatch {
  subjects?: string[];
  min_number?: number;
  max_number?: number;
}

export interface TrackOption {
  id: string;
  name: string;
  items: Omit<Course, "uid">[]; // the track's courses + constrained slots, expanded on pick
}

export interface Course {
  uid: string;            // stable id for drag-and-drop (codes can repeat as slots)
  code?: string;          // present once a real course is placed/chosen
  title?: string;
  credits: number;
  satisfies?: string[];   // program ids this course counts toward
  locked?: boolean;       // required core — movable, not deletable
  slot?: boolean;         // unfilled selective/elective placeholder
  slotKind?: "choose" | "open" | "track"; // constrained pick / open gen-ed / concentration chooser
  label?: string;         // slot label (e.g. "Calculus Selective")
  options?: string[];     // allowed course codes for a slot (empty => open search)
  note?: string | null;   // advising note / constraint hint for a slot
  match?: CourseMatch | null; // optional subject/number filter for an open slot
  tracks?: TrackOption[]; // for a track-chooser slot
  track_credits?: number; // estimated credits a concentration adds (display only)
  alternatives?: string[]; // "MA 16100 or MA 16500"
}

export interface Semester {
  term: string;
  courses: Course[];
}

export interface Edge {
  from: string;
  to: string;
  type: "prereq" | "coreq";
  satisfied: boolean;
}

export interface PrereqCheck {
  code: string;
  term: string;
  ok: boolean;
  status?: string;
  missing_best_alternative?: string[];
  warnings?: string[];
}

export interface ProgramResult {
  id: string;
  name: string;
  degree?: string;
  type?: string;
  total_credits?: number;
  satisfied: boolean;
  requirements: any[];
  progress: { have: number; need: number; percent: number };
  source_url?: string;
  notes?: string[];
}

export interface Audit {
  credits: { completed: number; planned: number; total: number; degree_target: number; target_met: boolean };
  programs: ProgramResult[];
  loads: { term: string; credits: number; count: number; flag?: string | null }[];
  overlaps: Record<string, string[]>;
  edges: Edge[];
  prerequisites: { ok: boolean; checks: PrereqCheck[]; warnings: string[] };
  feasible: boolean;
}

export interface Constraints {
  start_term: string;
  target_term?: string;
  max_credits: number;
  use_summers: boolean;
}
