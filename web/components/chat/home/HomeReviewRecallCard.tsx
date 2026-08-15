"use client";

/**
 * Home review recall card (student retention).
 *
 * The scheduler already knows which KPs are due for spaced review, but that
 * fact used to live one page away (learning-space path detail) as a single
 * line of small text. This card surfaces "N items due today / continue where
 * you left off" right on the empty home state, so a returning student has a
 * one-click low-friction re-entry instead of an empty greeting.
 *
 * Renders nothing when the fetch fails or there is nothing active — the
 * welcome greeting stays exactly as before for fresh accounts.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { fetchAllProgress, fetchMasteryMap } from "@/lib/learning-api";

interface RecallState {
  dueTotal: number;
  /** Most recently updated active book, plus the count of other active ones. */
  primaryTitle: string;
  otherCount: number;
}

export default function HomeReviewRecallCard() {
  const { i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const [state, setState] = useState<RecallState | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await fetchAllProgress();
        const active = (result.summaries || [])
          .filter((s) => s.kp_count > 0)
          .sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
        if (!active.length || cancelled) return;
        // Pull real due counts from the most recent paths (bounded: first 3,
        // so a returning student's wait stays short even with many paths).
        let dueTotal = 0;
        for (const s of active.slice(0, 3)) {
          try {
            const { map } = await fetchMasteryMap(s.book_id);
            dueTotal += map?.due_reviews || 0;
          } catch {
            /* one path failing must not blank the card */
          }
        }
        if (cancelled) return;
        setState({
          dueTotal,
          primaryTitle: active[0].name || active[0].book_id,
          otherCount: Math.max(0, active.length - 1),
        });
      } catch {
        /* not signed in / API unavailable → stay hidden */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!state) return null;

  const hasDue = state.dueTotal > 0;
  const headline = hasDue
    ? zh
      ? `今日有 ${state.dueTotal} 项复习到期`
      : `${state.dueTotal} item${state.dueTotal > 1 ? "s" : ""} due for review today`
    : zh
      ? "继续上次的学习"
      : "Continue where you left off";
  const sub = state.otherCount
    ? `${state.primaryTitle} ${zh ? `等 ${state.otherCount + 1} 本在学` : `+ ${state.otherCount} more paths`}`
    : state.primaryTitle;

  return (
    <div className="mb-6 w-full max-w-[960px]">
      <Link
        href="/space/learning"
        className="group flex w-full items-center justify-between gap-4 rounded-xl border border-[var(--border)] bg-[var(--card)] px-5 py-4 transition-colors hover:border-teal-500/40"
      >
        <div className="min-w-0">
          <p className="text-[14px] font-medium text-[var(--foreground)]">
            {headline}
          </p>
          <p className="mt-0.5 truncate text-[12px] text-[var(--muted-foreground)]">
            {sub}
          </p>
        </div>
        <span className="shrink-0 rounded-lg bg-teal-500/10 px-3 py-1.5 text-[13px] font-medium text-teal-600 dark:text-teal-400">
          {zh ? (hasDue ? "去复习" : "继续学习") : hasDue ? "Review now" : "Resume"}
          <span className="ml-1 inline-block transition-transform group-hover:translate-x-0.5">→</span>
        </span>
      </Link>
    </div>
  );
}
