import { useCallback, useEffect, useRef, useState } from "react";

import { searchSessions } from "@/lib/api";
import type { SearchHit } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

interface SearchState {
  query: string;
  hits: SearchHit[];
  loading: boolean;
  hasMore: boolean;
  offset: number;
}

const DEBOUNCE_MS = 200;
const PAGE_SIZE = 50;

/** Debounced cross-chat search. The 200ms debounce keeps us from firing one
 * request per keystroke; an AbortController cancels any in-flight request
 * when the query changes so a slow earlier search can't overwrite a fresher
 * result. A monotonic request id guards against the (rare) case where the
 * abort signal fires after the response body has already resolved. */
export function useSessionSearch(): {
  query: string;
  setQuery: (q: string) => void;
  hits: SearchHit[];
  loading: boolean;
  hasMore: boolean;
  loadMore: () => void;
} {
  const { token } = useClient();
  const [state, setState] = useState<SearchState>({
    query: "",
    hits: [],
    loading: false,
    hasMore: false,
    offset: 0,
  });
  const reqId = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const setQuery = useCallback((q: string) => {
    setState((s) => ({ ...s, query: q, offset: 0 }));
  }, []);

  // Debounced fetch of page 1 whenever the query changes.
  useEffect(() => {
    if (!state.query) {
      abortRef.current?.abort();
      abortRef.current = null;
      setState((s) => ({ ...s, hits: [], hasMore: false, loading: false }));
      return;
    }
    const id = ++reqId.current;
    const timer = window.setTimeout(() => {
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setState((s) => ({ ...s, loading: true }));
      void (async () => {
        try {
          const out = await searchSessions(token, state.query, {
            offset: 0,
            limit: PAGE_SIZE,
            signal: ctrl.signal,
          });
          if (id !== reqId.current) return;
          setState({
            query: state.query,
            hits: out.results,
            loading: false,
            hasMore: out.hasMore,
            offset: out.results.length,
          });
        } catch {
          if (id !== reqId.current) return;
          setState((s) => ({ ...s, loading: false, hasMore: false }));
        }
      })();
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      abortRef.current?.abort();
    };
  }, [state.query, token]);

  const loadMore = useCallback(() => {
    if (state.loading || !state.hasMore || !state.query) return;
    const id = ++reqId.current;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setState((s) => ({ ...s, loading: true }));
    void (async () => {
      try {
        const out = await searchSessions(token, state.query, {
          offset: state.offset,
          limit: PAGE_SIZE,
          signal: ctrl.signal,
        });
        if (id !== reqId.current) return;
        setState((s) => ({
          ...s,
          hits: [...s.hits, ...out.results],
          loading: false,
          hasMore: out.hasMore,
          offset: s.offset + out.results.length,
        }));
      } catch {
        if (id !== reqId.current) return;
        setState((s) => ({ ...s, loading: false }));
      }
    })();
  }, [state.loading, state.hasMore, state.query, state.offset, token]);

  return {
    query: state.query,
    setQuery,
    hits: state.hits,
    loading: state.loading,
    hasMore: state.hasMore,
    loadMore,
  };
}
