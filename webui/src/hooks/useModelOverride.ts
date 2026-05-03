import { useCallback, useEffect, useState } from "react";

import { useClient } from "@/providers/ClientProvider";

interface OverrideState {
  override: string | null;
  setOverride: (modelOrEmpty: string) => void;
}

/**
 * Tracks the per-chat ``model_override`` and exposes a setter that writes it
 * through the WebSocket ``set_model`` envelope.
 *
 * Hydration is **prop-driven**: callers pass the ``initialOverride`` they
 * already have on hand (``ChatSummary.modelOverride``, populated by
 * ``listSessions``). That field is server-derived from the same
 * ``Session.metadata['model_override']`` source the message-history endpoint
 * surfaces, so the user-visible behaviour matches a fetch-based hydration
 * without forcing a second round-trip — and without making this hook a second
 * consumer of ``/api/sessions/<key>/messages`` that conflicts with existing
 * test mocks of that endpoint.
 *
 * Subsequent writes are local-state changes plus a ``client.setModel`` call;
 * the server confirms via the ``model_set`` event from the channel handler.
 */
export function useModelOverride(
  chatId: string | null,
  initialOverride: string | null = null,
): OverrideState {
  const { client } = useClient();
  const [override, setOverrideState] = useState<string | null>(initialOverride);

  // Re-seed the local state whenever the active chat changes, so switching
  // away and back picks up the freshly-loaded session row's override (which
  // may have changed via the gateway on another tab/client).
  useEffect(() => {
    setOverrideState(initialOverride);
  }, [chatId, initialOverride]);

  const setOverride = useCallback(
    (modelOrEmpty: string) => {
      if (!chatId) return;
      // Optimistic update — the server confirms via the ``model_set`` event
      // emitted by the channel handler.
      setOverrideState(modelOrEmpty || null);
      client.setModel(chatId, modelOrEmpty);
    },
    [chatId, client],
  );

  return { override, setOverride };
}
