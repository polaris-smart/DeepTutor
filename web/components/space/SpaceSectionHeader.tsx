"use client";

import { type ReactNode } from "react";
import { type LucideIcon } from "lucide-react";
import { PageHeader } from "@/components/layout/FeaturePage";

interface SpaceSectionHeaderProps {
  title: string;
  description: string;
  /** 悦学保留: 组卷/题库等 K12 页头仍传 lucide 图标（上游 PageHeader 无此槽）。 */
  icon?: LucideIcon;
  action?: ReactNode;
  meta?: ReactNode;
  level?: 1 | 2;
}

export default function SpaceSectionHeader({
  title,
  description,
  icon: _icon,
  action,
  meta,
  level,
}: SpaceSectionHeaderProps) {
  return (
    <PageHeader title={title} description={description} action={action} meta={meta} level={level} />
  );
}
