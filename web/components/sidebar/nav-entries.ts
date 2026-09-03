import {
  BookOpen,
  BookText,
  Bot,
  Brain,
  HeartHandshake,
  House,
  LayoutGrid,
  Library,
  PenLine,
  Route,
  Settings,
  type LucideIcon,
} from "lucide-react";

import type { Capability } from "@/lib/capability-routes";

/**
 * Roles the backend issues in AuthStatus (`/api/auth/status`). "user" is the
 * signed-in account with no role assigned yet; the empty string covers the
 * unauthenticated / status-pending case.
 */
export type NavRole = "admin" | "teacher" | "student" | "parent" | "user";

export interface NavEntry {
  href: string;
  label: string;
  icon: LucideIcon;
  tooltipKey?: string;
  /** Model capability this feature needs; locked when the user lacks it. */
  requires?: Capability;
  /**
   * Roles this entry is *visible* to. Omit it for an entry every role sees.
   *
   * This is the visibility whitelist only — routes and pages stay reachable
   * (no gating happens here), so a learner who lands on a hidden URL still
   * gets the page, exactly like a folded feature. Producer-side tools
   * (Partners, Agents, Co-Writer) and admin consoles (Memory, Knowledge
   * Center) list ["teacher", "admin"] so the learner sidebar stays on task.
   */
  roles?: readonly NavRole[];
}

/** Roles that see every nav entry — the staff view. Everyone else (student,
 *  parent, "user", unauthenticated) gets the learner set. */
const FULL_NAV_ROLES: ReadonlySet<string> = new Set(["teacher", "admin"]);

/** Whether ``role`` may see ``entry``. Unknown/empty roles read as a learner
 *  (student view), which also keeps the first pre-auth render deterministic. */
export function isNavEntryVisible(entry: NavEntry, role: string): boolean {
  if (!entry.roles) return true;
  if (FULL_NAV_ROLES.has(role)) return true;
  return entry.roles.includes(role as NavRole);
}

/** Primary nav hrefs ``role`` may see, in shipped order. */
export function primaryNavHrefsFor(role: string): string[] {
  return PRIMARY_NAV.filter((entry) => isNavEntryVisible(entry, role)).map(
    (entry) => entry.href,
  );
}

/** Secondary nav entries ``role`` may see, in shipped order. */
export function secondaryNavFor(role: string): NavEntry[] {
  return SECONDARY_NAV.filter((entry) => isNavEntryVisible(entry, role));
}

/** The hub feature learner-facing roles land on after signing in. Also the
 *  href ``isNavActive`` special-cases, so it stays defined in one place. */
const LEARNING_SPACE_HREF = "/space";

/**
 * Where ``role`` lands when it signs in without an explicit return path.
 *
 * Staff keep the generic home. Learner-facing roles have their nav pruned to
 * the learning flow, so send them straight to the Learning Space — the hub of
 * the nav they actually see — instead of the chat home they'd have to leave.
 * Falls back to the first entry their nav still shows if the Learning Space
 * ever leaves the learner set.
 */
export function landingHrefFor(role: string): string {
  if (FULL_NAV_ROLES.has(role)) return "/";
  const hrefs = primaryNavHrefsFor(role);
  return hrefs.includes(LEARNING_SPACE_HREF)
    ? LEARNING_SPACE_HREF
    : (hrefs[0] ?? "/");
}

/**
 * The workspace features, in the order they ship in.
 *
 * This is the *default* arrangement, not the rendered one — a learner can
 * reorder these and fold the ones they don't use into "More"
 * (``lib/sidebar-layout.ts``). Adding an entry here places it for everyone,
 * including people who have already arranged their sidebar: it arrives next to
 * the neighbour it follows below rather than at the bottom of their list.
 */
export const PRIMARY_NAV: NavEntry[] = [
  {
    href: "/chat",
    label: "Home",
    icon: House,
    tooltipKey: "Home tooltip",
    requires: "llm",
  },
  {
    href: "/partners",
    label: "Partners",
    icon: HeartHandshake,
    tooltipKey: "Partners tooltip",
    requires: "llm",
    roles: ["teacher", "admin"],
  },
  {
    // My Agents is its own top-level feature (pulled out of the Learning
    // Space): connect a live local Claude Code / Codex to consult in chat,
    // and manage imported agent conversations. Ungated — managing connections
    // and imports needs no per-user model grant.
    href: "/agents",
    label: "My Agents",
    icon: Bot,
    tooltipKey: "Agents tooltip",
    roles: ["teacher", "admin"],
  },
  {
    href: "/co-writer",
    label: "Co-Writer",
    icon: PenLine,
    tooltipKey: "Co-Writer tooltip",
    requires: "llm",
    roles: ["teacher", "admin"],
  },
  {
    href: "/books",
    label: "Book",
    icon: Library,
    tooltipKey: "Book tooltip",
    requires: "llm",
  },
  // Courses nav entry temporarily hidden pending further product work.
  // The route and its data are untouched — only this entry point is gone.
  {
    href: "/mastery",
    label: "Mastery Path",
    icon: Route,
    tooltipKey: "Learn through a living mastery map",
    requires: "llm",
  },
  {
    href: "/reading",
    label: "Immersive Reading",
    icon: BookText,
    tooltipKey: "Immersive Reading tooltip",
    requires: "llm",
  },
  {
    href: "/space",
    label: "Learning Space",
    icon: LayoutGrid,
    tooltipKey: "Space tooltip",
  },
];

/** Consoles that sit under the chat history. Not arrangeable: Settings has to
 *  stay findable, and a console nobody folds away is one less thing to explain. */
export const SECONDARY_NAV: NavEntry[] = [
  {
    // Memory is its own top-level console (pulled out of the Learning Space):
    // a place to inspect and curate the tutor's long-term memory, not a daily
    // workspace. Never gated — memory has no per-user model requirement.
    href: "/memory",
    label: "Memory",
    icon: Brain,
    tooltipKey: "Memory tooltip",
    roles: ["teacher", "admin"],
  },
  {
    // Knowledge Center sits just above Settings: it's a console for managing
    // KBs and retrieval engines, not a daily workspace. Never gated — embedding
    // / search are shared admin infrastructure, no per-user model grant needed.
    href: "/knowledge-bases",
    label: "Knowledge Center",
    icon: BookOpen,
    tooltipKey: "Knowledge tooltip",
    roles: ["teacher", "admin"],
  },
  { href: "/settings", label: "Settings", icon: Settings },
];

/** Every primary href in shipped order, unfiltered by role. Rendering goes
 *  through ``primaryNavHrefsFor`` so hidden roles never enter the layout. */
export const PRIMARY_NAV_HREFS = PRIMARY_NAV.map((entry) => entry.href);

export const NAV_BY_HREF = new Map(
  [...PRIMARY_NAV, ...SECONDARY_NAV].map((entry) => [entry.href, entry]),
);

export function isNavActive(pathname: string, href: string) {
  if (href === "/space") {
    return (
      (pathname === "/space" || pathname.startsWith("/space/")) &&
      !pathname.startsWith("/mastery")
    );
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}
