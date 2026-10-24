import { apiFetch, apiUrl } from "@/lib/api";

/** One knowledge point below its mastery gate, per student. */
export interface WeakKnowledgePoint {
  kp_id: string;
  name: string;
  /** Display mastery 0..1 (0 for never-attempted quantitative KPs). */
  mastery: number;
}

/** Per-student row of the class insights overview. */
export interface ClassStudentInsight {
  username: string;
  kp_total: number;
  /** Average display mastery across all KPs, 0..100. */
  avg_mastery_pct: number;
  weak: WeakKnowledgePoint[];
  /** ISO timestamp of the student's latest progress update, or null. */
  last_active: string | null;
  /** Whitelist views mark rostered/linked students that have no data yet. */
  no_data?: boolean;
  /** False when the student has progress records but no attempts yet —
   * the family view renders a "no practice" empty state instead of a 0%. */
  has_attempts?: boolean;
}

/** Response of GET /api/class-insights/overview. */
export interface ClassInsightsOverview {
  students: ClassStudentInsight[];
  generated_at: string;
  /** Present when the view is narrowed to one class roster. */
  class?: { id: string; name: string };
}

/** One class roster (K12 班级), owned by a teacher. */
export interface ClassRoster {
  id: string;
  name: string;
  teacher: string;
  students: string[];
  created_at: string;
}

/** Response of GET /api/class-insights/classes. */
export interface ClassRostersResponse {
  classes: ClassRoster[];
}

async function parseError(res: Response, fallback: string): Promise<never> {
  const data = await res.json().catch(() => ({}));
  throw new Error(
    typeof data.detail === "string" ? data.detail : `${fallback}: ${res.status}`,
  );
}

export async function fetchClassInsights(
  classId?: string,
): Promise<ClassInsightsOverview> {
  const qs = classId ? `?class_id=${encodeURIComponent(classId)}` : "";
  const res = await apiFetch(apiUrl(`/api/class-insights/overview${qs}`));
  if (!res.ok) return parseError(res, "Failed to fetch class insights");
  return res.json();
}

export async function fetchClassRosters(): Promise<ClassRostersResponse> {
  const res = await apiFetch(apiUrl("/api/class-insights/classes"));
  if (!res.ok) return parseError(res, "Failed to fetch class rosters");
  return res.json();
}

/** Parent (or admin) view: mastery of the linked children. */
export async function fetchMyChildren(): Promise<ClassInsightsOverview> {
  const res = await apiFetch(apiUrl("/api/class-insights/my-children"));
  if (!res.ok) return parseError(res, "Failed to fetch children insights");
  return res.json();
}

/** Body of POST /api/class-insights/classes. */
export interface CreateClassInput {
  name: string;
  /** Roster of student usernames; the backend validates each is a live
   *  role=student account and rejects unknown names. */
  students?: string[];
}

/** Body of PUT /api/class-insights/classes/{id}. */
export interface UpdateClassInput {
  name?: string;
  students?: string[];
}

/** Create a class roster owned by the current teacher (or admin). */
export async function createClass(
  input: CreateClassInput,
): Promise<{ class: ClassRoster }> {
  const res = await apiFetch(apiUrl("/api/class-insights/classes"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) return parseError(res, "Failed to create class");
  return res.json();
}

/** Rename a class and/or replace its roster (owner or admin). */
export async function updateClass(
  classId: string,
  input: UpdateClassInput,
): Promise<{ class: ClassRoster }> {
  const res = await apiFetch(
    apiUrl(`/api/class-insights/classes/${encodeURIComponent(classId)}`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  if (!res.ok) return parseError(res, "Failed to update class");
  return res.json();
}

/** Delete a class roster (owner or admin); student accounts are untouched. */
export async function deleteClass(classId: string): Promise<{ ok: boolean }> {
  const res = await apiFetch(
    apiUrl(`/api/class-insights/classes/${encodeURIComponent(classId)}`),
    { method: "DELETE" },
  );
  if (!res.ok) return parseError(res, "Failed to delete class");
  return res.json();
}
