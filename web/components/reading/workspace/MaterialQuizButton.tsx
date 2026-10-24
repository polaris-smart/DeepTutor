"use client";

import { useState } from "react";
import { ClipboardCheck, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  useReadingActions,
  type ReadingActionEntry,
} from "@/components/reading/reading-actions-context";
import Tooltip from "@/shared/ui/Tooltip";

/**
 * "Generate a quiz" on the material itself — the extension-channel quiz
 * (three grounded questions, server-side grading, stars) that the workspace
 * otherwise only offers as a conversation turn. The entry exists so a learner
 * can self-serve the check without asking the tutor.
 *
 * The runner is the shared reading-actions one: the result lands as a card in
 * the companion column and is graded by the same quiz machinery every other
 * extension result uses (including the server verdict display).
 */
export function MaterialQuizButton({ disabled }: { disabled?: boolean }) {
  const { t } = useTranslation();
  const shared = useReadingActions();
  const [busy, setBusy] = useState(false);
  if (!shared || shared.ageMode !== "default") return null;

  const entry: ReadingActionEntry = {
    key: "quiz:start",
    extension: { id: "quiz" } as ReadingActionEntry["extension"],
    action: { id: "start", requires: ["visible_text"] } as ReadingActionEntry["action"],
    label: t("Generate a quiz"),
    needsSelection: false,
  };

  const label = t("Generate a quiz");
  return (
    <Tooltip label={label}>
      <button
        type="button"
        disabled={disabled || busy || shared.busyKey === "quiz:start"}
        onClick={() => {
          setBusy(true);
          void shared
            .run(entry)
            .catch(() => {
              // The runner surfaces its own error card; nothing to add here.
            })
            .finally(() => setBusy(false));
        }}
        aria-label={label}
        className="flex size-7 shrink-0 items-center justify-center rounded-md text-[var(--muted-foreground)] transition hover:bg-[var(--muted)] hover:text-[var(--foreground)] disabled:cursor-not-allowed disabled:opacity-40"
      >
        {busy || shared.busyKey === "quiz:start" ? (
          <Loader2 size={14} className="animate-spin" />
        ) : (
          <ClipboardCheck size={14} />
        )}
      </button>
    </Tooltip>
  );
}
