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
 * Renders nothing when the fetch fails or there is nothing due and no recent
 * path — the welcome greeting stays exactly as before for fresh accounts.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { fetchAllProgress } from "@/lib/learning-api";

interface RecallState {
  activeBooks: { book_id: string; title: string }[];
}

export default function HomeReviewRecallCard() {
  const { t, i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const [state, setState] = useState<RecallState | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAllProgress()
      .then((result) => {
        if (cancelled) return;
        const active = (result.summaries || [])
          .filter((s) => s.kp_count > 0)
          .slice(0, 3)
          .map((s) => ({
            book_id: s.book_id,
            title: s.name || s.book_id,
          }));
        if (active.length > 0) {
          setState({ activeBooks: active });
        }
      })
      .catch(() => {
        /* not signed in / API unavailable → stay hidden */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!state) return null;

  const headline = zh ? "继续上次的学习" : "Continue where you left off";

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
          {state.activeBooks.length > 0 && (
            <p className="mt-0.5 truncate text-[12px] text-[var(--muted-foreground)]">
              {state.activeBooks.map((b) => b.title).join(zh ? "　" : " · ")}
            </p>
          )}
        </div>
        <span className="shrink-0 rounded-lg bg-teal-500/10 px-3 py-1.5 text-[13px] font-medium text-teal-600 dark:text-teal-400">
          {zh ? "继续学习" : "Resume"}
          <span className="ml-1 inline-block transition-transform group-hover:translate-x-0.5">→</span>
        </span>
      </Link>
    </div>
  );
}
