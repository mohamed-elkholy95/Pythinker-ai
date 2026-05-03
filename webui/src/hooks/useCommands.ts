import { useEffect, useState } from "react";

import { fetchCommands, type CommandRow } from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";

interface CommandsState {
  commands: CommandRow[];
  loading: boolean;
}

const EMPTY: CommandsState = { commands: [], loading: false };

/**
 * One-shot fetch of the built-in slash-command list. The list is static for
 * the lifetime of the gateway process so the hook does not refetch unless
 * remounted (e.g. tab unload + reload).
 */
export function useCommands(): CommandsState {
  const { token } = useClient();
  const [state, setState] = useState<CommandsState>({ ...EMPTY, loading: true });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const commands = await fetchCommands(token);
        if (cancelled) return;
        setState({ commands, loading: false });
      } catch {
        if (cancelled) return;
        setState({ commands: [], loading: false });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  return state;
}
