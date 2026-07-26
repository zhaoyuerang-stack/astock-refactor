import { create } from "zustand";

export interface AIMessage {
  role: "user" | "assistant";
  content: string;
  timestamp?: string;
}

interface AIState {
  currentPageContext: string;
  suggestedQuestions: string[];
  messages: AIMessage[];
  isLoading: boolean;

  setCurrentPageContext: (ctx: string) => void;
  setSuggestedQuestions: (questions: string[]) => void;
  addMessage: (msg: AIMessage) => void;
  setMessages: (messages: AIMessage[]) => void;
  setIsLoading: (isLoading: boolean) => void;
  clearMessages: () => void;
}

export const useAIStore = create<AIState>((set) => ({
  currentPageContext: "dashboard",
  suggestedQuestions: [],
  messages: [],
  isLoading: false,

  setCurrentPageContext: (currentPageContext) => set({ currentPageContext }),
  setSuggestedQuestions: (suggestedQuestions) => set({ suggestedQuestions }),
  addMessage: (msg) => set((state) => ({ messages: [...state.messages, msg] })),
  setMessages: (messages) => set({ messages }),
  setIsLoading: (isLoading) => set({ isLoading }),
  clearMessages: () => set({ messages: [] }),
}));
