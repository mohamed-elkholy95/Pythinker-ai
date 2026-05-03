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
  overview: { group: "Monitor", label: "Overview" },
  usage: { group: "Monitor", label: "Usage" },
  logs: { group: "Monitor", label: "Logs" },
  channels: { group: "Workspace", label: "Channels" },
  sessions: { group: "Workspace", label: "Sessions" },
  agents: { group: "Workspace", label: "Agents" },
  skills: { group: "Workspace", label: "Skills" },
  dreams: { group: "Workspace", label: "Dreams" },
  cron: { group: "Workspace", label: "Cron" },
  config: { group: "System", label: "Config" },
  appearance: { group: "System", label: "Appearance" },
  infrastructure: { group: "System", label: "Infrastructure" },
  debug: { group: "System", label: "Debug" },
};

export function adminTabMeta(tabId: AdminTabId): { group: string; label: string } {
  return TAB_LOOKUP[tabId];
}
