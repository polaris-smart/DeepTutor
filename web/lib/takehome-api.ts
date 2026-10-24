"use client";

/**
 * Child-facing A4 take-home channel, on top of the already-proven
 * partner/exec pipeline: the reading page hands one instruction to the
 * learner's own AI partner over the HTTP chat stream, and the skill's HTML
 * artifact under the partner workspace `outputs/` tree is served back through
 * the public `/files/outputs/...` download surface (which searches visible
 * partner scopes for the caller, #1012).
 */

import { apiUrl } from "@/lib/api";

export interface PartnerTurnEvents {
  onThinking?: (text: string) => void;
  onStatus?: (text: string) => void;
}

/** Any path-like mention of the skill's HTML product inside stream text. */
const OUTPUT_PATH_RE =
  /(?:[\w./-]*outputs\/)?[\w./-]*a4-takehome[\w./-]*\.html/g;

export function extractTakehomePath(text: string): string {
  const seen = new Set<string>();
  for (const match of text.matchAll(OUTPUT_PATH_RE)) {
    let path = match[0].replace(/^[./]+/, "");
    // The download surface wants the path below the workspace root;
    // agent replies usually spell it with the `workspace/` prefix kept.
    if (!path.includes("outputs/")) continue;
    seen.add(path);
  }
  // Prefer the longest mention: it is the most specific (full run dir).
  return [...seen].sort((a, b) => b.length - a.length)[0] || "";
}

/**
 * Stream one partner turn over the HTTP execute-stream endpoint.
 * Resolves with the assistant's final reply text; thinking deltas stream
 * through `onThinking` so a multi-minute tool run still looks alive.
 */
export async function streamPartnerTurn(
  partnerId: string,
  content: string,
  events: PartnerTurnEvents = {},
  signal?: AbortSignal,
): Promise<string> {
  const response = await fetch(apiUrl(`/api/partners/${encodeURIComponent(partnerId)}/chat/execute-stream`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ content }),
    signal,
  });
  if (!response.ok || !response.body) {
    let detail = `Request failed: ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body?.detail === "string" && body.detail) detail = body.detail;
    } catch {
      // Non-JSON error bodies keep the status message.
    }
    throw new Error(detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalContent = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let splitAt = buffer.indexOf("\n");
    while (splitAt >= 0) {
      const line = buffer.slice(0, splitAt).trim();
      buffer = buffer.slice(splitAt + 1);
      splitAt = buffer.indexOf("\n");
      if (!line.startsWith("data:")) continue;
      try {
        const frame = JSON.parse(line.slice(5).trim()) as {
          event?: string;
          payload?: { content?: string; detail?: string };
        };
        if (frame.event === "thinking" && frame.payload?.content) {
          events.onThinking?.(frame.payload.content);
        } else if (frame.event === "content" && frame.payload?.content) {
          finalContent = frame.payload.content;
        } else if (frame.event === "error") {
          throw new Error(frame.payload?.detail || "Partner stream error.");
        }
      } catch (error) {
        if (error instanceof SyntaxError) continue;
        throw error;
      }
    }
  }
  return finalContent;
}

/** HEAD-check the public download surface so the panel only links working files. */
export async function verifyTakehomeDownload(path: string): Promise<string | null> {
  // Partner agents report the path relative to their workspace user tree;
  // the public surface wants it below the workspace root, so try the bare
  // path, the `workspace/`-prefixed shape and the stripped one.
  const candidates = [
    path,
    path.startsWith("workspace/") ? path.slice("workspace/".length) : `workspace/${path}`,
  ];
  const unique = [...new Set(candidates)];
  for (const candidate of unique) {
    try {
      const response = await fetch(apiUrl(`/files/outputs/${candidate}`), {
        method: "HEAD",
        credentials: "include",
      });
      if (response.ok) return candidate;
    } catch {
      // Try the next shape; a network miss falls through to "not ready".
    }
  }
  return null;
}
