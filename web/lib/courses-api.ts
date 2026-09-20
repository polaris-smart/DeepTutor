export interface Course {
  id: string;
  name: string;
  description: string;
  subject: string;
  syllabus?: {
    units: {
      unit_id: string;
      title: string;
      lessons: { lesson_id: string; title: string; resource_ids: string[] }[];
    }[];
  };
  resources: { resource_id: string; type: string; title: string }[];
  created_at: string;
}

export interface CourseResourceCandidate {
  id: string;
  title: string;
  type: string;
  kb_name: string;
}

const BASE = "/api/courses";

function authHeaders(): HeadersInit {
  return { "Content-Type": "application/json" };
}

export async function fetchCourses(): Promise<Course[]> {
  const r = await fetch(BASE, { headers: authHeaders() });
  if (!r.ok) throw new Error(`Failed to fetch courses: ${r.status}`);
  const d = await r.json();
  return d.courses || [];
}

export async function createCourse(name: string, description: string, subject: string): Promise<Course> {
  const r = await fetch(BASE, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ name, description, subject }),
  });
  if (!r.ok) throw new Error(`Failed to create course: ${r.status}`);
  return r.json();
}

export async function fetchResourceCandidates(): Promise<CourseResourceCandidate[]> {
  const r = await fetch(`${BASE}/resource-candidates`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`Failed to fetch resource candidates: ${r.status}`);
  const d = await r.json();
  return d.candidates || [];
}

export async function attachCourseResource(courseId: string, resourceId: string, type: string): Promise<void> {
  const r = await fetch(`${BASE}/${courseId}/resources`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ resource_id: resourceId, type }),
  });
  if (!r.ok) throw new Error(`Failed to attach resource: ${r.status}`);
}
