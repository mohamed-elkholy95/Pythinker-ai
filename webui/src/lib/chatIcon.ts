import {
  Bot,
  Code,
  Compass,
  FileText,
  Globe,
  Lightbulb,
  MessageCircle,
  MessageSquare,
  Sparkles,
  Wand2,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

/**
 * Deterministic per-chat icon + tint for the sidebar.
 *
 * The same chat key always resolves to the same icon and tint so the row
 * is visually stable across reloads and reorderings. Picks come from a
 * hand-curated set of conversational icons — we deliberately avoid
 * topic-classification heuristics (would need an LLM call per render and
 * would jitter as titles evolve mid-conversation).
 */
const ICON_SET: readonly LucideIcon[] = [
  MessageSquare,
  MessageCircle,
  Sparkles,
  Lightbulb,
  Wand2,
  Bot,
  FileText,
  Code,
  Globe,
  Compass,
];

// Six muted accent tints. Each pair is `bg | fg` Tailwind classes; tints
// are intentionally soft so the row stays readable under the accent
// background when the chat is active.
const TINT_SET: readonly { bg: string; fg: string }[] = [
  { bg: "bg-sky-500/12", fg: "text-sky-600 dark:text-sky-300" },
  { bg: "bg-emerald-500/12", fg: "text-emerald-600 dark:text-emerald-300" },
  { bg: "bg-amber-500/12", fg: "text-amber-600 dark:text-amber-300" },
  { bg: "bg-violet-500/12", fg: "text-violet-600 dark:text-violet-300" },
  { bg: "bg-rose-500/12", fg: "text-rose-600 dark:text-rose-300" },
  { bg: "bg-teal-500/12", fg: "text-teal-600 dark:text-teal-300" },
];

function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) {
    h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

export interface ChatIconStyle {
  Icon: LucideIcon;
  bgClass: string;
  fgClass: string;
}

export function chatIconFor(key: string): ChatIconStyle {
  const h = hashString(key || "fallback");
  const Icon = ICON_SET[h % ICON_SET.length];
  const tint = TINT_SET[h % TINT_SET.length];
  return { Icon, bgClass: tint.bg, fgClass: tint.fg };
}
