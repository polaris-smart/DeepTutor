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
}

/** Response of GET /api/v1/class-insights/overview. */
export interface ClassInsightsOverview {
  students: ClassStudentInsight[];
  generated_at: string;
}

export async function fetchClassInsights(): Promise<ClassInsightsOverview> {
  const res = await apiFetch(apiUrl("/api/v1/class-insights/overview"));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : `Failed to fetch class insights: ${res.status}`,
    );
  }
  return res.json();
}
