import { LEARNING_HUB } from '@/lib/learning-routes'
import {
  BookOpen,
  Bot,
  ClipboardList,
  FileText,
  GraduationCap,
  HeartHandshake,
  House,
  LayoutGrid,
  ListTodo,
  PenLine,
  Settings,
  Users,
  type LucideIcon,
} from 'lucide-react'

import type { Capability } from '@/lib/capability-routes'

/**
 * Roles the backend issues in AuthStatus (`/api/auth/status`). "user" is the
 * signed-in account with no role assigned yet; the empty string covers the
 * unauthenticated / status-pending case. (悦学 role-gating, kept on v1.6.14.)
 */
export type NavRole = "admin" | "teacher" | "student" | "parent" | "user";

export interface NavEntry {
  href: string
  label: string
  icon: LucideIcon
  tooltipKey?: string
  defaultCollapsed?: boolean
  /** Model capability this feature needs; locked when the user lacks it. */
  requires?: Capability
  /**
   * Learning-account surfaces that may see this entry. Absent means the
   * entry is hidden whenever a learning policy is in effect (#1222).
   */
  surfaces?: readonly string[]
  /**
   * Roles this entry is *visible* to. Omit it for an entry every role sees.
   * Visibility whitelist only — routes stay reachable (悦学 role-gating).
   */
  roles?: readonly NavRole[]
  /** Remains available when a learning policy redacts the workspace. */
  alwaysAvailableToLearningAccounts?: boolean
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
 * Staff keep the generic home; learner roles land on the Learning Space.
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
    href: '/chat',
    label: 'Home',
    icon: House,
    tooltipKey: 'Home tooltip',
    requires: 'llm',
    surfaces: ['chat'],
  },
  {
    href: '/partners',
    label: 'Partners',
    icon: HeartHandshake,
    tooltipKey: 'Partners tooltip',
    requires: 'llm',
    roles: ["teacher", "admin"],
  },
  {
    href: LEARNING_HUB,
    label: 'Personalized Learning',
    icon: GraduationCap,
    tooltipKey: 'One tutor, your own way to learn.',
    surfaces: ['reading', 'chat', 'books'],
  },
  {
    // The learner hub: its APIs (daily plan, assignments, courses) are exactly
    // the surfaces a learning policy grants, so it stays visible there.
    href: '/space',
    label: 'Learning Space',
    icon: LayoutGrid,
    tooltipKey: 'Space tooltip',
    alwaysAvailableToLearningAccounts: true,
  },
  {
    href: '/kanban',
    label: 'Task Board',
    icon: ListTodo,
    tooltipKey: 'kanban.intro',
    surfaces: ['chat'],
  },
  {
    href: '/co-writer',
    label: 'Co-Writer',
    icon: PenLine,
    tooltipKey: 'Co-Writer tooltip',
    requires: 'llm',
    roles: ["teacher", "admin"],
    defaultCollapsed: true,
  },
  {
    // My Agents is its own top-level feature (pulled out of the Learning
    // Space): connect a live local Claude Code / Codex to consult in chat,
    // and manage imported agent conversations. Producer-side tool → staff only.
    href: '/agents',
    label: 'My Agents',
    icon: Bot,
    tooltipKey: 'Agents tooltip',
    roles: ["teacher", "admin"],
    defaultCollapsed: true,
  },
  {
    // 教师作业闭环: 布置/统计是教师侧工作台，学生只在 Learning Space 的
    // 作业卡里作答，所以这一项只进教师/管理员全量导航，不进 learner 集。
    href: '/assignments',
    label: 'Assignments',
    icon: ClipboardList,
    tooltipKey: 'Assignments tooltip',
    roles: ["teacher", "admin"],
  },
  {
    href: '/courses',
    label: 'Courses',
    icon: BookOpen,
    tooltipKey: 'Courses tooltip',
    requires: 'llm',
    roles: ["teacher", "admin"],
  },
  {
    href: '/space/questions',
    label: 'Exam Paper',
    icon: FileText,
    tooltipKey: 'Exam Paper tooltip',
    requires: 'llm',
    roles: ["teacher", "admin"],
  },
  {
    // 家长专属：家庭学情视图（我的孩子）。K12 家长是 sidebar 上的 learner
    // 视图，/family 页面此前没有任何入口可达，这里补上唯一的导航挂点。
    href: '/family',
    label: 'My Children',
    icon: Users,
    tooltipKey: 'Family tooltip',
    roles: ['parent'],
  },
]

export const SECONDARY_NAV: NavEntry[] = [
  {
    // Memory is its own top-level console: staff-only curation surface.
    href: '/memory',
    label: 'Memory',
    icon: Settings,
    tooltipKey: 'Memory tooltip',
    roles: ["teacher", "admin"],
  },
  {
    // Knowledge Center: a console for managing KBs and retrieval engines —
    // shared admin infrastructure, no per-user model grant needed.
    href: '/knowledge-bases',
    label: 'Knowledge Center',
    icon: BookOpen,
    tooltipKey: 'Knowledge tooltip',
    roles: ["teacher", "admin"],
  },
  {
    href: '/settings',
    label: 'Settings',
    icon: Settings,
    alwaysAvailableToLearningAccounts: true,
  },
]

export const DEFAULT_COLLAPSED_NAV = PRIMARY_NAV.filter(entry => entry.defaultCollapsed).map(
  entry => entry.href
)

export const PRIMARY_NAV_HREFS = PRIMARY_NAV.map(entry => entry.href)

export const NAV_BY_HREF = new Map(
  [...PRIMARY_NAV, ...SECONDARY_NAV].map(entry => [entry.href, entry])
)

export function isNavActive(pathname: string, href: string) {
  if (href === "/space") {
    return (
      (pathname === "/space" || pathname.startsWith("/space/")) &&
      !pathname.startsWith("/mastery")
    );
  }
  return pathname === href || pathname.startsWith(`${href}/`)
}

/**
 * A learning policy hides every feature the account cannot call. Settings
 * stays, because the redacted page (theme, language) is still theirs.
 * ``allowedSurfaces === null`` means no policy: the full sidebar.
 * (Upstream v1.6.14 semantics, verbatim.)
 */
export function navVisibleUnderPolicy(
  href: string,
  allowedSurfaces: readonly string[] | null,
): boolean {
  if (allowedSurfaces === null) return true
  if (href === '/settings') return true
  const surfaces = NAV_BY_HREF.get(href)?.surfaces
  if (!surfaces?.length) return false
  return surfaces.some(surface => allowedSurfaces.includes(surface))
}

/**
 * 悦学兼容门（SidebarShell / learning-policy-shell.test 消费）：按条目对象判
 * 学习策略可见性。语义对齐上游 navVisibleUnderPolicy（归一化 allowedSurfaces），
 * 另保留 alwaysAvailableToLearningAccounts 豁免（/space、/settings）。
 */
export function isNavEntryAllowedForLearningPolicy(
  entry: NavEntry | undefined,
  learningPolicy: { allowedSurfaces?: readonly string[] } | null | undefined,
): boolean {
  if (!entry) return false;
  const allowedSurfaces = learningPolicy?.allowedSurfaces;
  if (!allowedSurfaces) return true;
  if (entry.alwaysAvailableToLearningAccounts) return true;
  const surfaces = entry.surfaces;
  if (!surfaces?.length) return false;
  return surfaces.some(surface => allowedSurfaces.includes(surface));
}
