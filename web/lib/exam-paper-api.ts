export interface ExamPaperRequest {
  kb_name: string;
  title: string;
  question_ids: string[];
  include_answers: boolean;
}

export async function generateExamPaper(req: ExamPaperRequest): Promise<Blob> {
  const r = await fetch("/api/exam-paper/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) throw new Error(`Exam paper generation failed: ${r.status}`);
  return r.blob();
}

export async function downloadExamPaper(req: ExamPaperRequest, filename: string): Promise<void> {
  const blob = await generateExamPaper(req);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
