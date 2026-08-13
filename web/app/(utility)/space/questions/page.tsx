"use client";

import { useState } from "react";
import QuestionBankSection from "@/components/space/QuestionBankSection";
import PaperReorderEditor from "./PaperReorderEditor";
import type { QuestionsSpaceView } from "./types";

export default function SpaceQuestionsPage() {
  const [view, setView] = useState<QuestionsSpaceView>("question_bank");

  return (
    <div className="space-y-5">
      <div
        className="flex w-fit rounded-lg border border-[var(--border)] bg-[var(--muted)]/40 p-1"
        role="tablist"
        aria-label="题目空间"
      >
        <button
          type="button"
          role="tab"
          aria-selected={view === "question_bank"}
          onClick={() => setView("question_bank")}
          className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] ${
            view === "question_bank"
              ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm"
              : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
          }`}
        >
          题库
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "paper_reorder"}
          onClick={() => setView("paper_reorder")}
          className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] ${
            view === "paper_reorder"
              ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm"
              : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
          }`}
        >
          题卷重排
        </button>
      </div>

      {view === "question_bank" ? <QuestionBankSection /> : <PaperReorderEditor />}
    </div>
  );
}
