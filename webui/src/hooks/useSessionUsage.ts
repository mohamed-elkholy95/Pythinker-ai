import { useEffect, useState } from "react";

import { useClient } from "@/providers/ClientProvider";
import { fetchSessionUsage } from "@/lib/api";

interface UsageState {
  used: number;
  limit: number;
  loading: boolean;
}

const ZERO: UsageState = { used: 0, limit: 0, loading: false };

/**
 * Polls token usage for *chatId* on mount and on every ``stream_end`` event
 * (i.e. after each completed turn). Returns ``ZERO`` when chatId is null.
 *
 * Errors are swallowed and surface as a continuing zero-state — this is a
 * cosmetic pill, not a load-bearing UI element. Server-side errors (404 on
 * a deleted session, 503 on misconfig) shouldn't make the header explode.
 */
export function useSessionUsage(chatId: string | null): UsageState {
  const { client, token } = useClient();
  const [state, setState] = useState<UsageState>(ZERO);

  useEffect(() => {
    if (!chatId) {
      setState(ZERO);
      return;
    }
    let cancelled = false;
    const refresh = async () => {
      setState((s) => ({ ...s, loading: true }));
      try {
        const data = await fetchSessionUsage(token, `websocket:${chatId}`);
        if (cancelled) return;
        setState({ used: data.used, limit: data.limit, loading: false });
      } catch {
        if (cancelled) return;
        setState(ZERO);
      }
    };
    void refresh();
    const unsub = client.onChat(chatId, (ev: { event: string }) => {
      if (ev.event === "stream_end") void refresh();
    });
    return () => {
      cancelled = true;
      unsub();
    };
  }, [chatId, client, token]);

  return state;
}
