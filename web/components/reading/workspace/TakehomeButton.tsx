"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  CircleAlert,
  Download,
  FileText,
  Loader2,
  Printer,
  RotateCcw,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import Tooltip from "@/shared/ui/Tooltip";
import {
  extractTakehomePath,
  streamPartnerTurn,
  verifyTakehomeDownload,
} from "@/lib/takehome-api";
import {
  addPartnerAssets,
  getPartnerAssets,
  getPartnerHistory,
  listPartners,
} from "@/lib/partners-api";

type Phase =
  | { kind: "idle" }
  | { kind: "no-partner" }
  | { kind: "preparing" }
  | { kind: "running"; startedAt: number }
  | { kind: "done"; href: string }
  | { kind: "error"; message: string };

const RUN_SOFT_TIMEOUT_MS = 10 * 60 * 1000;

/**
 * "A4 take-home sheet" on the reading workspace bar: hands the page's material
 * to the learner's own AI partner, whose a4-takehome skill prints the
 * printable sheet into the partner workspace outputs tree. The panel then
 * links the artifact through the public /files/outputs download surface.
 *
 * Everything behind the button is the already-proven partner/exec pipeline —
 * this entry only orchestrates it, it does not reimplement the skill.
 */
export function TakehomeButton({
  disabled,
  materialId,
  materialTitle,
  locator,
}: {
  disabled?: boolean;
  materialId: string | null;
  materialTitle: string;
  locator: number;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [progress, setProgress] = useState("");
  const [now, setNow] = useState(Date.now());
  const abortRef = useRef<AbortController | null>(null);
  // Thinking deltas are streamed, not kept by the stream helper; the artifact
  // path often only appears there (the agent narrates where it writes), so
  // accumulate them for the final path scan.
  const streamTextRef = useRef("");

  useEffect(() => {
    if (phase.kind !== "running") return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [phase.kind]);

  // Closing the panel cancels an in-flight generation: the partner turn keeps
  // running server-side (a real tutor keeps working), but this page stops
  // waiting and the next click starts a fresh one.
  useEffect(() => {
    if (open) return;
    abortRef.current?.abort();
    abortRef.current = null;
  }, [open]);

  function errorMessage(error: unknown): string {
    const raw = error instanceof Error ? error.message : String(error);
    if (/aborted|abort/i.test(raw)) return t("Take-home generation was cancelled.");
    return raw || t("Could not generate the take-home sheet. Please try again.");
  }

  /**
   * Wait for the partner's reply to a turn whose prompt was sent at
   * `sentAt`, polling the chat history — the fallback path when the SSE
   * connection was cut by an idle proxy but the run continues server-side.
   */
  async function pollPartnerReply(
    partnerId: string,
    sentAt: string,
    signal: AbortSignal,
  ): Promise<string> {
    const sentMs = Date.parse(sentAt);
    const deadline = Date.now() + RUN_SOFT_TIMEOUT_MS;
    while (Date.now() < deadline && !signal.aborted) {
      await new Promise((resolve) => window.setTimeout(resolve, 15000));
      if (signal.aborted) throw new Error("aborted");
      const history = await getPartnerHistory(partnerId, { limit: 10 }).catch(
        () => [] as { role: string; content: string; timestamp?: string }[],
      );
      const reply = [...history]
        .reverse()
        .find(
          (row) =>
            row.role === "assistant" &&
            (!row.timestamp || Date.parse(row.timestamp) > sentMs - 3000),
        );
      if (reply?.content?.trim()) return reply.content;
    }
    return "";
  }

  async function ensurePartnerWithSkill(): Promise<string> {    const partners = await listPartners();
    const partner =
      partners.find((row) => row.can_manage !== false) || partners[0];
    if (!partner) {
      setPhase({ kind: "no-partner" });
      throw new Error("no-partner");
    }
    const assets = await getPartnerAssets(partner.partner_id);
    const skills = Array.isArray(assets?.skills) ? assets.skills : [];
    const hasSkill = skills.some((row) => row?.name === "a4-takehome");
    if (!hasSkill) {
      setProgress(t("Mounting the take-home skill on your AI partner…"));
      await addPartnerAssets(partner.partner_id, { skills: ["a4-takehome"] });
    }
    return partner.partner_id;
  }

  async function generate() {
    if (!materialId) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setProgress("");
    setPhase({ kind: "preparing" });
    try {
      const partnerId = await ensurePartnerWithSkill();
      setPhase({ kind: "running", startedAt: Date.now() });
      setProgress(t("Your AI partner is writing the sheet — this takes a few minutes."));
      streamTextRef.current = "";
      const prompt =
        `请按照 a4-takehome 技能合同，为正在学的这页材料生成一张 A4 带走单（学生版）。` +
        `材料：${materialTitle || materialId}（第 ${locator} 段）。` +
        `完成后把生成的 HTML 文件完整路径告诉我（outputs/ 下的 .html 文件）。`;
      const sentAt = new Date().toISOString();
      let reply = "";
      try {
        reply = await streamPartnerTurn(
          partnerId,
          prompt,
          {
            onThinking: (text) => {
              streamTextRef.current += `\n${text}`;
              setProgress((prev) => text.slice(-240) || prev);
            },
          },
          controller.signal,
        );
      } catch (error) {
        if (controller.signal.aborted) throw error;
        // Public traffic rides proxies that cut an idle stream (e.g. a 100s
        // no-data window) while the partner run keeps going server-side.
        // Fall back to polling the chat history for the finished reply.
        setProgress(t("Connection paused — still checking with your AI partner…"));
        reply = await pollPartnerReply(partnerId, sentAt, controller.signal);
      }
      if (!reply) {
        reply = await pollPartnerReply(partnerId, sentAt, controller.signal);
      }
      const scanned = extractTakehomePath(
        `${reply}\n${streamTextRef.current}`,
      );
      const href = scanned ? await verifyTakehomeDownload(scanned) : null;
      if (href) {
        setPhase({ kind: "done", href: `/files/outputs/${href}` });
      } else {
        throw new Error(
          t(
            "The sheet was generated but no downloadable file was reported. Open your AI partner's conversation to find it.",
          ),
        );
      }
    } catch (error) {
      if (error instanceof Error && error.message === "no-partner") return;
      setPhase({ kind: "error", message: errorMessage(error) });
    } finally {
      abortRef.current = null;
    }
  }

  // A long partner run outlives the panel's patience: stop polling silently.
  useEffect(() => {
    if (
      phase.kind === "running" &&
      now - phase.startedAt > RUN_SOFT_TIMEOUT_MS
    ) {
      abortRef.current?.abort();
      setPhase({
        kind: "error",
        message: t(
          "The generation is taking too long and this page stopped waiting. Try again, or ask your AI partner directly.",
        ),
      });
    }
  }, [now, phase, t]);

  const label = t("A4 take-home sheet");
  const running = phase.kind === "preparing" || phase.kind === "running";
  const elapsed =
    phase.kind === "running" ? Math.round((now - phase.startedAt) / 1000) : 0;
  return (
    <>
      <Tooltip label={label}>
        <button
          type="button"
          disabled={disabled || running}
          onClick={() => {
            setPhase({ kind: "idle" });
            setProgress("");
            setOpen(true);
          }}
          aria-label={label}
          className="flex size-7 shrink-0 items-center justify-center rounded-md text-[var(--muted-foreground)] transition hover:bg-[var(--muted)] hover:text-[var(--foreground)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          {running ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <Printer size={14} />
          )}
        </button>
      </Tooltip>
      {open ? (
        <div className="fixed inset-0 z-50" onClick={() => setOpen(false)} />
      ) : null}
      {open ? (
        <div className="absolute right-2 top-[calc(100%+4px)] z-50 w-80 rounded-xl border border-[var(--border)] bg-[var(--card)] p-3 text-[12px] shadow-[0_20px_50px_rgba(0,0,0,.18)] dark:bg-[var(--popover)]">
          <div className="flex items-center gap-2">
            <FileText size={13} className="shrink-0 text-[var(--primary)]" />
            <p className="min-w-0 flex-1 font-semibold">{label}</p>
            <button
              type="button"
              aria-label={t("Close")}
              onClick={() => setOpen(false)}
              className="inline-flex size-6 items-center justify-center rounded-md text-[var(--muted-foreground)] transition hover:bg-[var(--muted)]"
            >
              <X size={12} />
            </button>
          </div>
          {phase.kind === "idle" ? (
            <>
              <p className="mt-2 leading-relaxed text-[var(--muted-foreground)]">
                {t(
                  "Turn this material into a printable A4 take-home sheet (summary, practice questions and scoring points). Your AI partner writes it; generation takes a few minutes.",
                )}
              </p>
              <button
                type="button"
                disabled={!materialId}
                onClick={() => void generate()}
                className="mt-3 flex h-8 w-full items-center justify-center gap-1.5 rounded-lg bg-[var(--primary)] text-[12px] font-medium text-[var(--primary-foreground)] transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Printer size={13} />
                {t("Generate the A4 take-home sheet")}
              </button>
            </>
          ) : null}
          {phase.kind === "no-partner" ? (
            <>
              <p className="mt-2 leading-relaxed text-[var(--muted-foreground)]">
                {t(
                  "You don't have an AI partner yet — it is the one who writes your take-home sheet.",
                )}
              </p>
              <Link
                href="/partners/new"
                onClick={() => setOpen(false)}
                className="mt-3 flex h-8 w-full items-center justify-center rounded-lg bg-[var(--primary)] text-[12px] font-medium text-[var(--primary-foreground)] transition hover:opacity-90"
              >
                {t("Create an AI partner")}
              </Link>
            </>
          ) : null}
          {phase.kind === "preparing" || phase.kind === "running" ? (
            <div role="status" className="mt-2 space-y-2">
              <p className="flex items-center gap-1.5 text-[var(--muted-foreground)]">
                <Loader2 size={13} className="animate-spin" />
                {phase.kind === "preparing"
                  ? t("Finding your AI partner…")
                  : progress || t("Working on it…")}
              </p>
              {phase.kind === "running" ? (
                <p className="text-[11px] tabular-nums text-[var(--muted-foreground)]">
                  {t("Elapsed {{seconds}}s · keeping the page open is fine", {
                    seconds: elapsed,
                  })}
                </p>
              ) : null}
              <div className="h-2 w-11/12 animate-pulse rounded bg-[var(--muted)]" />
              <div className="h-2 w-7/12 animate-pulse rounded bg-[var(--muted)]" />
            </div>
          ) : null}
          {phase.kind === "done" ? (
            <div className="mt-2 space-y-2">
              <p className="leading-relaxed text-[var(--muted-foreground)]">
                {t("Your A4 take-home sheet is ready to download and print.")}
              </p>
              <a
                href={phase.href}
                target="_blank"
                rel="noreferrer"
                className="flex h-8 w-full items-center justify-center gap-1.5 rounded-lg bg-[var(--primary)] text-[12px] font-medium text-[var(--primary-foreground)] transition hover:opacity-90"
              >
                <Download size={13} />
                {t("Download the A4 sheet")}
              </a>
              <button
                type="button"
                onClick={() => {
                  setPhase({ kind: "idle" });
                  setProgress("");
                }}
                className="flex h-8 w-full items-center justify-center gap-1.5 rounded-lg border border-[var(--border)] text-[12px] font-medium transition hover:bg-[var(--muted)]"
              >
                <RotateCcw size={13} />
                {t("Generate again")}
              </button>
            </div>
          ) : null}
          {phase.kind === "error" ? (
            <div className="mt-2 space-y-2">
              <p className="flex items-start gap-1.5 text-[var(--destructive)]">
                <CircleAlert size={13} className="mt-0.5 shrink-0" />
                <span className="min-w-0 flex-1 break-words">{phase.message}</span>
              </p>
              <button
                type="button"
                onClick={() => {
                  setPhase({ kind: "idle" });
                  setProgress("");
                }}
                className="flex h-8 w-full items-center justify-center gap-1.5 rounded-lg border border-[var(--border)] text-[12px] font-medium transition hover:bg-[var(--muted)]"
              >
                <RotateCcw size={13} />
                {t("Try again")}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
