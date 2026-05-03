import { useCallback, useMemo, useRef, useState } from "react";

export interface InChatSearchState {
  query: string;
  setQuery: (q: string) => void;
  /** Stable, ordered list of match ids registered by every bubble in the
   * thread (one per ``<mark>`` rendered by ``HighlightedText``). */
  matchIds: string[];
  /** Match id currently focused; ``null`` when ``query`` is empty or no
   * match has been selected yet. */
  activeMatchId: string | null;
  next: () => void;
  prev: () => void;
  /** Bubbles call this in a layout effect to publish their match count for
   * a given query. Returns an unregister function. */
  registerMatches: (bubbleId: string, ids: string[]) => () => void;
  reset: () => void;
}

export function useInChatSearch(): InChatSearchState {
  const [query, setQuery] = useState("");
  const [activeMatchId, setActiveMatchId] = useState<string | null>(null);
  // bubbleId -> ordered match ids; rebuilt into a flat list whenever any
  // bubble re-registers.
  const perBubble = useRef(new Map<string, string[]>());
  const [matchIds, setMatchIds] = useState<string[]>([]);

  const recompute = useCallback(() => {
    const flat: string[] = [];
    for (const ids of perBubble.current.values()) flat.push(...ids);
    setMatchIds(flat);
    setActiveMatchId((prev) => {
      if (flat.length === 0) return null;
      if (prev && flat.includes(prev)) return prev;
      return flat[0];
    });
  }, []);

  const registerMatches = useCallback(
    (bubbleId: string, ids: string[]) => {
      perBubble.current.set(bubbleId, ids);
      recompute();
      return () => {
        perBubble.current.delete(bubbleId);
        recompute();
      };
    },
    [recompute],
  );

  const next = useCallback(() => {
    setActiveMatchId((cur) => {
      if (matchIds.length === 0) return null;
      if (cur === null) return matchIds[0];
      const i = matchIds.indexOf(cur);
      return matchIds[(i + 1) % matchIds.length];
    });
  }, [matchIds]);

  const prev = useCallback(() => {
    setActiveMatchId((cur) => {
      if (matchIds.length === 0) return null;
      if (cur === null) return matchIds[matchIds.length - 1];
      const i = matchIds.indexOf(cur);
      return matchIds[(i - 1 + matchIds.length) % matchIds.length];
    });
  }, [matchIds]);

  const reset = useCallback(() => {
    setQuery("");
    setActiveMatchId(null);
    perBubble.current.clear();
    setMatchIds([]);
  }, []);

  return useMemo(
    () => ({
      query,
      setQuery,
      matchIds,
      activeMatchId,
      next,
      prev,
      registerMatches,
      reset,
    }),
    [query, matchIds, activeMatchId, next, prev, registerMatches, reset],
  );
}
