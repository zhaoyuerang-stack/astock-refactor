import { create } from "zustand";

export type WorkspaceMode = "ops" | "rd";

interface WorkspaceState {
  mode: WorkspaceMode;
  setMode: (mode: WorkspaceMode) => void;
}

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  mode: "ops",
  setMode: (mode) => set({ mode }),
}));
