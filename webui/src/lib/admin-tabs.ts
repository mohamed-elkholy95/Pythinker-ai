/** Admin console tab ids — shared by Sidebar shortcuts and AdminDashboard. */
export type AdminTabId =
  | "overview"
  | "channels"
  | "sessions"
  | "usage"
  | "cron"
  | "agents"
  | "skills"
  | "dreams"
  | "config"
  | "appearance"
  | "infrastructure"
  | "debug"
  | "logs";

const TAB_LOOKUP: Record<AdminTabId, { group: string; label: string }> = {
  overview: { group: "Control", label: "Overview" },
  channels: { group: "Control", label: "Channels" },
  sessions: { group: "Control", label: "Sessions" },
  usage: { group: "Control", label: "Usage" },
  cron: { group: "Control", label: "Cron" },
  agents: { group: "Agent", label: "Agents" },
  skills: { group: "Agent", label: "Skills" },
  dreams: { group: "Agent", label: "Dreams" },
  config: { group: "Settings", label: "Config" },
  appearance: { group: "Settings", label: "Appearance" },
  infrastructure: { group: "Settings", label: "Infrastructure" },
  debug: { group: "Settings", label: "Debug" },
  logs: { group: "Settings", label: "Logs" },
};

export function adminTabMeta(tabId: AdminTabId): { group: string; label: string } {
  return TAB_LOOKUP[tabId];
}
