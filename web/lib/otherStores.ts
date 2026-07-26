import { create } from "zustand";
import type { StrategyView, TradePlanView, TradeReadinessView, RiskReport, AuditView } from "./types";

// 1. Strategy Store
interface StrategyState {
  strategies: StrategyView[];
  selectedStrategy: StrategyView | null;
  isLoading: boolean;
  setStrategies: (strategies: StrategyView[]) => void;
  setSelectedStrategy: (strategy: StrategyView | null) => void;
  setIsLoading: (isLoading: boolean) => void;
}

export const useStrategyStore = create<StrategyState>((set) => ({
  strategies: [],
  selectedStrategy: null,
  isLoading: false,
  setStrategies: (strategies) => set({ strategies }),
  setSelectedStrategy: (selectedStrategy) => set({ selectedStrategy }),
  setIsLoading: (isLoading) => set({ isLoading }),
}));

// 2. Dashboard Store
interface DashboardState {
  paperPlan: TradePlanView | null;
  readiness: TradeReadinessView | null;
  isLoading: boolean;
  setPaperPlan: (plan: TradePlanView | null) => void;
  setReadiness: (readiness: TradeReadinessView | null) => void;
  setIsLoading: (isLoading: boolean) => void;
}

export const useDashboardStore = create<DashboardState>((set) => ({
  paperPlan: null,
  readiness: null,
  isLoading: false,
  setPaperPlan: (paperPlan) => set({ paperPlan }),
  setReadiness: (readiness) => set({ readiness }),
  setIsLoading: (isLoading) => set({ isLoading }),
}));

// 3. Audit Store
interface AuditState {
  auditLogs: any[];
  isLoading: boolean;
  setAuditLogs: (logs: any[]) => void;
  setIsLoading: (isLoading: boolean) => void;
}

export const useAuditStore = create<AuditState>((set) => ({
  auditLogs: [],
  isLoading: false,
  setAuditLogs: (auditLogs) => set({ auditLogs }),
  setIsLoading: (isLoading) => set({ isLoading }),
}));

// 4. Risk Store
interface RiskState {
  riskReport: RiskReport | null;
  isLoading: boolean;
  setRiskReport: (report: RiskReport | null) => void;
  setIsLoading: (isLoading: boolean) => void;
}

export const useRiskStore = create<RiskState>((set) => ({
  riskReport: null,
  isLoading: false,
  setRiskReport: (riskReport) => set({ riskReport }),
  setIsLoading: (isLoading) => set({ isLoading }),
}));
