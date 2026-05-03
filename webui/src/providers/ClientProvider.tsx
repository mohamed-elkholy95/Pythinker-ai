import { createContext, useContext, type ReactNode } from "react";

import type { PythinkerClient } from "@/lib/pythinker-client";

interface ClientContextValue {
  client: PythinkerClient;
  token: string;
  modelName: string | null;
  /** Whether the gateway exposes a working transcription pipeline. Defaults
   * to ``false`` — the full T11 voice surface is deferred (the Python
   * ``websockets`` server can't handle the ``POST /api/transcribe`` route).
   * The composer reads this to render a disabled mic button so a future
   * enable lands without a UI rewrite. */
  voiceEnabled: boolean;
}

const ClientContext = createContext<ClientContextValue | null>(null);

export function ClientProvider({
  client,
  token,
  modelName = null,
  voiceEnabled = false,
  children,
}: {
  client: PythinkerClient;
  token: string;
  modelName?: string | null;
  voiceEnabled?: boolean;
  children: ReactNode;
}) {
  return (
    <ClientContext.Provider
      value={{ client, token, modelName, voiceEnabled }}
    >
      {children}
    </ClientContext.Provider>
  );
}

export function useClient(): ClientContextValue {
  const ctx = useContext(ClientContext);
  if (!ctx) {
    throw new Error("useClient must be used within a ClientProvider");
  }
  return ctx;
}

/** Non-throwing variant for components that need to render in standalone
 * tests (no ``ClientProvider`` in the tree) but still want to read context
 * when one is present. Returns ``null`` when unmounted from a provider. */
export function useOptionalClient(): ClientContextValue | null {
  return useContext(ClientContext);
}
