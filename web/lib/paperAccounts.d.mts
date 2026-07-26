import type { PaperAccountsListView, PaperAccountView } from "./types";

export type PaperAccountsDisplayState = "loading" | "error" | "empty" | "ok";

export function accountsDisplayState(view: PaperAccountsListView | null | undefined): PaperAccountsDisplayState;
export function orderedAccounts(view: PaperAccountsListView | null | undefined): PaperAccountView[];

export interface PaperAccountStatusBadge {
  label: string;
  tone: "ok" | "danger" | "warn" | "neutral";
}
export function accountStatusBadge(status: string): PaperAccountStatusBadge;

export interface PaperAccountsVerdict {
  status: "ready" | "blocked" | "attention" | "neutral";
  title: string;
  detail?: string;
}
export function overallVerdict(view: PaperAccountsListView | null | undefined): PaperAccountsVerdict;
