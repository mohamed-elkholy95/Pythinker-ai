/**
 * Extract every ``<think>...</think>`` block from a model output and return
 * the reasoning concatenated separately from the visible answer.
 *
 * Design notes:
 *   - We use ``[\s\S]*?`` rather than the ``s`` regex flag because
 *     compatibility with older WebKit/Safari is broader. They are
 *     functionally equivalent for this pattern.
 *   - We do NOT attempt to handle nested ``<think>`` tags. Empirically,
 *     none of the providers Pythinker integrates with emit nested blocks
 *     (DashScope, MiniMax, VolcEngine, Moonshot, etc. all wrap a single
 *     contiguous chain-of-thought). Don't add cleverness without evidence.
 *   - Unclosed tags fall through to ``visible`` unchanged so a partial
 *     stream doesn't visually flicker as the closing tag arrives.
 */
const THINK_RE = /<think>([\s\S]*?)<\/think>/g;

export interface ExtractedThink {
  reasoning: string;
  visible: string;
}

export function extractThinkBlocks(text: string): ExtractedThink {
  if (!text || !text.includes("<think>")) {
    return { reasoning: "", visible: text };
  }
  const blocks: string[] = [];
  const visible = text.replace(THINK_RE, (_match, inner) => {
    blocks.push(String(inner));
    // Leave a single space so that adjacent visible runs separated by a
    // think block don't accidentally weld together (e.g. ``A<think>x</think>B``
    // → ``A B`` rather than ``AB``). Callers that care about exact whitespace
    // can ``.trim()`` / collapse runs themselves.
    return " ";
  });
  if (blocks.length === 0) {
    // Unclosed tag — pass through unchanged so streaming doesn't flicker.
    return { reasoning: "", visible: text };
  }
  return {
    reasoning: blocks.join("\n\n"),
    // Collapse the leading/trailing whitespace introduced by stripping
    // tags, but leave internal whitespace alone so markdown renders OK.
    visible: visible.replace(/^\s+|\s+$/g, ""),
  };
}
