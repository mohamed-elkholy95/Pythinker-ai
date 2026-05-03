import { useEffect, useState } from "react";

import { fetchAvailableModels, type ModelRow } from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";

interface ModelsState {
  models: ModelRow[];
  defaultModel: string | null;
  loading: boolean;
}

const EMPTY: ModelsState = { models: [], defaultModel: null, loading: false };

/**
 * Loads the configured model list from ``/api/models`` once on mount and
 * exposes the rows plus the configured default. Errors are swallowed and
 * surface as the empty state — this powers a cosmetic switcher, not a
 * load-bearing UI element.
 */
export function useAvailableModels(): ModelsState {
  const { token } = useClient();
  const [state, setState] = useState<ModelsState>({ ...EMPTY, loading: true });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const models = await fetchAvailableModels(token);
        if (cancelled) return;
        const defaultRow = models.find((m) => m.is_default);
        setState({
          models,
          defaultModel: defaultRow?.name ?? null,
          loading: false,
        });
      } catch {
        if (cancelled) return;
        setState({ ...EMPTY, loading: false });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  return state;
}
