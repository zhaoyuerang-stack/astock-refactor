import { create } from "zustand";
import { persist } from "zustand/middleware";

export const SIDEBAR_DEFAULT = 220;
export const SIDEBAR_MIN = 180;
export const SIDEBAR_MAX = 360;
export const AGENT_DEFAULT = 320;
export const AGENT_MIN = 280;
export const AGENT_MAX = 560;

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

interface LayoutState {
  sidebarWidth: number;
  agentWidth: number;
  rightPanelWidth: number; // alias for agentWidth requested by requirements doc
  sidebarCollapsed: boolean;
  agentCollapsed: boolean;
  theme: "dark" | "light";
  
  setSidebarWidth: (w: number) => void;
  setAgentWidth: (w: number) => void;
  setRightPanelWidth: (w: number) => void;
  resetSidebar: () => void;
  resetAgent: () => void;
  toggleSidebar: () => void;
  toggleAgent: () => void;
  setTheme: (theme: "dark" | "light") => void;
}

export const useLayoutStore = create<LayoutState>()(
  persist(
    (set) => ({
      sidebarWidth: SIDEBAR_DEFAULT,
      agentWidth: AGENT_DEFAULT,
      rightPanelWidth: AGENT_DEFAULT,
      sidebarCollapsed: false,
      agentCollapsed: false,
      theme: "dark", // Default to dark terminal style

      setSidebarWidth: (w) => set({ sidebarWidth: clamp(w, SIDEBAR_MIN, SIDEBAR_MAX) }),
      setAgentWidth: (w) => {
        const val = clamp(w, AGENT_MIN, AGENT_MAX);
        set({ agentWidth: val, rightPanelWidth: val });
      },
      setRightPanelWidth: (w) => {
        const val = clamp(w, AGENT_MIN, AGENT_MAX);
        set({ agentWidth: val, rightPanelWidth: val });
      },
      resetSidebar: () => set({ sidebarWidth: SIDEBAR_DEFAULT }),
      resetAgent: () => set({ agentWidth: AGENT_DEFAULT, rightPanelWidth: AGENT_DEFAULT }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      toggleAgent: () => set((s) => ({ agentCollapsed: !s.agentCollapsed })),
      setTheme: (theme) => set({ theme }),
    }),
    {
      name: "quant-os-layout",
      skipHydration: true,
    },
  ),
);
