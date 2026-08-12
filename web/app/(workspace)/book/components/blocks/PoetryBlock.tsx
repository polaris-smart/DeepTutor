"use client";

import { Volume2 } from "lucide-react";
import type { Block } from "@/lib/book-types";

/**
 * YuEdu fork: 诗词 block 渲染组件。
 * 渲染诗词原文（竖排/横排）+ 拼音 + 注解 + 朗读按钮。
 * Payload 形如:
 * {
 *   title: "静夜思",
 *   author: "李白",
 *   dynasty: "唐",
 *   lines: [{ text: "床前明月光", pinyin: "chuáng qián míng yuè guāng" }, ...],
 *   annotations: [{ term: "疑", explanation: "好像" }, ...]
 * }
 */
export default function PoetryBlock({ block }: { block: Block }) {
  const params = (block.payload as Record<string, unknown> | undefined) ?? {};
  const title = String(params.title ?? "");
  const author = String(params.author ?? "");
  const dynasty = String(params.dynasty ?? "");
  const lines = Array.isArray(params.lines) ? params.lines : [];
  const annotations = Array.isArray(params.annotations) ? params.annotations : [];

  const handleSpeak = () => {
    const text = lines.map((l: Record<string, unknown>) => String(l.text ?? "")).join("。");
    if (text && "speechSynthesis" in window) {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "zh-CN";
      utterance.rate = 0.8;
      speechSynthesis.speak(utterance);
    }
  };

  return (
    <div className="my-4 rounded-2xl border border-[var(--border)] bg-gradient-to-b from-[var(--card)] to-[var(--background)] p-6 shadow-sm">
      {/* 标题区 */}
      <div className="mb-4 text-center">
        {title && (
          <h3 className="text-xl font-bold text-[var(--foreground)]">{title}</h3>
        )}
        {(author || dynasty) && (
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            {[dynastyText(dynasty), author].filter(Boolean).join(" · ")}
          </p>
        )}
      </div>

      {/* 诗句 + 拼音 */}
      <div className="mx-auto max-w-md space-y-3">
        {lines.map((line: Record<string, unknown>, i: number) => {
          const text = String(line.text ?? "");
          const pinyin = String(line.pinyin ?? "");
          return (
            <div key={i} className="text-center">
              {pinyin && (
                <p className="text-xs text-[var(--muted-foreground)]/70 tracking-wide">
                  {pinyin}
                </p>
              )}
              <p className="text-lg leading-relaxed text-[var(--foreground)] tracking-wider">
                {text}
              </p>
            </div>
          );
        })}
      </div>

      {/* 朗读按钮 */}
      <div className="mt-4 text-center">
        <button
          onClick={handleSpeak}
          className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--card)] px-3 py-1 text-xs text-[var(--muted-foreground)] transition hover:bg-[var(--background)] hover:text-[var(--foreground)]"
        >
          <Volume2 className="h-3.5 w-3.5" />
          朗读
        </button>
      </div>

      {/* 注解 */}
      {annotations.length > 0 && (
        <div className="mt-4 border-t border-[var(--border)]/50 pt-3">
          <p className="mb-2 text-xs font-medium text-[var(--muted-foreground)]">注释</p>
          <dl className="space-y-1">
            {annotations.map((ann: Record<string, unknown>, i: number) => (
              <div key={i} className="flex gap-2 text-sm">
                <dt className="font-medium text-[var(--foreground)]">
                  {String(ann.term ?? "")}
                </dt>
                <dd className="text-[var(--muted-foreground)]">
                  {String(ann.explanation ?? "")}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}

function dynastyText(dynasty: string): string {
  if (!dynasty) return "";
  return `${dynasty}代`;
}
