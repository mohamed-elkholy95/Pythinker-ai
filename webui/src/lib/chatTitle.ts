/**
 * Sidebar-display cleanup for chat titles.
 *
 * The backend now strips ``<think>...</think>`` blocks before persisting,
 * but legacy chats from before that fix landed still have the raw blocks
 * (often truncated mid-thought because the title cap landed before the
 * closing tag). This helper covers both well-formed and unterminated
 * blocks so old chats render cleanly without a one-shot migration.
 */

const THINK_BLOCK = /<think>[\s\S]*?<\/think>/g;
const THINK_OPEN_TO_END = /<think>[\s\S]*$/;

export function cleanChatTitle(raw: string | undefined | null): string {
  if (!raw) return "";
  // Drop well-formed blocks first so a "<think>...</think>foo" title
  // collapses to "foo" instead of being truncated to nothing.
  let cleaned = raw.replace(THINK_BLOCK, "");
  // Drop unterminated tail (legacy truncated titles like "<think>The user
  // wants a concise chat tit" — no closing tag, no visible answer).
  cleaned = cleaned.replace(THINK_OPEN_TO_END, "");
  return cleaned.trim();
}
