import type { IconName } from "../components/Icon";
export interface NavigationItem {
  label: string;
  path: string;
  section: "Workspace" | "System";
  icon: IconName;
}
export const navigationItems: NavigationItem[] = [
  { label: "Inbox", path: "/inbox", section: "Workspace", icon: "alert" },
  { label: "Discover", path: "/discover", section: "Workspace", icon: "spark" },
  { label: "Portfolio", path: "/portfolio", section: "Workspace", icon: "building" },
  { label: "Research", path: "/research", section: "Workspace", icon: "research" },
  { label: "Sources", path: "/system/sources", section: "System", icon: "check" },
  { label: "Runs", path: "/system/runs", section: "System", icon: "activity" },
  { label: "Evaluation", path: "/system/evaluate", section: "System", icon: "evaluate" },
  { label: "Replay", path: "/system/replay", section: "System", icon: "history" },
  { label: "Settings", path: "/system/settings", section: "System", icon: "settings" },
];
