"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { MathBlockConfig } from "./mathBlockAdapter";

/**
 * YuEdu fork: Desmos 函数图像 block。
 *
 * 嵌入 Desmos 在线图形计算器（iframe embed，无需 API key，老悦学验证过的模式），
 * 并把 config.expressions 展示为可读文本。iframe 加载失败时降级为提示 + 表达式清单，
 * 不白屏。
 *
 * 注：任务书提到「script 注入 desmos api」，但 Desmos JS API 需 apiKey；老悦学
 * 验证过的是 iframe embed，此处沿用以保证开箱可用（见交付日志）。
 */
export default function DesmosBlock({ config }: { config: MathBlockConfig }) {
  const { t } = useTranslation();
  const [failed, setFailed] = useState(false);
  const expressions =
    config.expressions.length > 0 ? config.expressions : [config.expression];
  const src = "https://www.desmos.com/calculator?embed";

  return (
    <div className="my-4 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-4 shadow-sm">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h3 className="text-base font-bold text-[var(--foreground)]">
          {config.title || t("Desmos graph")}
        </h3>
        {expressions[0] && (
          <code className="text-sm text-[var(--primary)]">
            {expressions[0]}
          </code>
        )}
      </div>
      {config.description && (
        <p className="mb-3 text-sm text-[var(--muted-foreground)]">
          {config.description}
        </p>
      )}
      {failed ? (
        <div className="flex h-[420px] flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-[var(--border)] text-sm text-[var(--muted-foreground)]">
          <span>{t("Desmos failed to load. Check your network.")}</span>
          {expressions.length > 0 && (
            <pre className="max-w-full overflow-x-auto whitespace-pre-wrap rounded bg-[var(--background)] px-3 py-2 font-mono text-xs">
              {expressions.join("\n")}
            </pre>
          )}
        </div>
      ) : (
        <iframe
          title={config.title || "Desmos"}
          src={src}
          className="h-[420px] w-full rounded-lg border-0"
          loading="lazy"
          allowFullScreen
          onError={() => setFailed(true)}
        />
      )}
      <p className="mt-2 text-xs text-[var(--muted-foreground)]">
        {config.hint ||
          t("Drag the curve and add expressions to observe function properties.")}
      </p>
    </div>
  );
}
