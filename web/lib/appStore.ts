import { create } from "zustand";

export type DataStatus = "fresh" | "stale" | "error" | "unknown";

interface AppState {
  currentDate: string;
  latestDataDate: string;
  selectedStrategyId: string;
  selectedStrategyVersion: string;
  dataStatus: DataStatus;
  
  setCurrentDate: (date: string) => void;
  setLatestDataDate: (date: string) => void;
  setSelectedStrategy: (id: string, version: string) => void;
  setDataStatus: (status: DataStatus) => void;
}

export const useAppStore = create<AppState>((set) => ({
  currentDate: "—",
  latestDataDate: "—",
  selectedStrategyId: "illiquidity",
  selectedStrategyVersion: "v3.1",
  dataStatus: "unknown",

  setCurrentDate: (currentDate) => set({ currentDate }),
  setLatestDataDate: (latestDataDate) => set({ latestDataDate }),
  setSelectedStrategy: (selectedStrategyId, selectedStrategyVersion) =>
    set({ selectedStrategyId, selectedStrategyVersion }),
  setDataStatus: (dataStatus) => set({ dataStatus }),
}));
