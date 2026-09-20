"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { FileText, Download } from "lucide-react";
import { fetchAuthStatus } from "@/lib/auth";
import { downloadExamPaper } from "@/lib/exam-paper-api";

const ALLOWED_ROLES = new Set(["admin", "teacher"]);

export default function ExamPaperPage() {
  const router = useRouter();
  const [kbName, setKbName] = useState("试卷库-历史-高考");
  const [title, setTitle] = useState("");
  const [qIds, setQIds] = useState("");
  const [includeAnswers, setIncludeAnswers] = useState(true);
  const [error, setError] = useState("");
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    (async () => {
      const auth = await fetchAuthStatus();
      if (!ALLOWED_ROLES.has(auth.role ?? "")) router.push("/space");
    })();
  }, [router]);

  const handleGenerate = useCallback(async () => {
    if (!title.trim() || !qIds.trim()) return;
    setGenerating(true); setError("");
    try {
      await downloadExamPaper(
        { kb_name: kbName, title, question_ids: qIds.split(",").map(s=>s.trim()).filter(Boolean), include_answers: includeAnswers },
        `${title}.docx`
      );
    } catch (e) { setError(e instanceof Error ? e.message : "Failed"); }
    finally { setGenerating(false); }
  }, [kbName, title, qIds, includeAnswers]);

  return (
    <div className="p-8 max-w-3xl mx-auto">
      <h1 className="text-2xl font-bold mb-6 flex items-center gap-2"><FileText className="w-6 h-6" /> 组卷导出</h1>
      {error && <div className="bg-red-50 text-red-700 p-3 rounded mb-4">{error}</div>}
      <div className="bg-white rounded-lg shadow p-6 space-y-4">
        <div><label className="block text-sm font-medium mb-1">知识库</label>
          <select className="border rounded p-2 w-full" value={kbName} onChange={e=>setKbName(e.target.value)}>
            <option>试卷库-历史-高考</option><option>试卷库-数学</option><option>试卷库-政治</option>
          </select></div>
        <div><label className="block text-sm font-medium mb-1">试卷标题</label>
          <input className="border rounded p-2 w-full" value={title} onChange={e=>setTitle(e.target.value)} placeholder="期末考试卷" /></div>
        <div><label className="block text-sm font-medium mb-1">题目 ID（逗号分隔）</label>
          <textarea className="border rounded p-2 w-full h-20" value={qIds} onChange={e=>setQIds(e.target.value)} placeholder="q_001, q_002, ..." /></div>
        <div><label className="flex items-center gap-2"><input type="checkbox" checked={includeAnswers} onChange={e=>setIncludeAnswers(e.target.checked)} />含答案</label></div>
        <button onClick={handleGenerate} disabled={generating||!title.trim()||!qIds.trim()} className="bg-blue-600 text-white px-4 py-2 rounded disabled:opacity-50 flex items-center gap-1">
          <Download className="w-4 h-4" />{generating?"生成中...":"生成并下载 .docx"}
        </button>
      </div>
    </div>
  );
}
