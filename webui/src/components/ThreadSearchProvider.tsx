import { createContext, useContext, type ReactNode } from "react";

import {
  type InChatSearchState,
  useInChatSearch,
} from "@/hooks/useInChatSearch";

const Ctx = createContext<InChatSearchState | null>(null);

export function ThreadSearchProvider({ children }: { children: ReactNode }) {
  const value = useInChatSearch();
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/**
 * ``null`` when called outside ``<ThreadSearchProvider>`` — the hook is
 * intentionally optional so message bubbles render fine on the welcome
 * screen and inside tests that don't mount the provider.
 */
export function useThreadSearch(): InChatSearchState | null {
  return useContext(Ctx);
}
