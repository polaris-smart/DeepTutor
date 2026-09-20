"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { BookOpen, Plus, Trash2 } from "lucide-react";
import { fetchAuthStatus } from "@/lib/auth";
import {
  fetchCourses,
  createCourse,
  fetchResourceCandidates,
  attachCourseResource,
  type Course,
  type CourseResourceCandidate,
} from "@/lib/courses-api";

const ALLOWED_ROLES = new Set(["admin", "teacher"]);

export default function CoursesPage() {
  const router = useRouter();
  const [courses, setCourses] = useState<Course[]>([]);
  const [candidates, setCandidates] = useState<CourseResourceCandidate[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [subject, setSubject] = useState("");
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const auth = await fetchAuthStatus();
        if (!ALLOWED_ROLES.has(auth.role ?? "")) { router.push("/space"); return; }
        const [cs, rc] = await Promise.all([fetchCourses(), fetchResourceCandidates()]);
        setCourses(cs); setCandidates(rc);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load");
      } finally { setLoading(false); }
    })();
  }, [router]);

  const handleCreate = useCallback(async () => {
    if (!name.trim()) return;
    setCreating(true); setError("");
    try {
      const course = await createCourse(name, description, subject);
      setCourses(prev => [...prev, course]);
      setName(""); setDescription(""); setSubject("");
    } catch (e) { setError(e instanceof Error ? e.message : "Failed to create"); }
    finally { setCreating(false); }
  }, [name, description, subject]);

  if (loading) return <div className="p-8">Loading...</div>;

  return (
    <div className="p-8 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold mb-6 flex items-center gap-2"><BookOpen className="w-6 h-6" /> 课程管理</h1>
      {error && <div className="bg-red-50 text-red-700 p-3 rounded mb-4">{error}</div>}
      <div className="bg-white rounded-lg shadow p-6 mb-8">
        <h2 className="text-lg font-semibold mb-4">新建课程</h2>
        <div className="grid grid-cols-3 gap-4 mb-4">
          <input className="border rounded p-2" placeholder="课程名称" value={name} onChange={e=>setName(e.target.value)} />
          <input className="border rounded p-2" placeholder="科目" value={subject} onChange={e=>setSubject(e.target.value)} />
          <input className="border rounded p-2 col-span-1" placeholder="描述" value={description} onChange={e=>setDescription(e.target.value)} />
        </div>
        <button onClick={handleCreate} disabled={creating||!name.trim()} className="bg-blue-600 text-white px-4 py-2 rounded disabled:opacity-50 flex items-center gap-1">
          <Plus className="w-4 h-4" />{creating?"创建中...":"创建课程"}
        </button>
      </div>
      <div className="space-y-4">
        {courses.map(c => (
          <div key={c.id} className="bg-white rounded-lg shadow p-4 flex items-center justify-between">
            <div><h3 className="font-semibold">{c.name}</h3><p className="text-sm text-gray-500">{c.subject} | {c.description}</p></div>
            <span className="text-xs text-gray-400">{c.resources?.length ?? 0} 资源</span>
          </div>
        ))}
        {!loading && courses.length===0 && <p className="text-gray-400">暂无课程</p>}
      </div>
    </div>
  );
}
